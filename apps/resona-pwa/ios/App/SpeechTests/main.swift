// Tests for the Capacitor-free helpers of the iOS `ResonaSpeech` plugin
// (`ios/App/App/Speech/`), runnable without Xcode: `sh ios/App/SpeechTests/run.sh`
// (`bun run test:ios-speech`) compiles them with these checks for macOS and runs them.
// Mirrors the Android JUnit tests in `android/app/src/test/java/de/ecsplico/resona/speech/`.
import AVFAudio
import Foundation

var failures = 0
var checks = 0

func check(_ condition: @autoclosure () -> Bool, _ label: String, file: String = #fileID, line: Int = #line) {
    checks += 1
    if !condition() {
        failures += 1
        print("FAIL \(file):\(line) \(label)")
    }
}

func equal<T: Equatable>(_ actual: T, _ expected: T, _ label: String, file: String = #fileID, line: Int = #line) {
    checks += 1
    if actual != expected {
        failures += 1
        print("FAIL \(file):\(line) \(label): got \(actual), expected \(expected)")
    }
}

// MARK: SpeechTranscript

do {
    var t = SpeechTranscript()
    check(t.setTentative("Der Pat"), "tentative changes")
    equal(t.revision, 1, "revision after first tentative")
    check(!t.setTentative("  Der   Pat "), "same tentative after cleaning is no change")
    equal(t.revision, 1, "revision unchanged")
    t.setTentative("Der Patient")
    equal(t.committed, "", "nothing committed")
    equal(t.text, "Der Patient", "text = tentative")
}

do {
    var t = SpeechTranscript()
    t.setTentative("Der Patient")
    t.commit("Der Patient kommt.")
    t.setTentative("Heute")
    t.commit("Heute geht es besser")
    t.commit(", sagt er.")
    equal(t.committed, "Der Patient kommt. Heute geht es besser, sagt er.", "segments join with spaces, punctuation attaches")
    equal(t.tentative, "", "commit clears tentative")
}

do {
    var t = SpeechTranscript()
    t.commit("Anfang.")
    t.setTentative("ähm")
    let before = t.revision
    check(t.commit("   "), "empty commit withdraws the hypothesis")
    equal(t.committed, "Anfang.", "committed kept")
    equal(t.tentative, "", "tentative cleared")
    equal(t.revision, before + 1, "one revision")
    check(!t.commit(""), "nothing to withdraw")
}

do {
    var t = SpeechTranscript()
    t.commit("Anfang.")
    t.setTentative("und Ende")
    equal(t.text, "Anfang. und Ende", "text joins committed and tentative")
    check(t.commitTentative(), "trailing hypothesis commits")
    equal(t.committed, "Anfang. und Ende", "trailing hypothesis counts")
    check(!t.commitTentative(), "no second commit")
}

// MARK: PartialRestart

check(PartialRestart.isRestart(previous: "Der Patient kommt heute zur Visite", next: "Er sagt"), "short new hypothesis with another first word restarts")
check(!PartialRestart.isRestart(previous: "Der Patient kommt", next: "Der Patient kommt heute"), "growing hypothesis is no restart")
check(!PartialRestart.isRestart(previous: "Der Patient kommt heute zur Visite", next: "Der"), "same first word is a revision")
check(!PartialRestart.isRestart(previous: "Der Patient kommt", next: "Den Patienten"), "similar length is a revision")
check(!PartialRestart.isRestart(previous: "", next: "Hallo"), "nothing before")
check(!PartialRestart.isRestart(previous: "Hallo Welt wie geht es", next: ""), "empty next")

// MARK: PcmFrames

do {
    var frames = PcmFrames(frameBytes: 8)
    var emitted = Data()
    var all = Data()
    var count = 0
    var value: UInt8 = 0
    for size in [3, 5, 1, 7, 8, 16, 2, 6, 0, 8, 4, 4, 96, 24] {
        var chunk = Data()
        for _ in 0..<size {
            chunk.append(value)
            value &+= 1
        }
        all.append(chunk)
        for frame in frames.append(chunk) {
            equal(frame.count, 8, "frame size")
            emitted.append(frame)
            count += 1
        }
    }
    equal(count, all.count / 8, "frame count")
    equal(emitted, all, "frames keep order and bytes")
    check(frames.flush() == nil, "no tail")
}

do {
    var frames = PcmFrames(frameBytes: 4)
    let out = frames.append(Data([0, 1, 2, 3, 4, 5]))
    equal(out, [Data([0, 1, 2, 3])], "one full frame")
    equal(frames.flush(), Data([4, 5]), "tail")
    _ = frames.append(Data([0, 1, 2]))
    equal(frames.flush(), Data([0, 1]), "flush keeps whole samples only")
    check(frames.flush() == nil, "flush resets")
    let sliced = Data([9, 9, 1, 2, 3, 4]).dropFirst(2)
    equal(frames.append(sliced), [Data([1, 2, 3, 4])], "data slices with a non-zero start index")
}

// MARK: RecognitionRestarts

do {
    var r = RecognitionRestarts()
    equal(r.after(.silence), .restart(after: 0), "silence restarts at once")
    equal(r.after(.finished), .restart(after: 0), "a final result restarts at once")
    equal(r.after(.fatal(code: "language-unavailable")), .giveUp(code: "language-unavailable"), "fatal gives up")
    equal(r.after(.transient(code: "service")), .restart(after: 0.25), "first failure")
    equal(r.after(.transient(code: "service")), .restart(after: 0.5), "second failure doubles")
    r.heard()
    equal(r.after(.transient(code: "service")), .restart(after: 0.25), "heard text resets the budget")
    for _ in 0..<4 { _ = r.after(.transient(code: "service")) }
    equal(r.failures, 5, "five failures")
    equal(r.after(.transient(code: "audio")), .giveUp(code: "audio"), "sixth failure in a row gives up")
    var capped = RecognitionRestarts()
    var last = RecognitionRestarts.Decision.restart(after: 0)
    for _ in 0..<5 { last = capped.after(.transient(code: "x")) }
    equal(last, .restart(after: 2), "delay capped")
    equal(capped.after(.silence), .restart(after: 0), "silence never counts")
}

equal(RecognitionEnd.classify(domain: "kAFAssistantErrorDomain", code: 1110), .silence, "no speech")
equal(RecognitionEnd.classify(domain: "kLSRErrorDomain", code: 301), .silence, "cancelled")
equal(RecognitionEnd.classify(domain: "kLSRErrorDomain", code: 102), .fatal(code: "language-unavailable"), "assets missing")
equal(RecognitionEnd.classify(domain: "kLSRErrorDomain", code: 201), .fatal(code: "service-not-allowed"), "dictation off")
equal(RecognitionEnd.classify(domain: "SFSpeechErrorDomain", code: 2), .transient(code: "audio"), "audio read failed")
equal(RecognitionEnd.classify(domain: "NSCocoaErrorDomain", code: 4), .transient(code: "service"), "unknown")

// MARK: LanguageSupport

equal(LanguageSupport.status(analyzer: .installed, legacy: .none), "available", "assets installed")
equal(LanguageSupport.status(analyzer: .downloading, legacy: .onDevice), "downloading", "download running")
equal(LanguageSupport.status(analyzer: .supported, legacy: .onDevice), "downloadable", "SpeechAnalyzer preferred: offer its assets")
equal(LanguageSupport.status(analyzer: nil, legacy: .onDevice), "available", "below iOS 26 on device")
equal(LanguageSupport.status(analyzer: nil, legacy: .serverOnly), "unavailable", "server-only never")
equal(LanguageSupport.status(analyzer: .unsupported, legacy: .none), "unavailable", "nothing")

equal(LanguageSupport.engine(analyzer: .installed, legacy: .onDevice), .success(.analyzer), "analyzer when installed")
equal(LanguageSupport.engine(analyzer: .supported, legacy: .onDevice), .success(.legacy), "legacy stands in for missing assets")
equal(LanguageSupport.engine(analyzer: .supported, legacy: .none), .failure(LanguageError(code: "language-unavailable")), "pack missing")
equal(LanguageSupport.engine(analyzer: nil, legacy: .serverOnly), .failure(LanguageError(code: "language-not-supported")), "no on-device language")

equal(LanguageSupport.normalize("de_de"), "de-DE", "normalize underscore and case")
equal(LanguageSupport.normalize(" "), "de-DE", "empty defaults to German")
equal(LanguageSupport.normalize("zh-Hans-CN"), "zh-Hans-CN", "script kept")

let legacyLocales = ["de-AT", "de-CH", "de-DE", "en-GB", "en-US", "fr-FR"]
equal(LanguageSupport.bestMatch("de-DE", among: legacyLocales), "de-DE", "exact")
equal(LanguageSupport.bestMatch("de_de", among: legacyLocales), "de-DE", "exact after normalizing")
equal(LanguageSupport.bestMatch("de", among: legacyLocales), "de-DE", "bare language → main region")
equal(LanguageSupport.bestMatch("en", among: legacyLocales), "en-US", "bare English → en-US")
equal(LanguageSupport.bestMatch("fr", among: ["fr-CA", "fr-CH"]), "fr-CA", "bare language → any region")
check(LanguageSupport.bestMatch("de-LU", among: legacyLocales) == nil, "region needs that region")
equal(LanguageSupport.bestMatch("de", among: ["de_DE"]), "de_DE", "returns the identifier as given")

// MARK: PcmResampler

func sine(rate: Double, seconds: Double, channels: AVAudioChannelCount = 1) -> AVAudioPCMBuffer {
    let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: channels)!
    let frames = AVAudioFrameCount(rate * seconds)
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
    buffer.frameLength = frames
    for channel in 0..<Int(channels) {
        let data = buffer.floatChannelData![channel]
        for i in 0..<Int(frames) { data[i] = Float(0.5 * sin(2 * Double.pi * 440 * Double(i) / rate)) }
    }
    return buffer
}

do {
    let resampler = PcmResampler(outputFormat: PcmResampler.pcm16k())
    var samples = 0
    var peak: Int16 = 0
    var bytes = 0
    // 1 s of 48 kHz float32 in tap-sized chunks of 4800 frames (100 ms).
    let source = sine(rate: 48_000, seconds: 1)
    for start in stride(from: 0, to: Int(source.frameLength), by: 4800) {
        let chunk = AVAudioPCMBuffer(pcmFormat: source.format, frameCapacity: 4800)!
        chunk.frameLength = 4800
        chunk.floatChannelData![0].update(from: source.floatChannelData![0] + start, count: 4800)
        if let out = resampler.convert(chunk) {
            equal(out.format.commonFormat, .pcmFormatInt16, "int16 output")
            equal(out.format.sampleRate, 16_000, "16 kHz output")
            samples += Int(out.frameLength)
            let data = PcmResampler.int16Data(out)
            bytes += data.count
            for i in 0..<Int(out.frameLength) { peak = max(peak, abs(out.int16ChannelData![0][i])) }
        }
    }
    if let tail = resampler.flush() {
        samples += Int(tail.frameLength)
        bytes += PcmResampler.int16Data(tail).count
    }
    check(abs(samples - 16_000) <= 32, "1 s at 48 kHz → about 16000 samples (got \(samples))")
    equal(bytes, samples * 2, "two bytes per sample")
    check(peak > 15_000 && peak < 17_500, "amplitude kept (0.5 → about 16384, got \(peak))")
}

do {
    // A route change mid-session: the input format switches to 44.1 kHz stereo.
    let resampler = PcmResampler(outputFormat: PcmResampler.pcm16k())
    var samples = 0
    if let out = resampler.convert(sine(rate: 48_000, seconds: 0.5)) { samples += Int(out.frameLength) }
    if let out = resampler.convert(sine(rate: 44_100, seconds: 0.5, channels: 2)) { samples += Int(out.frameLength) }
    if let tail = resampler.flush() { samples += Int(tail.frameLength) }
    check(abs(samples - 16_000) <= 64, "format change rebuilds the converter (got \(samples))")
}

do {
    let resampler = PcmResampler(outputFormat: PcmResampler.pcm16k())
    let empty = AVAudioPCMBuffer(pcmFormat: AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1)!, frameCapacity: 16)!
    check(resampler.convert(empty) == nil, "empty buffer → nothing")
    check(resampler.flush() == nil, "flush before any audio → nothing")
}

print("\(checks - failures)/\(checks) checks passed")
exit(failures == 0 ? 0 : 1)
