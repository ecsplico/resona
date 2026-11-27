import Foundation

/// Language matching and the availability answer of `ResonaSpeech.available()` on iOS
/// (`DeviceSpeechAvailability` in `useDeviceSpeech.ts`). Two recognizers:
/// - iOS 26+: `SpeechTranscriber` (SpeechAnalyzer), whose language assets the app installs
///   through `AssetInventory` (`install()`); preferred whenever it knows the language.
/// - Otherwise `SFSpeechRecognizer` with `requiresOnDeviceRecognition`; its on-device
///   models come with the system (keyboard dictation languages), the app cannot install them.
/// Foundation only: tested by `ios/App/SpeechTests` without Xcode.
enum LanguageSupport {
    /// `AssetInventory.Status` of the `SpeechTranscriber` for the language, nil when iOS is
    /// older than 26, SpeechAnalyzer is unavailable on the device or it lacks the language.
    enum Assets: Equatable {
        case installed, downloading, supported, unsupported
    }

    /// `SFSpeechRecognizer` for the language: on-device capable, only server-based, or none.
    enum Legacy: Equatable {
        case onDevice, serverOnly, none
    }

    enum Engine: Equatable {
        case analyzer, legacy
    }

    static func status(analyzer: Assets?, legacy: Legacy) -> String {
        switch analyzer {
        case .installed: return "available"
        case .downloading: return "downloading"
        case .supported: return "downloadable"
        case .unsupported, nil:
            return legacy == .onDevice ? "available" : "unavailable"
        }
    }

    /// Which recognizer `start()` uses, or the error code why none can. The legacy recognizer
    /// stands in while the SpeechAnalyzer assets are missing (removed by the system after
    /// `available()` said so).
    static func engine(analyzer: Assets?, legacy: Legacy) -> Result<Engine, LanguageError> {
        if analyzer == .installed { return .success(.analyzer) }
        if legacy == .onDevice { return .success(.legacy) }
        if analyzer == .supported || analyzer == .downloading {
            return .failure(LanguageError(code: "language-unavailable"))
        }
        return .failure(LanguageError(code: "language-not-supported"))
    }

    /// `de-DE`, `de_de` → `de-DE`; empty → `de-DE` (the plugin's default, as on Android).
    static func normalize(_ tag: String) -> String {
        let trimmed = tag.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "_", with: "-")
        if trimmed.isEmpty { return "de-DE" }
        let parts = trimmed.split(separator: "-").map(String.init)
        guard let language = parts.first else { return "de-DE" }
        var out = [language.lowercased()]
        for part in parts.dropFirst() {
            out.append(part.count == 2 ? part.uppercased() : part)
        }
        return out.joined(separator: "-")
    }

    /// The region a bare language means (`en` → `en-US`); others use their own code (`de-DE`).
    private static let mainRegions = ["en": "US", "pt": "BR", "zh": "CN", "ja": "JP", "ko": "KR", "da": "DK", "sv": "SE", "nb": "NO", "cs": "CZ", "el": "GR", "uk": "UA", "he": "IL", "hi": "IN", "vi": "VN"]

    /// The identifier among `candidates` (BCP 47 or `_` form) that serves `tag`: the same tag,
    /// else for a bare language (`de`) its main region (`de-DE`), else any region of it.
    /// A tag with a region needs that exact region.
    static func bestMatch(_ tag: String, among candidates: [String]) -> String? {
        let wanted = normalize(tag)
        let normalized = candidates.map { (normalize($0), $0) }
        if let exact = normalized.first(where: { $0.0.caseInsensitiveCompare(wanted) == .orderedSame }) {
            return exact.1
        }
        if wanted.contains("-") { return nil }
        let main = wanted + "-" + (mainRegions[wanted] ?? wanted.uppercased())
        if let preferred = normalized.first(where: { $0.0 == main }) { return preferred.1 }
        return normalized
            .filter { $0.0.lowercased().hasPrefix(wanted + "-") }
            .sorted { $0.0 < $1.0 }
            .first?.1
    }
}

struct LanguageError: Error, Equatable {
    let code: String
}
