# Shuvam's Veg Restaurant Bot

A Flutter restaurant app with Home, Menu and dish pages, plus an AI button on every page. It's backed by a Python (FastAPI) server that calls Google Gemini.

The bot, Shuvam AI, answers questions about the restaurant's menu in `backend/app/menu.json` and about vegetarian food in general. It refuses non-veg food (meat, poultry, fish, seafood and eggs) and anything unrelated to food.

```
Flutter app (mobile_app/)  --GET /menu, POST /chat-->  FastAPI (backend/)  -->  Gemini API
```

The API key stays on the server and never ships inside the app.

For a full explanation of how the project works and what every file does, see [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md).

## Setup

**1. Add your API key.** Get a free key at https://aistudio.google.com/apikey and paste it after `GEMINI_API_KEY=` in `backend/.env`. If that file doesn't exist, copy it from `backend/.env.example`.

**2. Start the backend** (it uses the virtual environment in `backend/.venv`):

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

**3. Run the app** in a second terminal:

```powershell
cd mobile_app
flutter run -d chrome
```

For an Android phone connected over USB, forward the backend port first:

```powershell
adb reverse tcp:8000 tcp:8000
flutter run -d 7a5739b9
```

## Put in your real menu

Edit `backend/app/menu.json`, remove the `"sample": true` line, and restart the backend. The app's pages and the bot both read this file. The server logs a warning until that line is gone. Allergen data matters here, because the app and the bot show it to customers. Add `"popular": true` to a dish to show it under Customer favourites.

To add a dish photo, put the file in `backend/app/images/` and add its name to the dish, for example `"image": "paneer_tikka.jpg"`. Dishes without a photo show a coloured tile.

The home page wording is in `mobile_app/lib/pages/home_page.dart`. To change the app icon, replace the images in `mobile_app/assets/icon/` and run `dart run flutter_launcher_icons`.

## Change what the bot will discuss

The rules are in `build_system_prompt` in `backend/app/bot.py`.

## Tests

```powershell
# Backend, using a fake Gemini client (uses no quota)
cd backend
.\.venv\Scripts\python.exe -m pytest

# Backend, checking the real model stays on topic (uses API quota)
$env:RUN_LIVE_TESTS = "1"; .\.venv\Scripts\python.exe -m pytest tests/test_live_scope.py

# Flutter
cd ..\mobile_app
flutter test
```

## Before going live

- Check Google's current free-tier limits and terms. Free-tier chats may be used to improve Google's products, so a paid tier may suit real customers better.
- Host the backend on HTTPS and build with `--dart-define=API_URL=https://your-server`.
- Add rate limiting, so nobody can use up your Gemini quota through the public `/chat` endpoint.
- Add your site to `allow_origin_regex` in `backend/app/main.py` if you deploy the web version.
- Conversation history is kept in server memory: it is lost on restart and doesn't work across multiple server processes.
