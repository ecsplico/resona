import Foundation
import Speech

/// The device side of `ResonaSpeech.available()` / `install()` and the recognizer choice of
/// `start()` (rules in `LanguageSupport`). iOS 26+: `SpeechTranscriber` with its assets in
/// `AssetInventory`; the check and install follow T3 Code's `AppleSpeechEngine.prepare`
/// (`native/apple-speech/AppleSpeechEngine.swift`, pingdotgg/t3code@b70b9e3, MIT, itself
/// adapted from @react-native-ai/apple, MIT, Callstack). Older systems:
/// `SFSpeechRecognizer` on device. No Capacitor or UIKit: typechecked on macOS.
enum SpeechSupport {
    /// The recognizer `start()` runs and the locale it runs in.
    struct Choice {
        let engine: LanguageSupport.Engine
        let locale: Locale
    }

    static func status(for tag: String) async -> String {
        let analyzer = await analyzerAssets(for: tag)
        return LanguageSupport.status(analyzer: analyzer?.assets, legacy: legacy(for: tag).state)
    }

    static func resolve(_ tag: String) async -> Result<Choice, LanguageError> {
        let analyzer = await analyzerAssets(for: tag)
        let legacy = legacy(for: tag)
        switch LanguageSupport.engine(analyzer: analyzer?.assets, legacy: legacy.state) {
        case .success(.analyzer):
            guard let locale = analyzer?.locale else { return .failure(LanguageError(code: "language-not-supported")) }
            return .success(Choice(engine: .analyzer, locale: locale))
        case .success(.legacy):
            guard let locale = legacy.locale else { return .failure(LanguageError(code: "language-not-supported")) }
            return .success(Choice(engine: .legacy, locale: locale))
        case .failure(let error):
            return .failure(error)
        }
    }

    /// Starts downloading the `SpeechTranscriber` assets for the language in the background
    /// (`available()` then answers `downloading`). False when there is nothing the app can
    /// install: below iOS 26, or SpeechAnalyzer does not know the language.
    static func install(_ tag: String) async -> Bool {
        if #available(iOS 26, macOS 26, *) {
            return await AnalyzerAssets.install(tag)
        }
        return false
    }

    /// Asks for speech recognition (needed by `SFSpeechRecognizer`, the fallback below iOS 26).
    static func authorizeLegacy() async -> Bool {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: return true
        case .denied, .restricted: return false
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { status in
                    continuation.resume(returning: status == .authorized)
                }
            }
        @unknown default: return false
        }
    }

    private static func analyzerAssets(for tag: String) async -> (assets: LanguageSupport.Assets, locale: Locale)? {
        if #available(iOS 26, macOS 26, *) {
            return await AnalyzerAssets.state(tag)
        }
        return nil
    }

    private static func legacy(for tag: String) -> (state: LanguageSupport.Legacy, locale: Locale?) {
        let identifiers = SFSpeechRecognizer.supportedLocales().map { $0.identifier }
        guard
            let identifier = LanguageSupport.bestMatch(tag, among: identifiers),
            let recognizer = SFSpeechRecognizer(locale: Locale(identifier: identifier))
        else {
            return (.none, nil)
        }
        return (recognizer.supportsOnDeviceRecognition ? .onDevice : .serverOnly, recognizer.locale)
    }
}

@available(iOS 26, macOS 26, *)
enum AnalyzerAssets {
    /// The transcriber the live session runs: volatile results for the preview, plain text.
    static func transcriber(for locale: Locale) -> SpeechTranscriber {
        SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [.volatileResults], attributeOptions: [])
    }

    static func locale(for tag: String) async -> Locale? {
        guard SpeechTranscriber.isAvailable else { return nil }
        return await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: LanguageSupport.normalize(tag)))
    }

    static func state(_ tag: String) async -> (assets: LanguageSupport.Assets, locale: Locale)? {
        guard let locale = await locale(for: tag) else { return nil }
        switch await AssetInventory.status(forModules: [transcriber(for: locale)]) {
        case .installed: return (.installed, locale)
        case .downloading: return (.downloading, locale)
        case .supported: return (.supported, locale)
        case .unsupported: return (.unsupported, locale)
        @unknown default: return (.unsupported, locale)
        }
    }

    static func install(_ tag: String) async -> Bool {
        guard let locale = await locale(for: tag) else { return false }
        let key = locale.identifier(.bcp47)
        guard Downloads.shared.begin(key) else { return true }
        let request: AssetInstallationRequest?
        do {
            request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber(for: locale)])
        }
        catch {
            Downloads.shared.end(key)
            return false
        }
        guard let request else {
            // Already installed: nothing to download.
            Downloads.shared.end(key)
            return true
        }
        Task.detached {
            try? await request.downloadAndInstall()
            Downloads.shared.end(key)
        }
        return true
    }

    /// Locales with a download in flight, so a second tap does not start another one.
    final class Downloads: @unchecked Sendable {
        static let shared = Downloads()
        private let lock = NSLock()
        private var running = Set<String>()

        func begin(_ key: String) -> Bool {
            lock.lock()
            defer { lock.unlock() }
            return running.insert(key).inserted
        }

        func end(_ key: String) {
            lock.lock()
            running.remove(key)
            lock.unlock()
        }
    }
}
