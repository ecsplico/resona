# Resona app (Capacitor prototype)

!!! info "Reference app"
    This page belongs to the reference app, not the hub. The app is one consumer of
    the hub's published HTTP/WebSocket contract and keeps its users, recordings and
    transcripts itself ([ADR 0001](../adr/0001-hub-and-app-boundary.md)).

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
  (`@capgo/capacitor-audio-recorder` ≥ 8.4, `format: "aac"`: ADTS AAC, `audio/aac`); in the browser it is
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
- Live dictation with Einstellungen → Live → Quelle "Gerät" uses the app-local Capacitor
  plugin `ResonaSpeech` on Android 13+ (`android/app/src/main/java/de/ecsplico/resona/ResonaSpeechPlugin.java`,
  pure helpers and their JUnit tests under `…/resona/speech/`; JS side
  `app/utils/resonaSpeech.ts` + `useDeviceSpeech`). The plugin captures the microphone
  itself (`AudioRecord`, 16 kHz mono PCM16) and feeds the on-device recognizer
  (`SpeechRecognizer.createOnDeviceSpeechRecognizer`, API 31+) through a pipe
  (`EXTRA_AUDIO_SOURCE`, API 33+) in a segmented session; its results arrive as `update`
  events `{committed, tentative, revision}` (the preview), and the same audio arrives as
  `pcm` events (base64 int16, 250 ms each). The web app starts the `microphone` foreground
  service first (`app/utils/recordingService.ts`, shared with the native recorder), so the
  session keeps running with the screen locked. The PCM is the correction's input
  (`useLiveCorrection` takes `useDeviceSpeech().pcm` as its `PcmCapture`) and is kept as the
  saved audio (WAV): with Korrektur on, the hub's text is the saved transcript and the
  on-device text is only the preview (stamp `android-speech+<engine>`). Silence and
  recognizer ends restart the recognition at once; transient errors retry with backoff and
  after five in a row the preview gives up while the audio goes on. `available`/`install`
  map `checkRecognitionSupport`/`triggerModelDownload` to the drawer's language-pack states.
  Below Android 13 the plugin answers `unsupported` (the drawer says so, start is disabled);
  in the browser the device source stays on-device Web Speech.
  Structure ported from T3 Code's Android voice module (pingdotgg/t3code@b70b9e3, MIT).
- On iOS the same plugin (`ios/App/App/ResonaSpeechPlugin.swift`, registered by
  `ResonaBridgeViewController`; Capacitor-free helpers under `ios/App/App/Speech/`) gives the
  same JS surface, events and error codes. An `AVAudioEngine` input tap feeds the recognizer
  and, resampled with `AVAudioConverter` to 16 kHz int16, the `pcm` events (250 ms each).
  iOS 26+: `SpeechAnalyzer` with `SpeechTranscriber(reportingOptions: [.volatileResults])`;
  volatile results are the tentative text, finalized ones the committed text, and the
  language assets come from `AssetInventory` (`available` = its status, `install` starts
  `downloadAndInstall` in the background). Below iOS 26, or while the SpeechAnalyzer assets
  are missing: `SFSpeechRecognizer` with `requiresOnDeviceRecognition` (on-device models
  ship with the system's dictation languages; none for the language → `unavailable`), one
  task per utterance, restarted at once, transient errors with backoff. Audio session
  `.playAndRecord` plus `UIBackgroundModes: audio` keep it running while locked (no
  foreground service on iOS; `startRecordingService` returns at once). Info.plist carries
  `NSMicrophoneUsageDescription` and `NSSpeechRecognitionUsageDescription` (the speech
  prompt appears only on the `SFSpeechRecognizer` path). Transcripts are stamped
  `ios-speech`. The pure helpers typecheck and run without Xcode (`bun run
  test:ios-speech`, `swiftc` against the macOS SDK); the plugin itself needs an Xcode build.
  Asset check/install adapted from T3 Code's `native/apple-speech` (MIT).
- Sharing a file from another app (Android share sheet → Resona) goes through a
  `SEND` / `SEND_MULTIPLE` intent filter and `@capgo/capacitor-share-target` into the same share page as
  the web share target (`/share-target`); see the Findings row "Share target".
- On stop the app reads the recorded file back into the web view
  (`Capacitor.convertFileSrc` + `fetch`) and deletes it from the app cache; a
  file it cannot read stays in the cache.
- Recovery after a kill (Android): ADTS is a stream of self-contained frames, so a
  recording the app never stopped stays readable up to its last whole frame
  (MPEG-4 writes its index only at stop). The recordings list scans
  `cache/capacitor-audio-recorder/` for `recording-*.aac` files older than the running
  web view (`app/data/nativeUnfinished.ts`) and offers each like a web recording left in
  IndexedDB: "Nicht gespeichert …", Behalten → save sheet (upload → hub job) or Verwerfen.
  The file is deleted once saved or discarded; only its whole frames are uploaded. Nothing
  is offered while the plugin records; empty files are deleted; `.m4a` files of older
  app versions are left alone (unreadable without their index).
- The hub sniffs ADTS by its frame sync (`sniff_media`, the MPEG audio branch) and
  admits it as an audio job; ffmpeg decodes it, a cut-off last frame included.
- Directus login (data mode `directus`): the web build keeps cookie auth (refresh token
  in the `SameSite=Lax` httpOnly cookie, first-party through the PWA's `/directus` proxy).
  The app runs on `https://localhost` / `capacitor://localhost`, cross-site to Directus,
  where that cookie is never sent; so inside the app (`Capacitor.isNativePlatform()`)
  `useDirectus` uses the SDK's JSON mode: `/auth/login` and `/auth/refresh` send and
  return the refresh token in the body, and `app/utils/directusAuth.ts`
  (`preferencesAuthStorage`) keeps access token, refresh token and expiry in
  `@capacitor/preferences` under `resona.auth`. No refresh timer in the app
  (`autoRefresh: false`): Android and iOS freeze it in the background, and on resume it
  would race the first request's refresh with the same rotating refresh token. Every
  request instead refreshes an access token within 30 s of expiry first (the SDK's
  `getToken()`, one refresh in flight); after a restart the auth middleware's
  `restoreSession()` does the same from the stored session. `assetUrl()` is async and
  asks `getToken()` first, so audio fetched after a long pause carries a fresh token.
  Stored tokens are removed only when Directus rejects the refresh token (400/401 from
  `/auth/refresh`): offline, a timeout or a 5xx keep them, the app stays signed in and
  refreshes on the next request. A request's 401 first waits for a refresh already in
  flight (a request frozen in the background answers late with the old token on resume)
  and retries with the new token; only without one it refreshes once and retries, so the
  rotating refresh token is never sent twice. Logout
  removes the key, also when the logout request fails. The session ends when the refresh
  token is unused for `REFRESH_TOKEN_TTL` (30 days on Coolify) or Directus rejects it.
- Why Preferences and not a secure-storage plugin: Preferences is the Capacitor team's
  own plugin, versioned with Capacitor 8; the store (Android SharedPreferences, iOS
  UserDefaults) is private to the app and, unlike web view storage, not evicted by
  WebKit. It is not encrypted at rest; the Keychain/Keystore plugins that would be
  (`capacitor-secure-storage-plugin`, `@aparajita/capacitor-secure-storage`) are
  single-maintainer projects, and the web view already keeps the hub token in plain
  localStorage. Android excludes `CapacitorStorage.xml` from cloud backup and device
  transfer (`res/xml/backup_rules.xml`, `data_extraction_rules.xml`), so a restored
  phone signs in again. iOS: UserDefaults are part of the device backup; revisit with
  the iOS build.

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

With Directus login instead of standalone mode, point the app at the PWA's Directus
proxy (absolute: the app has no Nitro server):

```bash
NUXT_PUBLIC_DATA_MODE=directus \
NUXT_PUBLIC_DIRECTUS_URL=https://<pwa-host>/directus \
NUXT_PUBLIC_RESONA_API_URL=https://<hub-host> \
  bun run cap:apk
```

`cap:apk` = `cap:build` (`RESONA_CAPACITOR=1 nuxt generate && cap sync`) + `./gradlew
assembleDebug` in `android/`. The native projects (`android/`, `ios/`) are committed;
`cap sync` copies the web build and plugin list into them (both are gitignored).

App icon and splash screen come from the SVGs in `assets/` (white microphone on
`#D96846`, the PWA icon; splash on `#FBF5EC`). After changing them, run
`bun run cap:assets` and commit the generated `android/.../res` and `ios/.../Assets.xcassets`
files; the tool also reformats `AndroidManifest.xml` without changing it, revert that.

The hub must allow the app's origin in `CORS_ORIGINS`: `https://localhost` on Android
(`capacitor://localhost` on iOS); the default in the root `docker-compose.coolify.yml`
lists both (an explicit `CORS_ORIGINS` env must add them). The bearer token still
guards every call; CORS only lets the web view read the responses. For Directus login,
Directus `CORS_ORIGIN` lists them too: every PWA compose file appends `APP_ORIGINS`
(default `https://localhost,capacitor://localhost`) to `PWA_ORIGIN`. Through the
`/directus` proxy the app's `Origin` reaches Directus, which answers CORS itself. The
refresh cookie stays `SameSite=Lax`, so a page on another `https://localhost` never
sends it along.

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

Recovery after a kill:

1. Start a recording, speak for about a minute.
2. `adb shell am force-stop de.ecsplico.resona` (or swipe the app away / `adb shell am kill`
   after sending it to the background).
3. Open Resona: the list shows "Nicht gespeichert: Aufnahme vom … (m:ss min)". Behalten →
   save; the transcript covers that minute. Afterwards
   `adb shell run-as de.ecsplico.resona ls cache/capacitor-audio-recorder` lists no file.
4. A normal record → stop → save leaves no file there either.

Live dictation while locked (Android 13+, needs a human; recognizer quality, language
packs and vendor audio policies differ per phone):

1. Einstellungen → Live: Quelle "Gerät", Korrektur on. If the drawer says the language
   pack is missing, load it ("Sprachpaket laden") and wait until it says available.
2. Open Live. Allow the microphone and notifications; "Resona nimmt auf" appears.
3. Speak: the on-device preview grows word by word (tentative text, then committed).
4. Lock the screen, keep dictating for 2 minutes with pauses of 10 s or more.
5. Unlock: the preview covers the locked part (pauses did not end it), the correction
   blocks keep landing.
6. Stop and save: the recording has a `.wav` of the full length, the transcript is the
   hub's correction, stamped `android-speech+<engine>`.
7. Android 12 or older: the drawer shows the device source as unsupported; no crash.
8. iPhone with iOS 26 (once an iOS build exists): steps 1–6 with German. The first start
   asks for the microphone only; "Sprachpaket laden" downloads the SpeechTranscriber assets
   (the drawer shows "wird noch geladen", then available). Locked dictation needs no
   notification; the stamp is `ios-speech+<engine>`. Also check airplane mode (still
   recognizes: on-device) and a phone call during dictation (the session should report an
   `error` and stop, no crash).
9. iPhone with iOS 18 or older: Quelle "Gerät" asks for speech recognition on first start
   (`NSSpeechRecognitionUsageDescription`); German must be available if it is a keyboard
   dictation language on the phone, otherwise the drawer says unavailable.

Directus login over time (data mode `directus`, build above; needs a human, ≥ 1 h):

1. Install the Directus build, open Resona, sign in with e-mail and password (an account
   with a Directus password; Keycloak-only accounts have none). The login page shows the
   hint "Die Anmeldung mit Keycloak ist in der App noch nicht möglich …" instead of the
   Keycloak button.
2. Note the time. Open a recording (player loads), then press Home: the app is in the
   background.
3. After ≥ 20 min (past the 15 min access token): open Resona from the recent apps.
   Expected: no login page, the list loads, a recording's audio plays.
4. Lock the phone for ≥ 45 min more (total ≥ 1 h), unlock, open Resona. Same expectation.
5. Swipe the app away (or `adb shell am force-stop de.ecsplico.resona`), open it again
   after ≥ 20 min. Expected: still signed in (session restored from Preferences).
6. Optional check that the token lives in Preferences:
   `adb shell run-as de.ecsplico.resona cat shared_prefs/CapacitorStorage.xml` shows
   `resona.auth` with an `access_token` and a `refresh_token`.
7. Sign-out: the UI has no sign-out button (web neither); that `useDirectus().logout()`
   removes the key is unit-tested only. Android Settings → Apps → Resona → Storage →
   Clear storage resets the app: the login page appears.
8. Web build unchanged: sign in on the PWA in a desktop browser (password and Keycloak),
   reload after ≥ 20 min, still signed in.

Phone call during a recording (Android 10+; needs a SIM or a second phone):

1. Tap record, dictate a few sentences, keep the app open (or lock the phone).
2. Call the phone, answer, talk for about 30 s, hang up.
3. Open Resona: the recording sheet shows "AUFNAHME UNTERBROCHEN" with "Ein Anruf oder eine
   andere App hat das Mikrofon übernommen. Die Aufnahme ist angehalten; diese Zeit fehlt darin,
   Bisheriges bleibt erhalten." The clock stopped when the call was answered.
4. Tap Fortsetzen, dictate a few more sentences, stop and save.
5. Expected: the player's duration matches the elapsed time shown (the call is not in it), and
   the transcript has both parts without a long silent stretch between them.
6. Repeat with Beenden instead of Fortsetzen in step 4: the recording ends at the call.
7. Repeat with the phone locked and the call answered from the lock screen. If the notice
   instead says "Der Anruf wurde erst verspätet erkannt: ein Teil davon ist als Stille darin",
   the web view got the event more than 3 s late: the clock then includes that silence and the
   player's duration should still match it. Note how often this happens.
8. Optional: during a recording, start a voice memo in another app. Expect the same interruption.
   Then: start a Resona recording first and another recording app afterwards *without* a call;
   if Android silences that other app and not Resona, nothing must be shown.

iPhone (once an iOS build exists): run steps 1–6. The notice reads "Die Aufnahme ist
angehalten, Bisheriges bleibt erhalten." and makes no promise about the audio: the recorder
plugin (capgo 8.4.1) resumes on its own when the call ends, before the app pauses it again,
so a few seconds after the call can be in the file. Check how much, and that the player's
duration still matches the clock.

On an emulator a call can be tried with `adb emu gsm call 5551234`, `adb emu gsm accept
5551234`, later `adb emu gsm cancel 5551234` (not yet checked whether it silences the recording
like a real call).

Battery savers of some vendors (Samsung, Xiaomi, Huawei) kill foreground services
anyway; if the recording ends early, set Resona to "unrestricted" battery use.

## Findings

| Area | State |
| --- | --- |
| Batch recording while locked (Android) | Native recorder + `microphone` foreground service. Checked on an Android 15 emulator (debug APK, standalone mode): 180 s with the screen off, the service stayed in the foreground (type `0x80`, wake lock held), the saved `.m4a` was 197 s long for 196 s shown on the recording sheet. Long recordings on a real phone (vendor battery savers, real mic) still need the device test above |
| Push-to-talk, re-dictation | Native recorder too; same upload → hub job flow |
| Interruptions (#33, #39) | Web recorder: track `mute`/`ended` and a hidden page interrupt it. Native recorder: locking or backgrounding does not interrupt it (the service keeps the microphone); a phone call (or another app taking the microphone) does. Android 10+: the OS feeds the recording silence during a call and the recorder plugin reports nothing, so the app-local Capacitor plugin `RecordingSilence` (`AudioManager.registerAudioRecordingCallback`, `AudioRecordingConfiguration.isClientSilenced()` of the app's own recording session, no phone-state permission) reports it with a native timestamp. iOS: the recorder plugin's `recordingInterruptionBegan`/`Ended` events (an `Ended` without `shouldResume` and without a begin counts only while the plugin reports `PAUSED`, since it also arrives late after the user continued; after a `recordingError` the plugin cannot resume, so Fortsetzen is refused and the notice says Beenden keeps everything recorded so far). Either way the app pauses the native recording, stops the clock and shows "Aufnahme unterbrochen"; Fortsetzen resumes the same file, Beenden stops it. Android: the call is cut out of the audio ("… diese Zeit fehlt darin"), except the short delay until the callback fires; when the web view gets the event more than 3 s late (it stalls under the dialer or lock screen), the notice says part of the call is in the audio as silence and the clock counts it; if Android refuses to pause, the notice says the call is in the audio as silence. iOS: the notice promises nothing about the audio, since the plugin resumes on its own after the call before the app can pause it. Below Android 10 a call stays undetected. Unit-tested with the plugins mocked and the debug APK builds; the call on a real phone (device test above) is still open, iOS is not built |
| Recovery after a kill (#33, #40) | Web recorder: IndexedDB chunks (`resona-unfinished-recordings`). Native recorder (Android): records ADTS (`format: "aac"`, plugin ≥ 8.4), which stays readable up to its last frame; on the next open the recordings list offers `recording-*.aac` files left in `cache/capacitor-audio-recorder/` (Behalten → save → hub job, or Verwerfen; the file is deleted either way). Unit-tested with plugin and Filesystem mocked, and ffmpeg decodes a cut-off ADTS file; the kill on a real phone (device test above) is still open. iOS: not offered (the plugin writes to the app's `tmp/`, which the scan does not read) |
| Live dictation | Hub source: **foreground only**, as in the browser. It streams PCM from `getUserMedia` over the `/v1/listen` WebSocket in the web view, and `live.vue` keeps `useWebRecorder()` for the audio of that same stream. Device source on Android 13+: the `ResonaSpeech` plugin captures natively under the `microphone` foreground service, so preview, PCM for the hub correction and the saved WAV continue while locked (#163). JS side unit-tested with the plugin mocked (`test/useDeviceSpeech.native.spec.ts`), pure Java helpers with JUnit (`android/app/src/test/java/de/ecsplico/resona/speech/`); the locked run on a real phone (device test above) is still open, including whether Android throttles the web view's correction requests while locked (blocks would then queue and catch up after unlocking) |
| Standalone data mode | Works without login: IndexedDB + hub token; processing runs while the app is open |
| Directus login | JSON auth mode in the app (#41): access and refresh token in `@capacitor/preferences`, refreshed before a request once the access token is within 30 s of expiry, also after background or restart; web build unchanged (cookie mode). Directus `CORS_ORIGIN` lists `https://localhost` and `capacitor://localhost` (`APP_ORIGINS`). Unit-tested with Capacitor and the SDK mocked (`test/useDirectus.native.spec.ts`); the ≥ 1 h login on a real phone (device test above) is still open |
| Keycloak SSO | Not in the app: the login page hides the Keycloak button and says to sign in with password (Keycloak-only accounts: use the browser). Missing: Directus' OpenID callback ends by setting the refresh cookie (`AUTH_KEYCLOAK_MODE: cookie`) on the Directus host and redirecting to an allow-listed URL; it never hands a token to the redirect target, and no endpoint trades a one-time code for a JSON-mode refresh token. In the app the flow has to run in the system browser (Capacitor opens foreign hosts there; Keycloak/Google refuse embedded web views), so that cookie lands in the browser, not the app. To build: (1) a Directus endpoint extension the browser reaches after SSO (redirect allow-list entry, proxy `ALLOWED` path) that reads the refresh cookie first-party, refreshes in JSON mode, stores the result under a single-use, short-lived code bound to a verifier the app generated (PKCE-style), clears the browser session and redirects to an app link (`https://<pwa-host>/app-auth?code=…`, Android App Link with `assetlinks.json` / iOS Universal Link) or a custom scheme; (2) a POST exchanging code + verifier for `{access_token, refresh_token, expires}`; (3) in the app `@capacitor/browser` to start it, `@capacitor/app` `appUrlOpen` to receive the code, the intent filter / associated domain, then `preferencesAuthStorage().set(...)`. About 1–2 days plus a security review of the handover |
| Nitro routes | Not in the app (static build): calendar (`/api/calendar`), `/api/debug-report`, the `/directus` proxy. `NUXT_PUBLIC_DIRECTUS_URL` must be the absolute Directus URL |
| Share target | Android `SEND` intent filter on `MainActivity` for `audio/*`, PDF, PNG, JPEG, TIFF and DOCX, `SEND_MULTIPLE` for `audio/*`, `application/*` and `image/*` (Android gives several files their common type, `*/*` when audio and documents are mixed, which matches too); `@capgo/capacitor-share-target` copies the files into the app cache and `app/utils/nativeShare.ts` stashes every usable one and opens `/share-target`: documents → uploaded at once, then audio → one save sheet (several recordings keep their file names as titles) → recordings; all wait for the hub token and become hub jobs. One file opens its page, several the list. Files of other types in a multi-share (GIF, XLSX, …) are skipped and reported; a single file of another type does not offer Resona. Tested on a Pixel 9a (Android 17) from the Files app: one memo (cold start), one PDF, one DOCX, two memos + a PDF in one share. DOCX needs a hub that accepts Word uploads (after 0.2.2). iOS would need a Share Extension + App Group (not set up) |
| iOS | Project generated (`ios/`, Swift Package Manager), `UIBackgroundModes: audio`, `NSMicrophoneUsageDescription` and `NSSpeechRecognitionUsageDescription` set; `ResonaSpeech` plugin in Swift (#164, see above; pure helpers checked with `bun run test:ios-speech`). Not built: needs Xcode, an Apple developer account and signing |

### Effort for iOS / TestFlight

- Apple developer account (99 USD/year), bundle id `de.ecsplico.resona`, signing in Xcode.
- First build on a Mac with Xcode: `bun run cap:build && npx cap open ios`, set the
  team, run on a device. The recorder plugin keeps recording in the background with
  the audio background mode. Calls interrupt it; the app handles the plugin's
  `recordingInterruptionBegan`/`Ended` events (Findings row "Interruptions"), still to be
  checked with a call on an iPhone.
- Hub CORS: `capacitor://localhost` is in the default `CORS_ORIGINS` of the root
  `docker-compose.coolify.yml` and in `APP_ORIGINS` (Directus).
- App Store review needs a privacy policy, the microphone purpose string (set) and a
  reason for background audio (dictation). TestFlight internal testing needs no review.
- Rough effort: 1–2 days for a signed TestFlight build. Password login works as on
  Android (JSON auth); Keycloak SSO has the same gap (Findings).
