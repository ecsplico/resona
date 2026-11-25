import Foundation

/// The live preview of one `ResonaSpeech` session: committed text (finished segments, never
/// changed again) plus the tentative hypothesis of the segment in progress, and a revision
/// that grows with every change so the web app can drop stale events.
///
/// Same `{committed, tentative, revision}` shape as T3 Code's `SpeechStreamText`
/// (pingdotgg/t3code, MIT) and as the Android `SpeechTranscript.java`. Foundation only:
/// tested by `ios/App/SpeechTests` without Xcode.
struct SpeechTranscript {
    private(set) var committed = ""
    private(set) var tentative = ""
    private(set) var revision = 0

    /// Replaces the tentative text. Returns whether anything changed.
    @discardableResult
    mutating func setTentative(_ text: String) -> Bool {
        let next = Self.clean(text)
        if next == tentative { return false }
        tentative = next
        revision += 1
        return true
    }

    /// Commits a finished segment and clears the tentative text. An empty segment only clears
    /// the tentative text (the recognizer withdrew its hypothesis).
    @discardableResult
    mutating func commit(_ text: String) -> Bool {
        let piece = Self.clean(text)
        if piece.isEmpty { return setTentative("") }
        if !committed.isEmpty && !Self.startsWithPunctuation(piece) {
            committed += " "
        }
        committed += piece
        tentative = ""
        revision += 1
        return true
    }

    /// At the end of a session a trailing hypothesis counts as said.
    @discardableResult
    mutating func commitTentative() -> Bool {
        commit(tentative)
    }

    /// Committed text followed by the tentative text.
    var text: String {
        if tentative.isEmpty { return committed }
        if committed.isEmpty { return tentative }
        return committed + " " + tentative
    }

    static func clean(_ text: String) -> String {
        text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    private static func startsWithPunctuation(_ piece: String) -> Bool {
        guard let first = piece.first else { return false }
        return ".,;:!?".contains(first)
    }
}

/// `SFSpeechRecognizer` (below iOS 26) reports one growing hypothesis per recognition task,
/// but after a pause the on-device recognizer may start that hypothesis over from empty
/// without a final result, which would wipe the words said before the pause from the
/// preview. A partial that is much shorter than the previous one and does not begin with
/// its first word is taken as such a restart: the previous hypothesis is committed first.
/// A recognizer that merely revises its words keeps the first word or the length.
enum PartialRestart {
    static func isRestart(previous: String, next: String) -> Bool {
        let before = SpeechTranscript.clean(previous)
        let after = SpeechTranscript.clean(next)
        if before.isEmpty || after.isEmpty { return false }
        if after.count * 2 >= before.count { return false }
        return firstWord(before) != firstWord(after)
    }

    private static func firstWord(_ text: String) -> String {
        let word = text.split(separator: " ", maxSplits: 1).first.map(String.init) ?? ""
        return word.lowercased().trimmingCharacters(in: .punctuationCharacters)
    }
}
