"""Billing API tests, using an in-memory store instead of MongoDB."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.billing import (
    BillItemIn,
    DiscountIn,
    LoginGuard,
    MemoryBillStore,
    compute_totals,
    financial_year,
)
from app.main import create_app

PASSWORD = "test-staff-pass"
STAFF = {"X-Staff-Password": PASSWORD}
PRICES = {"Paneer Tikka": Decimal(260), "Masala Dosa": Decimal(170)}


@pytest.fixture
def api():
    return TestClient(create_app(client=object(), bill_store=MemoryBillStore(), staff_password=PASSWORD))


def new_bill(api, **body):
    body.setdefault("items", [{"name": "Paneer Tikka", "qty": 2}, {"name": "Masala Dosa", "qty": 1}])
    return api.post("/bills", json=body, headers=STAFF)


def test_totals_with_gst():
    totals = compute_totals([BillItemIn(name="Paneer Tikka", qty=2)], None, PRICES, Decimal(5))
    assert totals["subtotal"] == 520
    assert totals["cgst"] == totals["sgst"] == Decimal("13.00")
    assert totals["total"] == 546
    assert totals["round_off"] == 0


def test_gst_is_charged_after_discount_and_total_is_rounded():
    totals = compute_totals(
        [BillItemIn(name="Masala Dosa", qty=1)],
        DiscountIn(kind="percent", value=Decimal(10)),
        PRICES,
        Decimal(5),
    )
    assert totals["discount"]["amount"] == Decimal("17.00")
    assert totals["taxable"] == Decimal("153.00")
    assert totals["cgst"] == Decimal("3.83")  # 153 x 2.5%, rounded half up
    assert totals["total"] == 161  # 160.66 rounded to the nearest rupee
    assert totals["round_off"] == Decimal("0.34")


def test_same_dish_twice_is_merged():
    items = [BillItemIn(name="Masala Dosa", qty=1), BillItemIn(name="Masala Dosa", qty=2)]
    totals = compute_totals(items, None, PRICES, Decimal(5))
    assert [(line["name"], line["qty"]) for line in totals["items"]] == [("Masala Dosa", 3)]


@pytest.mark.parametrize(
    "discount, message",
    [
        (DiscountIn(kind="percent", value=Decimal(101)), "100%"),
        (DiscountIn(kind="flat", value=Decimal(500)), "more than the bill"),
    ],
)
def test_bad_discounts_are_rejected(discount, message):
    with pytest.raises(HTTPException, match=message):
        compute_totals([BillItemIn(name="Masala Dosa", qty=1)], discount, PRICES, Decimal(5))


def test_financial_year_starts_in_april():
    assert financial_year(datetime(2026, 3, 31, 12, tzinfo=UTC)) == "2526"
    assert financial_year(datetime(2026, 4, 1, 12, tzinfo=UTC)) == "2627"


def test_create_bill_uses_menu_prices_and_numbers_bills(api):
    first = new_bill(api, customer_name="Asha", table="T4")
    assert first.status_code == 201
    bill = first.json()
    assert bill["items"][0] == {
        "name": "Paneer Tikka",
        "no_onion_garlic": False,
        "price": 260.0,
        "qty": 2,
        "amount": 520.0,
    }
    assert bill["subtotal"] == 690
    assert bill["total"] == 725  # 690 + 34.50 GST = 724.50, rounded up
    assert bill["status"] == "paid"
    assert bill["customer_name"] == "Asha"

    second = new_bill(api).json()
    prefix, series, seq = second["number"].split("-")
    assert (prefix, seq) == ("SV", "00002")
    assert bill["number"] == f"SV-{series}-00001"


def test_no_onion_garlic_is_a_separate_line():
    items = [
        BillItemIn(name="Masala Dosa", qty=2),
        BillItemIn(name="Masala Dosa", qty=1, no_onion_garlic=True),
    ]
    totals = compute_totals(items, None, PRICES, Decimal(5), frozenset({"Masala Dosa"}))
    assert [(line["qty"], line["no_onion_garlic"]) for line in totals["items"]] == [(2, False), (1, True)]
    assert totals["subtotal"] == 510  # same price either way


def test_no_onion_garlic_only_for_dishes_that_allow_it(api):
    ok = new_bill(api, items=[{"name": "Masala Dosa", "qty": 1, "no_onion_garlic": True}])
    assert ok.status_code == 201
    assert ok.json()["items"][0]["no_onion_garlic"] is True
    response = new_bill(api, items=[{"name": "Veg Manchurian", "qty": 1, "no_onion_garlic": True}])
    assert response.status_code == 422
    assert "without onion and garlic" in response.json()["detail"]


def test_unknown_dish_is_rejected(api):
    response = new_bill(api, items=[{"name": "Chicken Curry", "qty": 1}])
    assert response.status_code == 422
    assert "not on the menu" in response.json()["detail"]


@pytest.mark.parametrize("headers", [{}, {"X-Staff-Password": "wrong"}])
def test_billing_needs_the_staff_password(api, headers):
    assert api.get("/bills", headers=headers).status_code == 401
    assert api.post("/staff/login", headers=headers).status_code == 401


def test_login_returns_bill_details(api):
    response = api.post("/staff/login", headers=STAFF)
    assert response.status_code == 200
    assert response.json()["gst_rate"] == 5


def test_billing_is_off_without_a_password(monkeypatch):
    monkeypatch.delenv("STAFF_PASSWORD", raising=False)
    api = TestClient(create_app(client=object(), bill_store=MemoryBillStore(), staff_password=None))
    assert api.post("/staff/login", headers=STAFF).status_code == 503


def test_billing_is_off_with_a_short_password():
    api = TestClient(create_app(client=object(), bill_store=MemoryBillStore(), staff_password="1234"))
    assert api.post("/staff/login", headers={"X-Staff-Password": "1234"}).status_code == 503


def test_repeated_wrong_passwords_lock_out():
    clock = [0.0]
    guard = LoginGuard("secret-pass", clock=lambda: clock[0])
    for _ in range(10):
        with pytest.raises(HTTPException) as wrong:
            guard.check("1.2.3.4", "nope")
        assert wrong.value.status_code == 401
    with pytest.raises(HTTPException) as locked:
        guard.check("1.2.3.4", "secret-pass")
    assert locked.value.status_code == 429
    guard.check("5.6.7.8", "secret-pass")  # other addresses are unaffected
    clock[0] += 15 * 60
    guard.check("1.2.3.4", "secret-pass")


def test_list_and_cancel(api):
    kept = new_bill(api).json()
    cancelled = new_bill(api).json()

    response = api.post(f"/bills/{cancelled['number']}/cancel", json={"reason": "Wrong table"}, headers=STAFF)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    again = api.post(f"/bills/{cancelled['number']}/cancel", json={}, headers=STAFF)
    assert again.status_code == 409

    day = api.get("/bills", headers=STAFF).json()
    assert [b["number"] for b in day["bills"]] == [cancelled["number"], kept["number"]]
    assert day["summary"] == {"bills": 1, "cancelled": 1, "sales": 725.0, "gst": 34.5}

    assert api.get(f"/bills/{kept['number']}", headers=STAFF).json()["total"] == 725
    assert api.get("/bills/SV-0000-99999", headers=STAFF).status_code == 404
    assert api.get("/bills?date=2001-01-01", headers=STAFF).json()["bills"] == []


def test_bad_mongodb_uri_turns_off_billing_but_not_the_app(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "https://cluster0.example.mongodb.net/")
    api = TestClient(create_app(client=object(), staff_password=PASSWORD))
    assert api.get("/health").status_code == 200
    assert api.post("/staff/login", headers=STAFF).status_code == 503


def test_quoted_mongodb_uri_is_accepted(monkeypatch):
    from app.billing import MongoBillStore, bill_store_from_env

    monkeypatch.setenv("MONGODB_URI", ' "mongodb://localhost:27017" ')
    assert isinstance(bill_store_from_env(), MongoBillStore)


def test_home_opens_the_api_docs(api):
    response = api.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_database_errors_get_a_clear_message():
    from pymongo.errors import ServerSelectionTimeoutError

    class DownStore(MemoryBillStore):
        async def next_number(self, series):
            raise ServerSelectionTimeoutError("SSL handshake failed")

    api = TestClient(create_app(client=object(), bill_store=DownStore(), staff_password=PASSWORD))
    response = new_bill(api)
    assert response.status_code == 503
    assert "bill database" in response.json()["detail"]
