# Shuvam's Veg Restaurant Bot

A Flutter restaurant app with Home, Menu and dish pages, plus an AI button on every page. It's backed by a Python (FastAPI) server that calls Google Gemini.

The bot, Shuvam AI, answers questions about the restaurant's menu in `backend/app/menu.json` and about vegetarian food in general. It refuses non-veg food (meat, poultry, fish, seafood and eggs) and anything unrelated to food.

```
Flutter app (mobile_app/)  --GET /menu, POST /chat-->  FastAPI (backend/)  -->  Gemini API
                           --/staff/login, /bills----->                    -->  MongoDB (bills)
```

The API key stays on the server and never ships inside the app.

For a full explanation of how the project works and what every file does, see [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md).

## Setup

**1. Add your settings.** Copy `backend/.env.example` to `backend/.env` if it doesn't exist, then fill in:

- `GEMINI_API_KEY`: a free key from https://aistudio.google.com/apikey
- `MONGODB_URI`: your MongoDB Atlas connection string, where bills are saved
- `STAFF_PASSWORD`: the password staff type to open Billing, at least 10 characters

`backend/.env` is never committed, because the GitHub repo is public.

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

## No onion & garlic dishes

Add `"no_onion_garlic": true` to any dish the kitchen can make without onion and garlic (Jain-friendly). The app then:

- shows "Available without onion & garlic" on that dish, with a filter on the Menu page
- lets the bot recommend only those dishes to customers who avoid onion or garlic
- lets staff bill the dish as "No onion & garlic", which prints on the bill. The server refuses this for dishes without the flag.

## Billing (staff only)

Tap the till icon at the top of the Home page and enter the staff password.

- **New bill:** tap ADD on dishes. Turn on the "No onion & garlic" chip to add dishes made that way. Then tap Review bill, add an optional customer name, phone, table and discount (% or ₹), and tap Generate bill.
- **Bill:** Share PDF (WhatsApp, email and so on) or Print. Cancel bill keeps the bill marked cancelled and leaves it out of sales, so bill numbers never have gaps.
- **Bills:** each day's bills, total sales and GST collected.

Bill numbers run per Indian financial year: `SV-2627-00001` is the first bill from April 2026 to March 2027. GST is charged on the amount after discount, split equally into CGST and SGST, and the total is rounded to the nearest rupee. Prices always come from the menu on the server.

Set the GST rate and the details printed on bills in the `billing` section of `backend/app/menu.json`. The bill says TAX INVOICE when `gstin` is filled in. Only charge GST if the restaurant is GST-registered.

The billing API is in `backend/app/billing.py`, and the screens are in `mobile_app/lib/billing/`.

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
- Add your web app's address to `CORS_ORIGINS` (comma-separated) if you deploy the web version.
- In MongoDB Atlas > Network Access, allow `0.0.0.0/0`, because Render's free servers have no fixed IP address.
- Conversation history is kept in server memory: it is lost on restart and doesn't work across multiple server processes.
