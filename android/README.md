# Android Manager App

Native Android app (Kotlin + Jetpack Compose) for managing the Telegram Auto Poster bot.

## Features

- View status: channels, media count, schedule, timezone, paused state, next post
- List, add, pause/resume, and delete channels
- Browse the media library
- Save API URL and API key

Posting media still happens from Telegram (send/forward media to the bot) or from the scheduler.

## Setup

1. Enable the manager API in the bot `.env` (on the PC/phone running the bot):

   ```env
   API_ENABLED=true
   API_HOST=0.0.0.0
   API_PORT=8000
   API_KEY=choose-a-long-random-key
   ```

2. Restart the bot. The API listens on port 8000.

3. In the Android app (Settings tab), enter:
   - API URL: `http://<IP of the bot machine>:8000`
     - Example on your home Wi-Fi: `http://192.168.1.10:8000`
   - API key: the same value as `API_KEY`

4. Build and install:

   ```bash
   cd android
   ./gradlew assembleDebug
   ```

   Then install `app/build/outputs/apk/debug/app-debug.apk` on the phone.

## Notes

- The API is a simple JSON API (`/api/status`, `/api/channels`, `/api/media`, `/api/settings`).
- Keep `API_KEY` secret. Do not expose the API port to the public internet without HTTPS.
- On Termux, the app connects to the phone's own IP; on a PC, use the PC's LAN IP.
