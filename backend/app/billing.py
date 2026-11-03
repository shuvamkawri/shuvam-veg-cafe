"""Bills: GST and discount totals, storage in MongoDB, and the staff-only routes."""

import hmac
import logging
import os
import time
from collections import OrderedDict
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, StringConstraints

logger = logging.getLogger("shuvam_veg_bot")

# India has no daylight saving, so a fixed offset is exact.
IST = timezone(timedelta(hours=5, minutes=30), "IST")
BILL_PREFIX = "SV"
MAX_FAILED_LOGINS = 10
MIN_PASSWORD_LENGTH = 10
LOCKOUT_SECONDS = 15 * 60
PAISE = Decimal("0.01")

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]


class BillItemIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    qty: int = Field(ge=1, le=99)
    no_onion_garlic: bool = False


class DiscountIn(BaseModel):
    kind: Literal["percent", "flat"]
    value: Decimal = Field(ge=0, le=1_000_000, decimal_places=2)


class BillIn(BaseModel):
    items: list[BillItemIn] = Field(min_length=1, max_length=100)
    discount: DiscountIn | None = None
    customer_name: Text = ""
    customer_phone: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9+ -]{0,15}$")] = ""
    table: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = ""


class CancelIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


def _money(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


def compute_totals(
    items: list[BillItemIn],
    discount: DiscountIn | None,
    prices: dict[str, Decimal],
    gst_rate: Decimal,
    no_onion_garlic: frozenset[str] = frozenset(),
) -> dict:
    """Prices come from the menu, never the app, so a bill can't be under-charged.
    no_onion_garlic names the dishes the kitchen can make without onion and garlic."""
    quantities: dict[tuple[str, bool], int] = {}
    for item in items:
        if item.name not in prices:
            raise HTTPException(422, f"'{item.name}' is not on the menu.")
        if item.no_onion_garlic and item.name not in no_onion_garlic:
            raise HTTPException(422, f"'{item.name}' can't be made without onion and garlic.")
        key = (item.name, item.no_onion_garlic)
        quantities[key] = quantities.get(key, 0) + item.qty
    if any(qty > 99 for qty in quantities.values()):
        raise HTTPException(422, "A dish can't have more than 99 portions on one bill.")

    lines = [
        {
            "name": name,
            "no_onion_garlic": without,
            "price": prices[name],
            "qty": qty,
            "amount": _money(prices[name] * qty),
        }
        for (name, without), qty in quantities.items()
    ]
    subtotal = sum((line["amount"] for line in lines), Decimal(0))

    discount_out = None
    discount_amount = Decimal(0)
    if discount and discount.value > 0:
        if discount.kind == "percent":
            if discount.value > 100:
                raise HTTPException(422, "A discount can't be more than 100%.")
            discount_amount = _money(subtotal * discount.value / 100)
        else:
            if discount.value > subtotal:
                raise HTTPException(422, "The discount is more than the bill.")
            discount_amount = _money(discount.value)
        discount_out = {"kind": discount.kind, "value": discount.value, "amount": discount_amount}

    # GST is charged on the amount after discount, split equally into CGST and SGST.
    taxable = subtotal - discount_amount
    cgst = _money(taxable * gst_rate / 200)
    sgst = cgst
    gross = taxable + cgst + sgst
    total = gross.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return {
        "items": lines,
        "subtotal": subtotal,
        "discount": discount_out,
        "taxable": taxable,
        "gst_rate": gst_rate,
        "cgst": cgst,
        "sgst": sgst,
        "round_off": total - gross,
        "total": total,
    }


def financial_year(moment: datetime) -> str:
    """Indian financial year, April to March: 1 May 2026 -> '2627'."""
    local = moment.astimezone(IST)
    start = local.year if local.month >= 4 else local.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _to_json(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _to_json(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, list):
        return [_to_json(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(IST).isoformat()
    return value


def _to_storage(value):
    """MongoDB has no Decimal type without extra work, and these are 2 dp values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _to_storage(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_storage(item) for item in value]
    return value


class BillStore(Protocol):
    async def setup(self) -> None: ...
    async def ping(self) -> None: ...
    async def next_number(self, series: str) -> int: ...
    async def insert(self, bill: dict) -> None: ...
    async def get(self, number: str) -> dict | None: ...
    async def between(self, start: datetime, end: datetime) -> list[dict]: ...
    async def cancel(self, number: str, reason: str, when: datetime) -> dict | None: ...


class MongoBillStore:
    def __init__(self, uri: str, db_name: str):
        from pymongo import AsyncMongoClient

        self._client = AsyncMongoClient(uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
        db = self._client[db_name]
        self._bills = db["bills"]
        self._counters = db["counters"]

    async def setup(self) -> None:
        await self._bills.create_index("number", unique=True)
        await self._bills.create_index("created_at")

    async def ping(self) -> None:
        await self._client.admin.command("ping")

    async def next_number(self, series: str) -> int:
        from pymongo import ReturnDocument

        counter = await self._counters.find_one_and_update(
            {"_id": f"bill-{series}"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return counter["seq"]

    async def insert(self, bill: dict) -> None:
        await self._bills.insert_one(dict(bill))

    async def get(self, number: str) -> dict | None:
        return await self._bills.find_one({"number": number})

    async def between(self, start: datetime, end: datetime) -> list[dict]:
        cursor = self._bills.find({"created_at": {"$gte": start, "$lt": end}}).sort("created_at", -1)
        return await cursor.to_list(length=2000)

    async def cancel(self, number: str, reason: str, when: datetime) -> dict | None:
        from pymongo import ReturnDocument

        return await self._bills.find_one_and_update(
            {"number": number, "status": "paid"},
            {"$set": {"status": "cancelled", "cancelled_at": when, "cancel_reason": reason}},
            return_document=ReturnDocument.AFTER,
        )


class MemoryBillStore:
    """For tests: same behaviour as MongoBillStore, kept in memory."""

    def __init__(self):
        self.bills: dict[str, dict] = {}
        self.counters: dict[str, int] = {}

    async def setup(self) -> None:
        pass

    async def ping(self) -> None:
        pass

    async def next_number(self, series: str) -> int:
        self.counters[series] = self.counters.get(series, 0) + 1
        return self.counters[series]

    async def insert(self, bill: dict) -> None:
        self.bills[bill["number"]] = dict(bill)

    async def get(self, number: str) -> dict | None:
        return self.bills.get(number)

    async def between(self, start: datetime, end: datetime) -> list[dict]:
        found = [b for b in self.bills.values() if start <= b["created_at"] < end]
        return sorted(found, key=lambda b: b["created_at"], reverse=True)

    async def cancel(self, number: str, reason: str, when: datetime) -> dict | None:
        bill = self.bills.get(number)
        if bill is None or bill["status"] != "paid":
            return None
        bill.update(status="cancelled", cancelled_at=when, cancel_reason=reason)
        return bill


class LoginGuard:
    """Locks out an address after repeated wrong passwords. A caller can fake its
    address, so the real protection is a long random password; this only slows
    down casual guessing."""

    def __init__(self, password: str | None, clock=time.monotonic):
        self._password = password
        self._clock = clock
        self._failures: OrderedDict[str, list[float]] = OrderedDict()

    def check(self, address: str, attempt: str | None) -> None:
        if not self._password:
            raise HTTPException(503, "Billing isn't set up yet.")
        now = self._clock()
        recent = [t for t in self._failures.get(address, []) if now - t < LOCKOUT_SECONDS]
        if len(recent) >= MAX_FAILED_LOGINS:
            raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
        if attempt and hmac.compare_digest(attempt.encode(), self._password.encode()):
            self._failures.pop(address, None)
            return
        self._failures[address] = [*recent, now]
        self._failures.move_to_end(address)
        while len(self._failures) > 10_000:
            self._failures.popitem(last=False)
        raise HTTPException(401, "Wrong staff password.")


def create_billing_router(menu: dict, store: BillStore | None, guard: LoginGuard, now=None) -> APIRouter:
    now = now or (lambda: datetime.now(UTC))
    settings = menu.get("billing", {})
    gst_rate = Decimal(str(settings.get("gst_rate", 5)))
    prices = {
        dish["name"]: Decimal(str(dish["price"]))
        for category in menu["categories"]
        for dish in category["dishes"]
    }
    no_onion_garlic = frozenset(
        dish["name"]
        for category in menu["categories"]
        for dish in category["dishes"]
        if dish.get("no_onion_garlic")
    )
    business = {
        "restaurant": menu["restaurant"],
        "currency": menu["currency"],
        "gst_rate": float(gst_rate),
        "address": settings.get("address", ""),
        "phone": settings.get("phone", ""),
        "gstin": settings.get("gstin", ""),
    }

    def staff(request: Request, x_staff_password: Annotated[str | None, Header()] = None) -> BillStore:
        guard.check(request.client.host if request.client else "unknown", x_staff_password)
        if store is None:
            raise HTTPException(503, "The bill database isn't set up yet.")
        return store

    Staff = Annotated[BillStore, Depends(staff)]
    router = APIRouter(tags=["billing"])

    @router.post("/staff/login")
    async def login(_: Staff) -> dict:
        """Checks the staff password and returns the details printed on bills."""
        return business

    @router.post("/bills", status_code=201)
    async def create_bill(bill: BillIn, bills: Staff) -> dict:
        totals = compute_totals(bill.items, bill.discount, prices, gst_rate, no_onion_garlic)
        created = now()
        series = financial_year(created)
        seq = await bills.next_number(series)
        record = {
            "number": f"{BILL_PREFIX}-{series}-{seq:05d}",
            "created_at": created,
            "status": "paid",
            "customer_name": bill.customer_name,
            "customer_phone": bill.customer_phone,
            "table": bill.table,
            **_to_storage(totals),
        }
        await bills.insert(record)
        return _to_json(record)

    @router.get("/bills")
    async def list_bills(
        bills: Staff,
        day: Annotated[date | None, Query(alias="date", description="YYYY-MM-DD, India time. Default: today.")] = None,
    ) -> dict:
        day = day or now().astimezone(IST).date()
        start = datetime(day.year, day.month, day.day, tzinfo=IST)
        found = await bills.between(start, start + timedelta(days=1))
        paid = [b for b in found if b["status"] == "paid"]
        return {
            "date": day.isoformat(),
            "summary": {
                "bills": len(paid),
                "cancelled": len(found) - len(paid),
                "sales": float(sum(Decimal(str(b["total"])) for b in paid)),
                "gst": float(sum(Decimal(str(b["cgst"])) + Decimal(str(b["sgst"])) for b in paid)),
            },
            "bills": _to_json(found),
        }

    @router.get("/bills/{number}")
    async def get_bill(number: str, bills: Staff) -> dict:
        bill = await bills.get(number)
        if bill is None:
            raise HTTPException(404, "Bill not found.")
        return _to_json(bill)

    @router.post("/bills/{number}/cancel")
    async def cancel_bill(number: str, body: CancelIn, bills: Staff) -> dict:
        """Bills are cancelled, never deleted, so the numbering stays complete for GST."""
        bill = await bills.cancel(number, body.reason, now())
        if bill is None:
            if await bills.get(number) is None:
                raise HTTPException(404, "Bill not found.")
            raise HTTPException(409, "This bill is already cancelled.")
        return _to_json(bill)

    return router


def bill_store_from_env() -> BillStore | None:
    # Pasting into a hosting dashboard often adds spaces or quotes around the value.
    uri = (os.getenv("MONGODB_URI") or "").strip().strip("'\"").strip()
    if not uri:
        logger.error("MONGODB_URI is not set, so billing is off. Add it to backend/.env.")
        return None
    db_name = (os.getenv("MONGODB_DB") or "").strip().strip("'\"").strip() or "shuvam_veg_cafe"
    try:
        return MongoBillStore(uri, db_name)
    except Exception as error:
        # A bad URI must not take down the menu and chat with it.
        logger.error("MONGODB_URI is invalid, so billing is off: %s", error)
        return None
