"""API tests using a fake Gemini client, so they need no key and use no quota."""

import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from google import genai
from google.genai import errors, types

from app import main
from app.bot import (
    BACKUP_MODEL,
    DEFAULT_MODEL,
    IMAGES_DIR,
    NON_VEG_REPLY,
    OFF_TOPIC_REPLY,
    turn,
)
from app.main import create_app

SESSION = "test-session-0001"
STOP = types.FinishReason.STOP


def chunk(text, finish_reason=None):
    return SimpleNamespace(
        text=text, candidates=[SimpleNamespace(finish_reason=finish_reason)]
    )


class FakeClient:
    def __init__(self, chunks=None, error=None):
        self.calls = []
        chunks = chunks or [chunk("Paneer "), chunk("Tikka", STOP)]

        async def generate_content_stream(**kwargs):
            self.calls.append(kwargs)
            if error:
                raise error

            async def generate():
                for item in chunks:
                    yield item

            return generate()

        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=generate_content_stream)
        )


def post(api, message="What is Paneer Tikka?", session=SESSION):
    response = api.post("/chat", json={"session_id": session, "message": message})
    assert response.status_code == 200
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line
    ]


def test_streams_reply_then_done():
    assert post(TestClient(create_app(FakeClient()))) == [
        {"type": "delta", "text": "Paneer "},
        {"type": "delta", "text": "Tikka"},
        {"type": "done"},
    ]


def test_every_request_carries_menu_and_scope_rules_as_system_instruction():
    client = FakeClient()
    post(TestClient(create_app(client)))
    system = client.calls[0]["config"].system_instruction
    assert "Dal Makhani" in system
    assert OFF_TOPIC_REPLY in system
    assert NON_VEG_REPLY in system


def test_menu_endpoint_returns_categories_without_internal_flags():
    menu = TestClient(create_app(FakeClient())).get("/menu").json()
    assert menu["restaurant"] == "Shuvam's Veg Restaurant"
    assert menu["categories"][0]["dishes"][0]["name"] == "Paneer Tikka"
    assert "sample" not in menu


def test_dish_marker_becomes_cards_and_never_appears_as_text():
    # The marker is deliberately split across chunks, as streaming can do.
    client = FakeClient(
        chunks=[
            chunk("Try these!\n["),
            chunk("[DISHES: paneer tikka | Dal"),
            chunk(" Makhani | Invented Dish]]", STOP),
        ]
    )
    api = TestClient(create_app(client))
    assert post(api) == [
        {"type": "delta", "text": "Try these!\n"},
        {"type": "dishes", "names": ["Paneer Tikka", "Dal Makhani"]},
        {"type": "done"},
    ]
    # The next turn still sees the marker, so the model keeps using the format.
    post(api, "next")
    assert "[[DISHES:" in client.calls[1]["contents"][1]["parts"][0]["text"]


def test_text_with_a_lone_bracket_is_not_lost():
    client = FakeClient(chunks=[chunk("See list ["), chunk("1]", STOP)])
    events = post(TestClient(create_app(client)))
    assert "".join(e.get("text", "") for e in events) == "See list [1]"


def test_marker_with_no_real_dishes_and_no_text_shows_redirect():
    client = FakeClient(chunks=[chunk("[[DISHES: Butter Chicken]]", STOP)])
    assert post(TestClient(create_app(client))) == [
        {"type": "delta", "text": OFF_TOPIC_REPLY},
        {"type": "done"},
    ]


def test_dish_photos_are_served_from_the_images_folder():
    photo = IMAGES_DIR / "_test_photo.txt"
    IMAGES_DIR.mkdir(exist_ok=True)
    photo.write_text("photo")
    try:
        response = TestClient(create_app(FakeClient())).get(f"/images/{photo.name}")
        assert response.status_code == 200
        assert response.text == "photo"
    finally:
        photo.unlink()


def test_second_turn_includes_first_turn_and_sessions_are_separate():
    client = FakeClient()
    api = TestClient(create_app(client))
    post(api, "first")
    post(api, "second")
    post(api, "other", session="test-session-0002")
    assert client.calls[1]["contents"] == [
        turn("user", "first"),
        turn("model", "Paneer Tikka"),
        turn("user", "second"),
    ]
    assert client.calls[2]["contents"] == [turn("user", "other")]


def test_history_is_capped_and_starts_with_a_user_turn():
    client = FakeClient()
    api = TestClient(create_app(client))
    for i in range(15):
        post(api, f"message {i}")
    contents = client.calls[-1]["contents"]
    assert len(contents) == 21
    assert contents[0] == turn("user", "message 4")


def test_blocked_reply_shows_redirect_and_is_not_remembered():
    client = FakeClient(chunks=[chunk(None, types.FinishReason.SAFETY)])
    api = TestClient(create_app(client))
    assert post(api) == [{"type": "delta", "text": OFF_TOPIC_REPLY}, {"type": "done"}]
    post(api, "next")
    assert client.calls[1]["contents"] == [turn("user", "next")]


def test_reply_blocked_midway_adds_redirect_and_is_not_remembered():
    client = FakeClient(chunks=[chunk("Par"), chunk(None, types.FinishReason.SAFETY)])
    api = TestClient(create_app(client))
    assert post(api) == [
        {"type": "delta", "text": "Par"},
        {"type": "delta", "text": f"\n\n{OFF_TOPIC_REPLY}"},
        {"type": "done"},
    ]
    post(api, "next")
    assert client.calls[1]["contents"] == [turn("user", "next")]


def api_error(code):
    return errors.ClientError(code, {"error": {"message": "secret detail"}})


class FlakyClient:
    """Fails on the first model with the given error, then answers."""

    def __init__(self, error):
        self.models = []

        async def generate_content_stream(**kwargs):
            self.models.append(kwargs["model"])
            if len(self.models) == 1:
                raise error

            async def generate():
                yield chunk("Dal Makhani", STOP)

            return generate()

        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=generate_content_stream)
        )


def test_overloaded_or_slow_model_falls_back_to_backup(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    for error in (
        errors.ServerError(503, {"error": {"message": "overloaded"}}),
        api_error(429),
        httpx.ReadTimeout("slow"),
    ):
        client = FlakyClient(error)
        events = post(TestClient(create_app(client)))
        assert events == [{"type": "delta", "text": "Dal Makhani"}, {"type": "done"}]
        assert client.models == [DEFAULT_MODEL, BACKUP_MODEL]


def test_other_errors_do_not_try_the_backup():
    client = FlakyClient(api_error(403))
    events = post(TestClient(create_app(client)))
    assert events == [{"type": "error", "message": "The bot isn't configured yet."}]
    assert len(client.models) == 1


def test_timeout_on_every_model_says_busy():
    events = post(TestClient(create_app(FakeClient(error=httpx.ReadTimeout("slow")))))
    assert events == [{"type": "error", "message": "The bot is busy. Please try again shortly."}]


def test_rate_limit_and_bad_key_send_friendly_error_without_internal_details():
    for code, message in (
        (429, "The bot is busy. Please try again shortly."),
        (503, "The bot is busy. Please try again shortly."),
        (403, "The bot isn't configured yet."),
        (400, "Something went wrong. Please try again."),
    ):
        events = post(TestClient(create_app(FakeClient(error=api_error(code)))))
        assert events == [{"type": "error", "message": message}]


def test_unexpected_failure_sends_error_event_instead_of_dropping_the_stream():
    events = post(TestClient(create_app(FakeClient(error=OSError("secret")))))
    assert events == [{"type": "error", "message": "Something went wrong. Please try again."}]


def test_missing_api_key_starts_server_and_reports_not_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    events = post(TestClient(main.create_app()))
    assert events == [{"type": "error", "message": "The bot isn't configured yet."}]


def test_real_sdk_sends_expected_request_and_parses_streamed_reply(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    captured = {}

    def sse(text, **candidate):
        content = {"role": "model", "parts": [{"text": text}]}
        return f"data: {json.dumps({'candidates': [{'content': content, **candidate}]})}\n\n"

    def handler(request):
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-goog-api-key")
        captured["body"] = json.loads(request.content)
        body = sse("Dal ") + sse("₹260", finishReason="STOP")
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body.encode()
        )

    client = genai.Client(
        api_key="test-key",
        http_options=types.HttpOptions(
            async_client_args={"transport": httpx.MockTransport(handler)}
        ),
    )
    events = post(TestClient(create_app(client)))

    assert events == [
        {"type": "delta", "text": "Dal "},
        {"type": "delta", "text": "₹260"},
        {"type": "done"},
    ]
    assert "gemini-3.5-flash:streamGenerateContent" in captured["url"]
    assert captured["key"] == "test-key"
    assert "Dal Makhani" in captured["body"]["systemInstruction"]["parts"][0]["text"]
    assert captured["body"]["contents"] == [turn("user", "What is Paneer Tikka?")]


def test_rejects_invalid_requests_before_calling_gemini():
    client = FakeClient()
    api = TestClient(create_app(client))
    for body in (
        {"session_id": SESSION, "message": "   "},
        {"session_id": SESSION, "message": "x" * 501},
        {"session_id": "short", "message": "hi"},
    ):
        assert api.post("/chat", json=body).status_code == 422
    assert client.calls == []
