import Foundation

/// What ended one `SFSpeechRecognizer` task (the fallback below iOS 26), as far as the
/// restart policy cares.
enum RecognitionEnd: Equatable {
    /// Final result: the recognizer closed the utterance.
    case finished
    /// No speech heard (or the task was cancelled by the system): restart at once.
    case silence
    /// A failure a restart cannot fix (`code` for the web app's `error` event).
    case fatal(code: String)
    /// Anything else: retry with a growing delay.
    case transient(code: String)

    /// Classifies an `SFSpeechRecognitionTask` error by domain and code. The private domains
    /// are what iOS reports in practice (not documented by Apple): `kAFAssistantErrorDomain`
    /// 1110 "No speech detected", 216 / `kLSRErrorDomain` 301 "request was canceled",
    /// `kLSRErrorDomain` 102 "Failed to access assets" (language pack missing), 201 "Siri and
    /// Dictation are disabled". `SFSpeechErrorDomain` is the public one (iOS 14+).
    static func classify(domain: String, code: Int) -> RecognitionEnd {
        switch (domain, code) {
        case ("kAFAssistantErrorDomain", 1110):
            return .silence
        case ("kAFAssistantErrorDomain", 216), ("kLSRErrorDomain", 301):
            return .silence
        case ("kLSRErrorDomain", 102):
            return .fatal(code: "language-unavailable")
        case ("kLSRErrorDomain", 201):
            return .fatal(code: "service-not-allowed")
        case ("SFSpeechErrorDomain", 2):
            return .transient(code: "audio")
        case ("SFSpeechErrorDomain", 12):
            return .transient(code: "timeout")
        default:
            return .transient(code: "service")
        }
    }
}

/// When a recognition ends while the microphone still runs, whether and how soon to start the
/// next one. Silence and a clean end restart at once and never count against the budget, so
/// long pauses in a dictation do not end the preview. Transient failures restart with a
/// growing delay; after `maxFailures` in a row without new text, or on a fatal error, the
/// preview gives up and the web app is told. The PCM for the hub correction keeps flowing
/// either way. Port of the Android `RecognitionRestarts.java` (error naming after T3 Code's
/// `VoiceTranscriptionFailure`, pingdotgg/t3code, MIT). Foundation only: tested by
/// `ios/App/SpeechTests` without Xcode.
struct RecognitionRestarts {
    enum Decision: Equatable {
        case restart(after: TimeInterval)
        case giveUp(code: String)
    }

    static let maxFailures = 5
    static let baseDelay: TimeInterval = 0.25
    static let maxDelay: TimeInterval = 2

    private(set) var failures = 0

    /// The recognizer produced text: the failure budget starts over.
    mutating func heard() {
        failures = 0
    }

    mutating func after(_ end: RecognitionEnd) -> Decision {
        switch end {
        case .finished, .silence:
            return .restart(after: 0)
        case .fatal(let code):
            return .giveUp(code: code)
        case .transient(let code):
            failures += 1
            if failures > Self.maxFailures { return .giveUp(code: code) }
            let delay = Self.baseDelay * Double(1 << (failures - 1))
            return .restart(after: min(Self.maxDelay, delay))
        }
    }
}
