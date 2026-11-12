# Resona app (Capacitor prototype)

Browsers stop the microphone when the phone is locked or the app goes to the
background (WebKit bugs 226620 / 204681; Chrome on Android behaves the same).
A web app cannot work around that. The Resona app is a [Capacitor](https://capacitorjs.com)
shell around the same Nuxt SPA (`apps/resona-pwa`) whose recordings run in a
native recorder, so a recording keeps going while the screen is locked.

Status: **prototype, Android first.** iOS is prepared but not built or signed.

## How it works

- The app ships the static SPA build (`nuxt generate` → `.output/public`) as its
  web assets. Without a service worker: `RESONA_CAPACITOR=1` disables `@vite-pwa/nuxt`.
- Every recording flow asks `useRecorder()` (`app/composables/useRecorder.ts`) for a
  `Recorder`. Inside the app that is `useNativeRecorder()`
  (`@capgo/capacitor-audio-recorder`, MPEG-4 AAC, `audio/mp4`); in the browser it is
  `useWebRecorder()` (MediaRecorder). The upload → hub job flow behind it is the same:
  new recordings, push-to-talk on a recording, re-dictation and the `/debug` test dictation.
- Android: on record, the app starts a foreground service of type `microphone`
  (`@capawesome-team/capacitor-android-foreground-service`) with the persistent
  notification "Resona nimmt auf", then starts the recorder. The service keeps the
  process alive, holds a partial wake lock and lets the app keep the microphone
  while locked. It stops when the recording stops. If the bridge reports that the
  service cannot start, the app refuses to record ("Hintergrundaufnahme konnte nicht
  gestartet werden."). The plugin resolves once it has asked Android for the service;
  a failure inside the service itself (`startForeground`) is only logged natively.
- Elapsed time is computed from the wall clock, so the recording sheet shows the
  right length after unlocking (web view timers stall while locked).
- Sharing a file from another app (Android share sheet → Resona) goes through a
  `SEND` intent filter and `@capgo/capacitor-share-target` into the same share page as
  the web share target (`/share-target`); see the Findings row "Share target".
- On stop the app reads the recorded file back into the web view
  (`Capacitor.convertFileSrc` + `fetch`) and deletes it from the app cache; a
  file it cannot read stays in the cache.

## Build the debug APK

Prerequisites: bun, JDK 21, Android SDK with platform 36 and build-tools.
On macOS with Homebrew:

```bash
brew install openjdk@21
brew install --cask android-commandlinetools
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0"
```

Build (the `NUXT_PUBLIC_*` values are baked into the bundle at build time):

```bash
cd apps/resona-pwa
bun install --frozen-lockfile
NUXT_PUBLIC_DATA_MODE=standalone \
NUXT_PUBLIC_RESONA_API_URL=https://<hub-host> \
  bun run cap:apk
# → android/app/build/outputs/apk/debug/app-debug.apk
```

`cap:apk` = `cap:build` (`RESONA_CAPACITOR=1 nuxt generate && cap sync`) + `./gradlew
assembleDebug` in `android/`. The native projects (`android/`, `ios/`) are committed;
`cap sync` copies the web build and plugin list into them (both are gitignored).

The hub must allow the app's origin in `CORS_ORIGINS`: `https://localhost` on Android
(`capacitor://localhost` on iOS). The bearer token still guards every call; CORS only
lets the web view read the responses.

## Device test (Android)

Before: the hub has an online, approved worker and a promoted release for the
organization's default execution profile (otherwise the job waits in the queue,
`queue_reason` in `resona jobs get`).

1. Install: `adb install -r android/app/build/outputs/apk/debug/app-debug.apk`, or copy
   the APK to the phone and open it (allow "install unknown apps" for the file manager).
2. Open Resona → Einstellungen → Verbindung: enter the hub token.
3. Tap record. Allow the microphone and notifications. The notification
   "Resona nimmt auf" appears.
4. Speak, lock the screen, keep speaking now and then for 10 minutes.
5. Unlock, open Resona (the notification opens it), note the elapsed time on the
   recording sheet, stop and save.
6. Keep the app open until the recording shows its transcript: in standalone mode the
   queue is only processed while the app runs.
7. Expected: the player's duration matches the elapsed time noted in step 5
   (about 10 minutes), and the transcript covers what was said while locked.

Battery savers of some vendors (Samsung, Xiaomi, Huawei) kill foreground services
anyway; if the recording ends early, set Resona to "unrestricted" battery use.

## Findings

| Area | State |
| --- | --- |
| Batch recording while locked (Android) | Native recorder + `microphone` foreground service. Checked on an Android 15 emulator (debug APK, standalone mode): 180 s with the screen off, the service stayed in the foreground (type `0x80`, wake lock held), the saved `.m4a` was 197 s long for 196 s shown on the recording sheet. Long recordings on a real phone (vendor battery savers, real mic) still need the device test above |
| Push-to-talk, re-dictation | Native recorder too; same upload → hub job flow |
| Interruptions, recovery after a kill (#33) | Web recorder only. Locking or backgrounding does not interrupt the native recorder (the service keeps the microphone), so it never shows "Aufnahme unterbrochen". A phone call does interrupt it, unnoticed: on Android 10+ the recording gets silence during the call and the plugin reports nothing; on iOS the plugin emits `recordingInterruptionBegan`/`Ended`, which the app does not handle yet, so the clock keeps running. It keeps no IndexedDB chunks: if Android kills the app mid-recording, the `.m4a` stays in the app cache but the app does not offer it again. Recovery would need the plugin's crash-safe `aac` format plus a scan of that cache on start |
| Live dictation | **Foreground only**, as in the browser. It streams PCM from `getUserMedia` over the `/v1/listen` WebSocket in the web view, and `live.vue` keeps `useWebRecorder()` for the audio of that same stream. Background live would need native PCM capture and a native WebSocket client |
| Standalone data mode | Works without login: IndexedDB + hub token; processing runs while the app is open |
| Directus login | Blocked as is. The SDK runs in cookie mode: the refresh token is an httpOnly cookie of the Directus host, and from the app origin `https://localhost` that is a cross-site cookie. The WebView accepts third-party cookies (Capacitor enables them), but Directus sets the refresh cookie `SameSite=Lax` (`REFRESH_TOKEN_COOKIE_SAME_SITE` in `docker-compose.coolify.yml`), so it is not sent on the app's cross-site refresh. Password login gets an access token, but the session ends at the first refresh (~15 min). Fix options: JSON auth mode in the app (refresh token in app storage), or `SameSite=None` refresh cookies on Directus. Either way Directus `CORS_ORIGIN` must also list `https://localhost` |
| Keycloak SSO | Blocked by the same cookie, plus the return: Directus redirects back to a web URL, not into the app. Needs the system browser (`@capacitor/browser`) + an app link / custom scheme deep link + a token handover Directus SSO does not offer today (it only sets the cookie) |
| Nitro routes | Not in the app (static build): calendar (`/api/calendar`), `/api/debug-report`, the `/directus` proxy. `NUXT_PUBLIC_DIRECTUS_URL` must be the absolute Directus URL |
| Share target | Android `SEND` intent filter on `MainActivity` for `audio/*`, PDF, PNG, JPEG, TIFF and DOCX; `@capgo/capacitor-share-target` copies the file into the app cache and `app/utils/nativeShare.ts` stashes it like a web share and opens `/share-target`: audio → save sheet → recording, document → uploaded at once → document; both wait for the hub token and become hub jobs. One file per share (no `SEND_MULTIPLE`; of several files the first usable one is taken). Other image types (GIF, HEIC, …) do not offer Resona. Built into the debug APK (manifest checked with `aapt`); the share from the Files app on a real phone is still to be tested. iOS would need a Share Extension + App Group (not set up) |
| iOS | Project generated (`ios/`, Swift Package Manager), `UIBackgroundModes: audio` and `NSMicrophoneUsageDescription` set. Not built: needs Xcode, an Apple developer account and signing |

### Effort for iOS / TestFlight

- Apple developer account (99 USD/year), bundle id `de.ecsplico.resona`, signing in Xcode.
- First build on a Mac with Xcode: `bun run cap:build && npx cap open ios`, set the
  team, run on a device. The recorder plugin keeps recording in the background with
  the audio background mode. Calls interrupt it: the plugin only resumes when iOS
  allows it (`recordingInterruptionEnded.shouldResume`), so the app has to handle those
  events (show the interruption, resume or stop) before iOS is usable; about half a day.
- Hub CORS: add `capacitor://localhost`.
- App Store review needs a privacy policy, the microphone purpose string (set) and a
  reason for background audio (dictation). TestFlight internal testing needs no review.
- Rough effort: 1–2 days for a signed TestFlight build, more if login has to work
  (same blockers as Android).
