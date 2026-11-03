"""HTTP API for the Flutter app. Run with: uvicorn app.main:app --reload"""

import json
import logging
import os
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, StringConstraints

from app.billing import MIN_PASSWORD_LENGTH, LoginGuard, bill_store_from_env, create_billing_router
from app.bot import (
    IMAGES_DIR,
    build_system_prompt,
    dish_names,
    load_menu,
    stream_reply,
    turn,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logger = logging.getLogger("shuvam_veg_bot")

MAX_SESSIONS = 1000
MAX_HISTORY_MESSAGES = 20
# Gemini can stall for minutes under load. Giving up early leaves time for the
# backup model, and then for an error message, before a customer gives up too.
REQUEST_TIMEOUT_MS = 20_000
NOT_CONFIGURED = "The bot isn't configured yet."
FAILED = "Something went wrong. Please try again."
BUSY = "The bot is busy. Please try again shortly."


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9-]{16,64}$")
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def create_app(client=None, bill_store=None, staff_password=None) -> FastAPI:
    bill_store = bill_store or bill_store_from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if bill_store is not None:
            try:
                await bill_store.setup()
            except Exception:
                # Bills fail with a clear error later; the menu and chat still work.
                logger.exception("Could not reach MongoDB. Check MONGODB_URI and Atlas network access.")
        yield

    app = FastAPI(title="Shuvam's Veg Restaurant Bot", lifespan=lifespan)
    # Lets the Flutter web app call the API during local development. For a
    # hosted web app, list its origins in CORS_ORIGINS (comma-separated).
    # Phone apps don't need this: CORS only applies in browsers.
    extra_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=extra_origins,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["POST", "GET"],
        allow_headers=["Content-Type", "X-Staff-Password"],
    )

    menu = load_menu()
    if menu.get("sample"):
        logger.warning(
            "app/menu.json contains SAMPLE data. Replace it with the real menu "
            'and remove "sample": true before showing this bot to customers.'
        )
    system_prompt = build_system_prompt(menu)
    known_dishes = dish_names(menu)
    # Dish photos: put files here and name them with "image" in menu.json.
    IMAGES_DIR.mkdir(exist_ok=True)
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

    api_key = os.getenv("GEMINI_API_KEY")
    gemini = client or (
        genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
        if api_key
        else None
    )
    if gemini is None:
        logger.error("GEMINI_API_KEY is not set. Add it to backend/.env and restart.")

    # History lives on the server so a client can't forge earlier bot replies.
    sessions: OrderedDict[str, list[dict]] = OrderedDict()

    async def run_chat(request: ChatRequest) -> AsyncIterator[str]:
        if gemini is None:
            yield _event({"type": "error", "message": NOT_CONFIGURED})
            return

        history = sessions.get(request.session_id, [])
        try:
            async for kind, value in stream_reply(
                gemini, system_prompt, history, request.message, known_dishes
            ):
                if kind == "delta":
                    yield _event({"type": "delta", "text": value})
                elif kind == "dishes":
                    yield _event({"type": "dishes", "names": value})
                elif value:
                    updated = [
                        *history,
                        turn("user", request.message),
                        turn("model", value),
                    ]
                    sessions[request.session_id] = updated[-MAX_HISTORY_MESSAGES:]
                    sessions.move_to_end(request.session_id)
                    while len(sessions) > MAX_SESSIONS:
                        sessions.popitem(last=False)
            yield _event({"type": "done"})
        except errors.APIError as error:
            logger.exception("Gemini API error")
            if error.code in (429, 503):
                message = BUSY
            elif error.code in (401, 403):
                message = NOT_CONFIGURED
            else:
                message = FAILED
            yield _event({"type": "error", "message": message})
        except httpx.TimeoutException:
            logger.warning("Gemini timed out on every model")
            yield _event({"type": "error", "message": BUSY})
        except Exception:
            # The app must always get a message, never a dropped stream.
            logger.exception("Chat request failed")
            yield _event({"type": "error", "message": FAILED})

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/menu")
    async def get_menu() -> dict:
        return {
            "restaurant": menu["restaurant"],
            "currency": menu["currency"],
            "categories": menu["categories"],
        }

    @app.post("/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        return StreamingResponse(run_chat(request), media_type="text/event-stream")

    password = staff_password or os.getenv("STAFF_PASSWORD")
    if not password:
        logger.error("STAFF_PASSWORD is not set, so billing is off. Add it to backend/.env.")
    elif len(password) < MIN_PASSWORD_LENGTH:
        # The API is public, so a short password could be guessed.
        logger.error(
            "STAFF_PASSWORD is shorter than %d characters, so billing is off.", MIN_PASSWORD_LENGTH
        )
        password = None
    app.include_router(create_billing_router(menu, bill_store, LoginGuard(password)))

    return app


app = create_app()
