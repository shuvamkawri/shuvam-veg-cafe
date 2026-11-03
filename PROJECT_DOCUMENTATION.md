# Shuvam's Veg Restaurant App: Project Documentation

This document explains what the project does, how its parts work together, and what every file is for. For quick setup steps, see [README.md](README.md).

## Contents

1. [What this project is](#1-what-this-project-is)
2. [How it works](#2-how-it-works)
3. [Project structure](#3-project-structure)
4. [Root files](#4-root-files)
5. [Backend files](#5-backend-files)
6. [Flutter app: files we wrote](#6-flutter-app-files-we-wrote)
7. [Flutter app: project and configuration files](#7-flutter-app-project-and-configuration-files)
8. [Flutter app: platform files](#8-flutter-app-platform-files)
9. [Backend API](#9-backend-api)
10. [Bot rules](#10-bot-rules)
11. [Settings](#11-settings)
12. [Common changes](#12-common-changes)
13. [Running the tests](#13-running-the-tests)
14. [Publishing the app](#14-publishing-the-app)
15. [Known limits](#15-known-limits)

---

## 1. What this project is

A mobile and web app for Shuvam's Veg Restaurant, made of two programs:

| Part | Folder | Technology | Job |
|---|---|---|---|
| App | `mobile_app/` | Flutter (Dart) | Shows the Home page, Menu page, dish pages and AI chat |
| Backend | `backend/` | Python, FastAPI | Serves the menu and connects the chat to Google Gemini |

**App features**

- A Home page with menu categories and customer favourites.
- A Menu page with search by dish or ingredient, and category filters.
- A details page for each dish, showing price, ingredients, spice level and allergens.
- A small AI button on every page that opens the Shuvam AI chat.
- Recommendations shown as a list of dish cards with image and price, instead of paragraphs.
- A "No onion & garlic" filter and badge for dishes the kitchen can make that way (Jain-friendly).
- Staff-only billing behind a password: GST (CGST + SGST), discounts, PDF bills to share or print, daily sales, and bill cancellation. Bills are saved in MongoDB. See the Billing section of [README.md](README.md).

**Current status:** the menu in `backend/app/menu.json` is **sample data**. The dishes, prices and allergens are invented and must be replaced before customers use the app.

---

## 2. How it works

```
 Flutter app                    Python backend                Google
 (mobile_app/)                  (backend/)                    Gemini API

 Home / Menu pages  --GET /menu-->  reads menu.json
                    <--menu JSON--

 Chat               --POST /chat--> adds the bot rules
                                    and the menu       ------> gemini-3.5-flash
                                                               (backup: gemini-
                                                               flash-lite-latest)
 Text and dish      <--streamed---  hides the dish     <------ reply
 cards appear                       marker, checks
                                    dish names
```

**The API key stays on the backend.** The app never contains it, so it can't be extracted from the APK.

**What happens when a customer asks for a recommendation**

1. The app sends the message and a random session ID to `POST /chat`.
2. The backend sends Gemini the bot rules, the full menu, the recent conversation and the new message.
3. Gemini replies with one short line, followed by a hidden marker such as `[[DISHES: Paneer Tikka | Dal Makhani]]`.
4. The backend streams the text to the app and removes the marker. It keeps only names that exist on the menu, so an invented dish can't become a card.
5. The backend sends those names to the app as a `dishes` event.
6. The app finds each dish in the menu it already loaded and shows a card with its image, price and spice level.

**If Gemini is overloaded**, the backend waits up to 20 seconds, then tries the backup model. This only happens if the customer hasn't seen any of the reply yet. If both fail, the customer sees "The bot is busy. Please try again shortly."

---

## 3. Project structure

```
shuvam_veg_bot/
├── README.md                  Quick setup steps
├── PROJECT_DOCUMENTATION.md   This document
├── .gitignore                 Keeps the API key and temporary files out of git
├── backend/                   Python server
│   ├── .env                   Your private API key (never upload)
│   ├── .env.example           Template for .env
│   ├── requirements.txt       Python packages
│   ├── app/
│   │   ├── __init__.py        Marks this folder as a Python package
│   │   ├── main.py            Web server and API endpoints
│   │   ├── bot.py             Bot rules and the Gemini call
│   │   ├── menu.json          The restaurant menu
│   │   └── images/            Dish photos
│   └── tests/
│       ├── test_chat.py       Tests with a simulated Gemini
│       └── test_live_scope.py Tests with real Gemini
└── mobile_app/                Flutter app
    ├── pubspec.yaml           App name, packages, fonts, icon settings
    ├── lib/                   The app's code
    │   ├── main.dart          App start, AI button, bottom navigation
    │   ├── api.dart           Talks to the backend, defines dish data
    │   ├── stores.dart        Holds the menu and chat conversation
    │   ├── theme.dart         Colours, fonts, category styles
    │   ├── widgets.dart       Shared parts such as dish rows and veg mark
    │   └── pages/
    │       ├── home_page.dart
    │       ├── menu_page.dart
    │       ├── dish_page.dart
    │       └── chat_sheet.dart
    ├── test/widget_test.dart  App tests
    ├── assets/                Fonts and app icon images
    ├── android/               Android project
    ├── web/                   Web project
    └── windows/               Windows project
```

These folders are created automatically and are not part of the source code. You can ignore them, and git does too: `backend/.venv/`, `__pycache__/`, `.pytest_cache/`, `mobile_app/build/`, `mobile_app/.dart_tool/`, `mobile_app/.idea/`.

---

## 4. Root files

| File | Description |
|---|---|
| `README.md` | Short guide to setting up, running and testing the project. |
| `PROJECT_DOCUMENTATION.md` | This document. |
| `.gitignore` | Tells git to skip `backend/.env`, `backend/.venv/` and Python temporary folders. This is what keeps the API key from being uploaded. |

---

## 5. Backend files

### `backend/.env`
**Private. Never upload or share this file.** Holds `GEMINI_API_KEY` and `GEMINI_MODEL`. The backend reads it when it starts. Git ignores it.

### `backend/.env.example`
A safe template showing which settings `.env` needs, with no real key. Copy it to `.env` when setting up on a new computer. Never put a real key in this file, because git does not ignore it.

### `backend/requirements.txt`
The Python packages the backend needs, with exact versions:

| Package | Used for |
|---|---|
| `google-genai` | Calling Google Gemini |
| `fastapi` | The web API |
| `uvicorn[standard]` | Running the web server |
| `python-dotenv` | Loading settings from `.env` |
| `httpx` | Recognising timeout errors, and simulating Gemini in tests |
| `pytest` | Running the tests |

### `backend/app/__init__.py`
An empty file that marks `app/` as a Python package, so `app.main` can be imported.

### `backend/app/main.py`
The web server.

| Part | What it does |
|---|---|
| `ChatRequest` | Checks incoming chats: the session ID must be 16 to 64 letters, numbers or hyphens, and the message must be 1 to 500 characters. |
| `create_app()` | Builds the server: loads the menu, warns if it is sample data, connects to Gemini and sets up the endpoints. |
| `run_chat()` | Runs one chat turn: streams events to the app, saves the conversation, and turns failures into friendly messages. |
| CORS settings | Allow the web version on `localhost` to call the API. |
| `/images` | Serves dish photos from `app/images/`. |

Key limits, set at the top of the file:

| Setting | Value | Meaning |
|---|---|---|
| `MAX_SESSIONS` | 1000 | Conversations kept in memory; the oldest are dropped |
| `MAX_HISTORY_MESSAGES` | 20 | Messages remembered per conversation |
| `REQUEST_TIMEOUT_MS` | 20000 | How long to wait for Gemini, per model |

Conversation history is stored on the server, so an app user can't fake earlier bot replies.

### `backend/app/bot.py`
The bot's brain.

| Part | What it does |
|---|---|
| `build_system_prompt()` | **The bot's rules.** Edit this to change what the bot will discuss and how it writes. |
| `OFF_TOPIC_REPLY` | The fixed reply to non-food questions. |
| `NON_VEG_REPLY` | The fixed reply to non-veg questions. |
| `stream_reply()` | Calls Gemini, streams the text, hides the dish marker, and switches to the backup model if needed. |
| `_parse_dishes()` | Reads the dish marker and keeps only real menu dishes. |
| `_is_overload()` | Decides whether an error is worth trying the backup model for. |
| `DEFAULT_MODEL`, `BACKUP_MODEL` | The Gemini models used. |
| `MAX_DISH_CARDS` | At most 5 cards per reply. |
| `load_menu()`, `dish_names()`, `turn()` | Small helpers for reading the menu and formatting conversation turns. |

### `backend/app/menu.json`
**The single source of menu data.** The app's pages and the bot both read it, so a change here updates everything after a backend restart.

```json
{
  "sample": true,
  "restaurant": "Shuvam's Veg Restaurant",
  "currency": "INR",
  "categories": [
    {
      "name": "Starters",
      "dishes": [
        {
          "name": "Paneer Tikka",
          "price": 260,
          "spice": "medium",
          "popular": true,
          "image": "paneer_tikka.jpg",
          "description": "Paneer cubes grilled in a tandoor.",
          "ingredients": ["paneer", "yogurt"],
          "allergens": ["milk"]
        }
      ]
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `sample` | No | While `true`, the server logs a warning that the menu is fake. Remove it when using the real menu. |
| `name` | Yes | The dish name. Must be unique. |
| `price` | Yes | A number, in the menu's currency. |
| `spice` | No | `none`, `mild`, `medium` or `hot`. |
| `popular` | No | `true` shows the dish under Customer favourites. |
| `image` | No | File name of a photo in `app/images/`. Without it, the dish shows a coloured tile. No dish has a photo yet. |
| `description` | No | A short description. |
| `ingredients` | No | List of ingredients, also used by the menu search. |
| `allergens` | No | List of allergens, shown to customers and repeated by the bot. **This must be accurate.** |

### `backend/app/images/`
Dish photos go here. It's currently empty, and the server creates it on startup if it's missing. Git doesn't keep empty folders, so it will appear in your repository once it contains a photo.

### `backend/tests/test_chat.py`
Tests the backend using a simulated Gemini, so they need no key and use no quota. They cover streaming, conversation memory, dish cards, hiding the marker, the backup model, error messages, photo serving and rejecting invalid requests. One test also runs the real Gemini library against a simulated server, to check the actual request format.

### `backend/tests/test_live_scope.py`
Tests the bot's real behaviour using real Gemini, so it uses your API quota. It only runs when `RUN_LIVE_TESTS=1` is set. It checks that the bot refuses off-topic and non-veg questions, answers menu and veg-food questions, resists attempts to override its rules, returns recommendations as cards, and doesn't invent opening hours. These tests can fail with "busy" errors when Google is overloaded, which doesn't mean the bot gave a wrong answer.

---

## 6. Flutter app: files we wrote

### `mobile_app/lib/main.dart`
The app's entry point and overall layout.

| Part | What it does |
|---|---|
| `ShuvamVegApp` | Starts the app, creates the shared data, and opens the chat. |
| `MaterialApp.builder` | Places the AI button above all pages, which is how it appears everywhere. |
| `AskAiButton` | The small floating AI button. |
| `HomeShell` | The bottom navigation between Home and Menu, the green band behind the phone's status bar, and the loading and error screens. |

### `mobile_app/lib/api.dart`
Everything that talks to the backend.

| Part | What it does |
|---|---|
| `apiUrl` | The backend address. Defaults to `http://localhost:8000`, and is changed at build time with `--dart-define=API_URL=...`. |
| `Dish` | One dish's data. |
| `RestaurantMenu` | The whole menu, with helpers for favourites, finding a dish by name, and formatting prices. |
| `ChatEvent` | One piece of a streamed reply: text or dish names. |
| `Api.fetchMenu()` | Loads the menu. |
| `Api.chat()` | Sends a message and streams the reply. |
| `ApiException` | A failure with a message that is safe to show customers. |

### `mobile_app/lib/stores.dart`
Holds data that several pages share.

| Part | What it does |
|---|---|
| `MenuStore` | Loads the menu and tracks loading and error states. |
| `ChatStore` | Holds the conversation, so it survives closing the chat and changing pages. |
| `ChatMessage` | One message: its text, sender, error state and dish names. |

### `mobile_app/lib/theme.dart`
The app's look: the green, cream and saffron colours (`AppColors`), fonts and text styles (`buildTheme`), each category's icon and colour (`CategoryStyle`), and the card shadow. Change this file to restyle the whole app.

### `mobile_app/lib/widgets.dart`
Parts shared between pages.

| Widget | What it shows |
|---|---|
| `VegMark` | The green square-and-dot vegetarian symbol. |
| `DishImage` | The dish's photo, or the coloured tile if it has none or the photo fails to load. |
| `DishArt` | The coloured tile with a category icon. |
| `SpiceBadge` | Flame icons and the spice level. |
| `DishTile` | One dish row in the Menu page. |
| `LoadError` | An error message with a Try again button. |
| `openDish()` | Opens a dish's page. |

### `mobile_app/lib/pages/home_page.dart`
The Home page: the green banner with restaurant name, slogan and View menu button, the category cards, the Customer favourites cards, and the "Not sure what to order?" card that opens the chat. The slogan "Pure veg food, made with love" is written here.

### `mobile_app/lib/pages/menu_page.dart`
The Menu page: the search box, category filters and dish list.

### `mobile_app/lib/pages/dish_page.dart`
One dish's details: image, category, spice level, name, price, description, ingredients and allergens, with advice to confirm allergies with staff.

### `mobile_app/lib/pages/chat_sheet.dart`
The Shuvam AI chat, which slides up from the bottom: the header, message bubbles, suggestion buttons, dish cards and message box. The suggestion buttons are listed in `_suggestions`.

### `mobile_app/test/widget_test.dart`
13 app tests using a simulated backend. They cover loading the menu, streaming replies, the Home page, category links, search, the AI button on each page, dish details, dish cards in chat, keeping the conversation after closing, and error messages.

### `mobile_app/assets/fonts/`

| File | Used for |
|---|---|
| `DMSerifDisplay-Regular.ttf` | Headings |
| `Poppins-Regular.ttf` | Normal text |
| `Poppins-Medium.ttf` | Labels and buttons |
| `Poppins-SemiBold.ttf` | Dish names and prices |

These are open-licensed fonts from Google Fonts, built into the app so they work offline.

### `mobile_app/assets/icon/`

| File | Used for |
|---|---|
| `app_icon.png` | The source of the app icon (a leaf on a plate with an AI sparkle), also shown on the Home page. |
| `app_icon_foreground.png` | The same design on a transparent background, for Android's adaptive icon. |

---

## 7. Flutter app: project and configuration files

| File | Description | Edit it? |
|---|---|---|
| `mobile_app/pubspec.yaml` | The app's name, version, packages, fonts, assets and icon settings. | Yes, to add packages or assets, or change the version |
| `mobile_app/pubspec.lock` | The exact package versions in use. | No, Flutter manages it |
| `mobile_app/analysis_options.yaml` | Code-style rules for `flutter analyze`. | Rarely |
| `mobile_app/.metadata` | Flutter's record of how the project was created. | No |
| `mobile_app/.gitignore` | Files git should skip in the Flutter project. | Rarely |
| `mobile_app/README.md` | Flutter's default short readme. | Optional |
| `mobile_app/shuvam_veg_bot.iml` | Project settings for Android Studio or IntelliJ. Ignored by git. | No |

---

## 8. Flutter app: platform files

Flutter generates these so the app can run on each platform. Most never need editing.

### Android: main files

| File | Description |
|---|---|
| `android/app/src/main/AndroidManifest.xml` | **The app's name on the phone** (`Shuvam's Veg`), its icon, and internet permission. |
| `android/app/src/debug/AndroidManifest.xml` | Debug-only settings: allows plain `http` to the local backend. Release builds need HTTPS. |
| `android/app/src/profile/AndroidManifest.xml` | Settings for profile builds, used for performance testing. |
| `android/app/build.gradle.kts` | **The package ID** (`com.shuvamveg.shuvam_veg_bot`), version and signing settings. Change the signing here for the Play Store. |
| `android/build.gradle.kts` | Build settings for the whole Android project. |
| `android/settings.gradle.kts` | Which Gradle plugins and modules the project uses. |
| `android/gradle.properties` | Gradle's options, such as memory limits. |
| `android/local.properties` | The Android SDK and Flutter locations on this computer. Ignored by git. |
| `android/.gitignore` | Android files git should skip. |
| `android/shuvam_veg_bot_android.iml` | Project settings for Android Studio. Ignored by git. |
| `android/gradlew`, `android/gradlew.bat` | Scripts that run Gradle on Mac/Linux and Windows. |
| `android/gradle/wrapper/gradle-wrapper.jar` | The program those scripts use to download Gradle. |
| `android/gradle/wrapper/gradle-wrapper.properties` | Which Gradle version to use. |
| `android/app/src/main/kotlin/com/shuvamveg/shuvam_veg_bot/MainActivity.kt` | The Android screen that hosts the Flutter app. |
| `android/app/src/main/java/io/flutter/plugins/GeneratedPluginRegistrant.java` | Generated plugin list. Ignored by git. |

### Android: `android/app/src/main/res/`

| File | Description |
|---|---|
| `mipmap-mdpi/`, `mipmap-hdpi/`, `mipmap-xhdpi/`, `mipmap-xxhdpi/`, `mipmap-xxxhdpi/` `ic_launcher.png` (5 files) | The app icon at each screen density. Generated from `assets/icon/app_icon.png`. |
| `drawable-mdpi/`, `drawable-hdpi/`, `drawable-xhdpi/`, `drawable-xxhdpi/`, `drawable-xxxhdpi/` `ic_launcher_foreground.png` (5 files) | The foreground of the adaptive icon at each density. Generated. |
| `mipmap-anydpi-v26/ic_launcher.xml` | Combines the icon's foreground and background on Android 8 and newer. Generated. |
| `values/colors.xml` | The icon's background colour. Generated. |
| `values/styles.xml` | The app's light startup theme. |
| `values-night/styles.xml` | The app's dark-mode startup theme. |
| `drawable/launch_background.xml` | The startup screen on older Android versions. |
| `drawable-v21/launch_background.xml` | The startup screen on Android 5 and newer. |

### Web: `mobile_app/web/`

| File | Description |
|---|---|
| `index.html` | The web page that loads the app, with its title and description. |
| `manifest.json` | The name, colours and icons used when the site is installed as an app. |
| `favicon.png` | The browser tab icon. |
| `icons/Icon-192.png`, `icons/Icon-512.png` | The installed web app's icon. |
| `icons/Icon-maskable-192.png`, `icons/Icon-maskable-512.png` | Versions of the icon that devices can crop to a shape. |

### Windows: `mobile_app/windows/`

| File | Description |
|---|---|
| `CMakeLists.txt` | The main build settings for the Windows app. |
| `.gitignore` | Windows files git should skip. |
| `flutter/CMakeLists.txt` | Connects the Flutter engine to the build. |
| `flutter/generated_plugins.cmake` | Generated list of plugins to build. |
| `flutter/generated_plugin_registrant.cc`, `.h` | Generated code that registers plugins. |
| `runner/CMakeLists.txt` | Build settings for the Windows program. |
| `runner/main.cpp` | Where the Windows program starts. |
| `runner/flutter_window.cpp`, `.h` | The window that shows the Flutter app. |
| `runner/win32_window.cpp`, `.h` | Basic Windows window handling. |
| `runner/utils.cpp`, `.h` | Small helpers for the console and text. |
| `runner/Runner.rc` | The program's icon and details, such as its version. |
| `runner/resource.h` | IDs used by `Runner.rc`. |
| `runner/runner.exe.manifest` | Windows settings, such as display scaling. |
| `runner/resources/app_icon.ico` | The Windows app icon. Generated from `assets/icon/app_icon.png`. |

---

## 9. Backend API

### `GET /health`
Checks that the server is running. Returns `{"status": "ok"}`.

### `GET /menu`
Returns the restaurant name, currency and categories from `menu.json`.

### `GET /images/{file}`
Returns a dish photo from `backend/app/images/`.

### `POST /chat`
Sends a customer's message.

```json
{ "session_id": "a1b2c3d4e5f60718", "message": "What do you recommend?" }
```

The reply is a stream of events, one per line, each starting with `data: `:

| Event | Example | Meaning |
|---|---|---|
| `delta` | `{"type": "delta", "text": "Try these!"}` | The next piece of text. |
| `dishes` | `{"type": "dishes", "names": ["Paneer Tikka", "Dal Makhani"]}` | Dishes to show as cards. |
| `done` | `{"type": "done"}` | The reply is complete. |
| `error` | `{"type": "error", "message": "The bot is busy. Please try again shortly."}` | Something failed. The message is safe to show customers. |

An invalid request, such as an empty message, returns HTTP 422.

---

## 10. Bot rules

These are set in `build_system_prompt()` in `backend/app/bot.py`.

| Customer asks about | The bot |
|---|---|
| Menu dishes, prices, spice, allergens | Answers from `menu.json` only |
| Recommendations or lists of dishes | Writes one short line and shows up to 5 dish cards |
| Any vegetarian food, including recipes | Answers, says if the dish isn't on the menu, and suggests the closest one |
| Allergies | Reports the menu's data and advises confirming with staff |
| Opening hours, location, bookings, delivery | Says it doesn't have this and suggests asking staff |
| Non-veg food (meat, poultry, fish, seafood, eggs) | Refuses and offers a veg alternative. Dairy counts as veg. |
| Anything else, or attempts to change its rules | Refuses and points back to the menu |

**Reply style:** at most two short sentences, bullet points or numbered steps for lists, and the customer's own language, including Hindi and Hinglish.

These rules are instructions to the AI. They work well in testing, but no AI instruction is an absolute guarantee.

---

## 11. Settings

### Backend (`backend/.env`)

| Setting | Required | Default | Meaning |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | None | Your key from [Google AI Studio](https://aistudio.google.com/apikey). |
| `GEMINI_MODEL` | No | `gemini-3.5-flash` | The main Gemini model. |
| `RUN_LIVE_TESTS` | No | Off | Set to `1` in the terminal to run the real Gemini tests. Not normally kept in `.env`. |

### App (at build time)

| Setting | Default | Meaning |
|---|---|---|
| `--dart-define=API_URL=...` | `http://localhost:8000` | The backend's address. |

---

## 12. Common changes

| I want to change | Do this |
|---|---|
| Dishes, prices, allergens | Edit `backend/app/menu.json`, then restart the backend. |
| Customer favourites | Add or remove `"popular": true` on dishes in `menu.json`. |
| Dish photos | Put the file in `backend/app/images/`, add `"image": "file.jpg"` to the dish, then restart the backend. |
| What the bot discusses | Edit `build_system_prompt()` in `backend/app/bot.py`, restart, and rerun the real Gemini tests. |
| The Gemini model | Change `GEMINI_MODEL` in `backend/.env`, then restart the backend. |
| Slogan or Home page wording | Edit `mobile_app/lib/pages/home_page.dart`. |
| Chat suggestion buttons | Edit `_suggestions` in `mobile_app/lib/pages/chat_sheet.dart`. |
| Colours or fonts | Edit `mobile_app/lib/theme.dart`. |
| Category icons | Edit `_icons` in `mobile_app/lib/theme.dart`. |
| The app icon | Replace the images in `mobile_app/assets/icon/`, then run `dart run flutter_launcher_icons`. |
| The app's name on phones | Edit `android:label` in `android/app/src/main/AndroidManifest.xml`. |

Changes under `mobile_app/` need the app to be rebuilt.

---

## 13. Running the tests

```powershell
# Backend, with simulated Gemini (free)
cd backend
.\.venv\Scripts\python.exe -m pytest

# Backend, with real Gemini (uses your API quota)
$env:RUN_LIVE_TESTS = "1"; .\.venv\Scripts\python.exe -m pytest tests/test_live_scope.py

# App
cd ..\mobile_app
flutter analyze
flutter test
```

---

## 14. Publishing the app

**Before you start:** create a new Gemini API key, replace the sample menu, remove `"sample": true`, and add rate limiting.

**1. Upload the code** to a private GitHub repository. Check that `git status` doesn't list `backend/.env` before you commit.

**2. Host the backend**, for example on [Render](https://render.com), as a Web Service with these settings:

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Environment variables | `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.5-flash`, `PYTHON_VERSION=3.14.7` |

**3. Build the app** with your real backend address:

```powershell
cd mobile_app
flutter build apk --release --dart-define=API_URL=https://your-backend-address
```

**4. Test and share** `mobile_app/build/app/outputs/flutter-apk/app-release.apk`. The Play Store also needs your own signing key, set in `android/app/build.gradle.kts`.

---

## 15. Known limits

| Limit | What it means | How to fix it |
|---|---|---|
| Sample menu | The dishes, prices and allergens are invented. | Replace `menu.json`. |
| No dish photos | Dishes show coloured tiles with icons. | Add real photos. |
| No rate limiting | Anyone with the backend address could use up your Gemini quota. | Add rate limiting before sharing widely. |
| Gemini free tier | Replies can be slow or "busy" when Google is overloaded, and free-tier chats may be used to improve Google's products. | Consider a paid Gemini plan. Check Google's current terms. |
| Memory-only conversations | Conversations are lost when the backend restarts, and won't work across several server processes. | Store them in a database. |
| No ordering or payment | Customers can browse and ask questions, but can't order. | Would be a new feature. |
| Restaurant details | The app has no address, phone number or opening hours, and the bot says to ask staff. | Add the real details to the app and the bot's data. |
| Debug signing | Release APKs use the debug key, which is fine for direct sharing but not the Play Store. | Create a signing key. |
| iPhone | No iOS project was created. An APK only works on Android. | Run `flutter create --platforms=ios .` in `mobile_app/` and build on a Mac, or host the web version. |
| Web version | The backend only allows the web app from `localhost`. | Add your site to the `CORS_ORIGINS` setting (comma-separated). |
