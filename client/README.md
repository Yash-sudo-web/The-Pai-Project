# PAI Client — Windows & iOS App

Flutter desktop/mobile client for the Personal AI Assistant. Handles voice input (STT) and voice responses (TTS) locally — no Whisper or heavy deps needed on the server.

## Features
- 🎤 **Push-to-talk voice input** — Windows SAPI / Apple Speech
- 🔊 **Voice responses** — system TTS (toggle in sidebar)
- 💬 **Persistent chat** — loads today's history from the backend
- ✅ **Confirmation flow** — approve/reject sensitive actions in-app
- ⚙️ **Settings dialog** — enter backend URL + API key, stored securely in SharedPreferences

## Prerequisites

### Windows
1. Install Flutter SDK: https://docs.flutter.dev/get-started/install/windows/desktop
2. Install **Visual Studio 2022** with the **Desktop development with C++** workload
3. Verify: `flutter doctor`

### iOS / macOS (do on your MacBook)
```bash
brew install --cask flutter
brew install cocoapods
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
flutter doctor
```

## Quick Start

```bash
# 1. Scaffold platform boilerplate (first time only)
cd client
flutter create . --platforms=windows,ios,macos --org com.pai

# 2. Install packages
flutter pub get

# 3. Run on Windows
flutter run -d windows

# 4. Run on iOS (from MacBook, with device plugged in)
flutter run -d <device-id>
```

## Configuration

On first launch the Settings dialog appears automatically. Enter:
- **Backend URL**: your Vercel deployment URL, e.g. `https://the-pai-project.vercel.app`
- **API Key**: the value of `PAI_API_KEY` from your Vercel env vars

These are saved locally in the app's SharedPreferences — no `.env` file needed.

Alternatively, pass them at build time:
```bash
flutter run -d windows \
  --dart-define=PAI_BASE_URL=https://the-pai-project.vercel.app \
  --dart-define=PAI_API_KEY=your_key
```

## Build Release (Windows)

```bash
flutter build windows --release
# Executable: build/windows/x64/runner/Release/pai_client.exe
```

## Project Structure

```
client/
├── lib/
│   ├── main.dart              # Entry point
│   ├── app.dart               # Theme + MaterialApp
│   ├── config.dart            # API URL/key resolution
│   ├── models/
│   │   └── chat_message.dart
│   ├── services/
│   │   ├── api_service.dart   # HTTP client
│   │   ├── stt_service.dart   # Speech-to-text
│   │   └── tts_service.dart   # Text-to-speech
│   ├── providers/
│   │   └── chat_provider.dart # State management
│   └── ui/
│       ├── screens/
│       │   ├── chat_screen.dart
│       │   └── settings_screen.dart
│       └── widgets/
│           ├── sidebar.dart
│           ├── message_bubble.dart
│           ├── voice_button.dart
│           └── thinking_indicator.dart
└── pubspec.yaml
```
