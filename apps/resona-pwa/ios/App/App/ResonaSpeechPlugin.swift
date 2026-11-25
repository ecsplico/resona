import AVFAudio
import Capacitor
import Foundation

/// On-device live dictation for the live page (`app/composables/useDeviceSpeech.ts`,
/// `app/utils/resonaSpeech.ts`), the iOS side of the Android `ResonaSpeechPlugin.java` with
/// the same JS surface:
///
/// - `available({language})` → `{status}`: `available`, `downloadable`, `downloading` or
///   `unavailable` (`SpeechSupport`; iOS 26+ asks `AssetInventory` about the
///   `SpeechTranscriber` assets, older systems whether `SFSpeechRecognizer` runs on device).
/// - `install({language})` → `{started}`: starts the `SpeechTranscriber` asset download
///   (iOS 26+; false below, where on-device models come with the system).
/// - `start({language, emitPcm})`: asks for the microphone (and, for `SFSpeechRecognizer`,
///   speech recognition) if needed, then runs a `LiveSpeechSession`. Events:
///   `update {committed, tentative, revision}`, `pcm {data}` (base64 little-endian int16,
///   16 kHz mono, 250 ms per event) and `error {code, message, fatal}`.
/// - `stop()` → `{text}`: the whole on-device text, after the last `pcm` event.
///
/// Registered by `ResonaBridgeViewController` (app-local plugin, not an npm package).
/// The session's audio session plus `UIBackgroundModes: audio` keep it running while the
/// screen is locked; the web app's `startRecordingService()` is Android-only and a no-op here.
@objc(ResonaSpeechPlugin)
public class ResonaSpeechPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "ResonaSpeechPlugin"
    public let jsName = "ResonaSpeech"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "available", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "install", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "start", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stop", returnType: CAPPluginReturnPromise),
    ]

    private let lock = NSLock()
    private var session: LiveSpeechSession?

    @objc func available(_ call: CAPPluginCall) {
        let language = call.getString("language", "")
        Task {
            call.resolve(["status": await SpeechSupport.status(for: language)])
        }
    }

    @objc func install(_ call: CAPPluginCall) {
        let language = call.getString("language", "")
        Task {
            call.resolve(["started": await SpeechSupport.install(language)])
        }
    }

    @objc func start(_ call: CAPPluginCall) {
        let language = call.getString("language", "de-DE")
        let emitPcm = call.getBool("emitPcm", true)
        Task {
            await self.begin(call, language: language, emitPcm: emitPcm)
        }
    }

    @objc func stop(_ call: CAPPluginCall) {
        guard let ending = replace(with: nil) else {
            call.resolve(["text": ""])
            return
        }
        Task {
            let text = await ending.stop()
            call.resolve(["text": text])
        }
    }

    private func begin(_ call: CAPPluginCall, language: String, emitPcm: Bool) async {
        guard await Self.microphoneAccess() else {
            call.reject("Mikrofon nicht erlaubt", "not-allowed")
            return
        }
        let choice: SpeechSupport.Choice
        switch await SpeechSupport.resolve(language) {
        case .success(let resolved):
            choice = resolved
        case .failure(let error):
            call.reject(Self.message(error.code), error.code)
            return
        }
        if choice.engine == .legacy, !(await SpeechSupport.authorizeLegacy()) {
            call.reject("Spracherkennung für Resona nicht erlaubt (Einstellungen → Resona)", "service-not-allowed")
            return
        }
        var me: LiveSpeechSession?
        let next = LiveSpeechSession(choice: choice, emitPcm: emitPcm, events: LiveSpeechSession.Events(
            update: { [weak self] committed, tentative, revision in
                guard let self, self.isCurrent(me) else { return }
                self.notifyListeners("update", data: ["committed": committed, "tentative": tentative, "revision": revision])
            },
            pcm: { [weak self] frame in
                guard let self, self.isCurrent(me) else { return }
                self.notifyListeners("pcm", data: ["data": frame.base64EncodedString()])
            },
            error: { [weak self] code, message, fatal in
                guard let self, self.isCurrent(me) else { return }
                self.notifyListeners("error", data: ["code": code, "message": message, "fatal": fatal])
            }
        ))
        me = next
        replace(with: next)?.abort()
        do {
            try await next.start()
        }
        catch let error as LiveSpeechSession.StartError {
            clear(next)
            call.reject(error.message, error.code)
            return
        }
        catch {
            clear(next)
            call.reject(error.localizedDescription, "audio-capture")
            return
        }
        call.resolve()
    }

    /// Only the current session talks to the web app; a replaced one may still be draining.
    private func isCurrent(_ candidate: LiveSpeechSession?) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return candidate != nil && session === candidate
    }

    /// Makes `next` the current session; returns the previous one.
    @discardableResult
    private func replace(with next: LiveSpeechSession?) -> LiveSpeechSession? {
        lock.lock()
        defer { lock.unlock() }
        let previous = session
        session = next
        return previous
    }

    private func clear(_ failed: LiveSpeechSession) {
        lock.lock()
        defer { lock.unlock() }
        if session === failed { session = nil }
    }

    private static func microphoneAccess() async -> Bool {
        if #available(iOS 17, *) {
            switch AVAudioApplication.shared.recordPermission {
            case .granted: return true
            case .denied: return false
            default: return await AVAudioApplication.requestRecordPermission()
            }
        }
        #if os(iOS)
        let audioSession = AVAudioSession.sharedInstance()
        switch audioSession.recordPermission {
        case .granted: return true
        case .denied: return false
        default:
            return await withCheckedContinuation { continuation in
                audioSession.requestRecordPermission { granted in continuation.resume(returning: granted) }
            }
        }
        #else
        return false
        #endif
    }

    private static func message(_ code: String) -> String {
        switch code {
        case "language-unavailable": return "Das Sprachpaket für die lokale Erkennung fehlt."
        case "language-not-supported": return "Die lokale Spracherkennung kann diese Sprache nicht."
        default: return "Lokale Spracherkennung nicht verfügbar (\(code))."
        }
    }
}
