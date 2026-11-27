import AVFAudio
import Foundation
import Speech

/// One live dictation on the iPhone: an `AVAudioEngine` input tap feeds two consumers.
/// (1) The on-device recognizer (`AnalyzerPreview` on iOS 26+, `LegacyPreview` below): its
/// results become the live preview (`SpeechTranscript`). (2) The web app, as `pcm` frames
/// (16 kHz mono int16, 250 ms), which the hub correction re-transcribes and which become the
/// saved audio. The app's audio session (`.playAndRecord`) and `UIBackgroundModes: audio`
/// keep the engine, and so the session, running while the screen is locked.
///
/// Threads: the tap calls `deliver` on the audio engine's thread; recognizer callbacks
/// arrive on the main queue (legacy) or a results task (analyzer). `lock` guards the
/// transcript, the PCM path and the recognizer handle, so events leave in order.
///
/// Same structure as the Android `LiveSpeechSession.java`, which is ported from T3 Code's
/// voice module (pingdotgg/t3code@b70b9e3, MIT); no Capacitor or UIKit here (the iOS audio
/// session parts are behind `#if os(iOS)`), so it typechecks on macOS.
final class LiveSpeechSession: @unchecked Sendable {
    struct Events {
        let update: (_ committed: String, _ tentative: String, _ revision: Int) -> Void
        let pcm: (_ frame: Data) -> Void
        /// `fatal`: the microphone stopped, so the session cannot go on.
        let error: (_ code: String, _ message: String, _ fatal: Bool) -> Void
    }

    struct StartError: Error {
        let code: String
        let message: String
    }

    /// One `pcm` event: 250 ms of 16 kHz int16.
    static let frameBytes = 8000
    static let stopTimeout: TimeInterval = 3
    static let tapFrames: AVAudioFrameCount = 4096

    private let choice: SpeechSupport.Choice
    private let emitPcm: Bool
    private let events: Events
    private let lock = NSLock()
    private let engine = AVAudioEngine()
    private let pcmResampler = PcmResampler(outputFormat: PcmResampler.pcm16k())
    private var frames = PcmFrames(frameBytes: LiveSpeechSession.frameBytes)
    private var transcript = SpeechTranscript()
    private var recognizer: PreviewRecognizer?
    private var capturing = false
    private var stopped = false
    private var observers: [NSObjectProtocol] = []

    init(choice: SpeechSupport.Choice, emitPcm: Bool, events: Events) {
        self.choice = choice
        self.emitPcm = emitPcm
        self.events = events
    }

    /// Opens the microphone and the recognizer. Throws `StartError` when either cannot start.
    func start() async throws {
        let preview = try await makeRecognizer()
        guard attach(preview) else {
            // stop() or abort() came first: never open the microphone.
            preview.cancel()
            throw StartError(code: "aborted", message: "Die Aufnahme wurde beendet, bevor sie begann.")
        }
        do {
            #if os(iOS)
            try AudioSessionControl.activate()
            #endif
            try startCapture()
        }
        catch {
            preview.cancel()
            attach(nil)
            #if os(iOS)
            AudioSessionControl.deactivate()
            #endif
            throw StartError(code: "audio-capture", message: "Das Mikrofon ließ sich nicht starten (\(error.localizedDescription)).")
        }
        observe()
    }

    /// Ends the session: stops the microphone (the last PCM frame goes out before this
    /// returns), lets the recognizer finish the audio it has, commits a trailing hypothesis
    /// and returns the whole on-device text.
    func stop() async -> String {
        let preview = endCapture()
        if let preview {
            await Self.bounded(Self.stopTimeout) { await preview.finish() }
            preview.cancel()
        }
        let text = closeTranscript()
        #if os(iOS)
        AudioSessionControl.deactivate()
        #endif
        return text
    }

    /// Drops everything at once (a new session replaces this one).
    func abort() {
        let preview = endCapture()
        preview?.cancel()
        attach(nil)
        #if os(iOS)
        AudioSessionControl.deactivate()
        #endif
    }

    /// Sets the recognizer; false (and nothing set) when the session already ended.
    @discardableResult
    private func attach(_ preview: PreviewRecognizer?) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if preview != nil && stopped { return false }
        recognizer = preview
        return true
    }

    /// Drops the recognizer, commits a trailing hypothesis and returns the whole text.
    private func closeTranscript() -> String {
        lock.lock()
        defer { lock.unlock() }
        recognizer = nil
        stopped = true
        if transcript.commitTentative() { emitUpdateLocked() }
        return transcript.text
    }

    // MARK: capture

    private func startCapture() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw StartError(code: "audio-capture", message: "Kein Mikrofon")
        }
        lock.lock()
        capturing = true
        lock.unlock()
        input.installTap(onBus: 0, bufferSize: Self.tapFrames, format: format) { [weak self] buffer, _ in
            self?.deliver(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        }
        catch {
            input.removeTap(onBus: 0)
            lock.lock()
            capturing = false
            lock.unlock()
            throw error
        }
    }

    /// Stops the microphone and flushes the PCM tail. Returns the recognizer to finish, or nil
    /// when the session had already ended.
    private func endCapture() -> PreviewRecognizer? {
        lock.lock()
        if stopped {
            lock.unlock()
            return nil
        }
        stopped = true
        capturing = false
        lock.unlock()
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers = []
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        lock.lock()
        defer { lock.unlock() }
        if emitPcm {
            if let tail = pcmResampler.flush() { emitPcmLocked(PcmResampler.int16Data(tail)) }
            if let last = frames.flush() { events.pcm(last) }
        }
        recognizer?.endAudio()
        return recognizer
    }

    private func deliver(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        defer { lock.unlock() }
        guard capturing else { return }
        if emitPcm, let converted = pcmResampler.convert(buffer) {
            emitPcmLocked(PcmResampler.int16Data(converted))
        }
        recognizer?.append(buffer)
    }

    private func emitPcmLocked(_ data: Data) {
        for frame in frames.append(data) { events.pcm(frame) }
    }

    /// The input format changes with the audio route (headset, Bluetooth): the engine stops
    /// and the tap must be installed again with the new format. The resamplers follow it.
    private func reconfigure() {
        lock.lock()
        let live = capturing
        lock.unlock()
        guard live else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        do {
            lock.lock()
            capturing = false
            lock.unlock()
            try startCapture()
        }
        catch {
            microphoneLost("Das Mikrofon ließ sich nach dem Wechsel des Audiogeräts nicht wieder starten.")
        }
    }

    private func microphoneLost(_ message: String) {
        lock.lock()
        let live = !stopped
        capturing = false
        lock.unlock()
        if live { events.error("interrupted", message, true) }
    }

    private func observe() {
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil) { [weak self] _ in
            self?.reconfigure()
        })
        #if os(iOS)
        observers.append(center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: nil) { [weak self] note in
            guard
                let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                AVAudioSession.InterruptionType(rawValue: raw) == .began
            else { return }
            self?.microphoneLost("Die Aufnahme wurde unterbrochen (Anruf oder eine andere App).")
        })
        observers.append(center.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: nil) { [weak self] _ in
            self?.microphoneLost("Die Audiodienste von iOS wurden neu gestartet.")
        })
        #endif
    }

    // MARK: recognizer

    private func makeRecognizer() async throws -> PreviewRecognizer {
        let callbacks = PreviewCallbacks(
            partial: { [weak self] text in self?.recognized(text, final: false) },
            final: { [weak self] text in self?.recognized(text, final: true) },
            giveUp: { [weak self] code, message in self?.events.error(code, message, false) }
        )
        switch choice.engine {
        case .analyzer:
            guard #available(iOS 26, macOS 26, *) else {
                throw StartError(code: "unsupported", message: "SpeechAnalyzer braucht iOS 26.")
            }
            let preview = AnalyzerPreview(locale: choice.locale, callbacks: callbacks)
            do {
                try await preview.prepare()
            }
            catch {
                throw StartError(code: "language-unavailable", message: "Die lokale Spracherkennung ließ sich nicht starten (\(error.localizedDescription)).")
            }
            return preview
        case .legacy:
            guard let preview = LegacyPreview(locale: choice.locale, callbacks: callbacks) else {
                throw StartError(code: "language-not-supported", message: "Die lokale Spracherkennung kann diese Sprache nicht.")
            }
            await MainActor.run { preview.begin() }
            return preview
        }
    }

    private func recognized(_ text: String, final: Bool) {
        lock.lock()
        defer { lock.unlock() }
        let changed = final ? transcript.commit(text) : transcript.setTentative(text)
        if changed { emitUpdateLocked() }
    }

    private func emitUpdateLocked() {
        events.update(transcript.committed, transcript.tentative, transcript.revision)
    }

    /// Runs `operation` for at most `seconds`; returns when it finished or time ran out.
    static func bounded(_ seconds: TimeInterval, _ operation: @escaping () async -> Void) async {
        let once = Once()
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            Task {
                await operation()
                if once.claim() { continuation.resume() }
            }
            Task {
                try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
                if once.claim() { continuation.resume() }
            }
        }
    }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var done = false

        func claim() -> Bool {
            lock.lock()
            defer { lock.unlock() }
            if done { return false }
            done = true
            return true
        }
    }
}

/// How a recognizer reports to its session.
struct PreviewCallbacks {
    let partial: (String) -> Void
    let final: (String) -> Void
    /// The preview stopped for good (the PCM keeps flowing).
    let giveUp: (_ code: String, _ message: String) -> Void
}

/// The on-device recognizer behind the preview. `append` and `endAudio` are called under the
/// session's lock from the tap thread; they must not block.
protocol PreviewRecognizer: AnyObject {
    func append(_ buffer: AVAudioPCMBuffer)
    /// No more audio comes.
    func endAudio()
    /// Waits until the recognizer has reported its last result (the caller bounds the wait).
    func finish() async
    func cancel()
}

/// iOS 26+: `SpeechAnalyzer` with a `SpeechTranscriber` (`reportingOptions: [.volatileResults]`).
/// Volatile results are the tentative text, finalized results are committed. One analysis for
/// the whole dictation; the tap's audio is converted to the analyzer's format and streamed in.
@available(iOS 26, macOS 26, *)
final class AnalyzerPreview: PreviewRecognizer {
    private let locale: Locale
    private let callbacks: PreviewCallbacks
    private let transcriber: SpeechTranscriber
    private let analyzer: SpeechAnalyzer
    private let input: AsyncStream<AnalyzerInput>
    private let continuation: AsyncStream<AnalyzerInput>.Continuation
    private var resampler: PcmResampler?
    private var results: Task<Void, Never>?

    init(locale: Locale, callbacks: PreviewCallbacks) {
        self.locale = locale
        self.callbacks = callbacks
        transcriber = AnalyzerAssets.transcriber(for: locale)
        analyzer = SpeechAnalyzer(modules: [transcriber])
        (input, continuation) = AsyncStream<AnalyzerInput>.makeStream(bufferingPolicy: .bufferingNewest(256))
    }

    func prepare() async throws {
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw LanguageError(code: "language-unavailable")
        }
        resampler = PcmResampler(outputFormat: format)
        let transcriber = self.transcriber
        let callbacks = self.callbacks
        results = Task {
            do {
                for try await result in transcriber.results {
                    let text = String(result.text.characters)
                    if result.isFinal {
                        callbacks.final(text)
                    }
                    else {
                        callbacks.partial(text)
                    }
                }
            }
            catch is CancellationError {
                // cancel()
            }
            catch {
                callbacks.giveUp("service", "Die lokale Vorschau ist ausgefallen (\(error.localizedDescription)).")
            }
        }
        try await analyzer.prepareToAnalyze(in: format)
        try await analyzer.start(inputSequence: input)
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        guard let converted = resampler?.convert(buffer) else { return }
        continuation.yield(AnalyzerInput(buffer: converted))
    }

    func endAudio() {
        if let tail = resampler?.flush() { continuation.yield(AnalyzerInput(buffer: tail)) }
        continuation.finish()
    }

    func finish() async {
        try? await analyzer.finalizeAndFinishThroughEndOfInput()
        await results?.value
    }

    func cancel() {
        continuation.finish()
        results?.cancel()
        let analyzer = self.analyzer
        Task { await analyzer.cancelAndFinishNow() }
    }
}

/// Below iOS 26 (and when the SpeechAnalyzer assets are missing): `SFSpeechRecognizer` with
/// `requiresOnDeviceRecognition`. One recognition task reports a growing hypothesis
/// (tentative) and ends with a final result (committed); the next task starts at once, so the
/// preview covers the whole dictation (`RecognitionRestarts`). Callbacks run on the main queue.
final class LegacyPreview: PreviewRecognizer, @unchecked Sendable {
    private let recognizer: SFSpeechRecognizer
    private let callbacks: PreviewCallbacks
    private let lock = NSLock()
    // Guarded by `lock` (tap thread and main queue).
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var ending = false
    // Main queue only.
    private var task: SFSpeechRecognitionTask?
    private var generation = 0
    private var hypothesis = ""
    private var restarts = RecognitionRestarts()
    private var gaveUp = false
    private var done: CheckedContinuation<Void, Never>?
    private var over = false

    init?(locale: Locale, callbacks: PreviewCallbacks) {
        guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.supportsOnDeviceRecognition else { return nil }
        recognizer.queue = .main
        self.recognizer = recognizer
        self.callbacks = callbacks
    }

    /// Starts a recognition task. Call on the main queue (or before any callback can run).
    func begin() {
        lock.lock()
        let finishing = ending
        lock.unlock()
        if finishing || gaveUp {
            settle()
            return
        }
        let next = SFSpeechAudioBufferRecognitionRequest()
        next.shouldReportPartialResults = true
        next.requiresOnDeviceRecognition = true
        if #available(iOS 16, macOS 13, *) {
            next.addsPunctuation = true
        }
        lock.lock()
        request = next
        lock.unlock()
        generation += 1
        hypothesis = ""
        let current = generation
        task = recognizer.recognitionTask(with: next) { [weak self] result, error in
            self?.handle(current, result, error)
        }
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        let current = request
        lock.unlock()
        current?.append(buffer)
    }

    func endAudio() {
        lock.lock()
        ending = true
        let last = request
        lock.unlock()
        last?.endAudio()
    }

    func finish() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.main.async {
                if self.over || self.task == nil || self.gaveUp {
                    continuation.resume()
                }
                else {
                    self.done = continuation
                }
            }
        }
    }

    func cancel() {
        DispatchQueue.main.async {
            self.lock.lock()
            self.ending = true
            self.request = nil
            self.lock.unlock()
            self.task?.cancel()
            self.task = nil
            self.settle()
        }
    }

    private func handle(_ current: Int, _ result: SFSpeechRecognitionResult?, _ error: Error?) {
        guard current == generation, task != nil else { return }
        if let result {
            let text = result.bestTranscription.formattedString
            if !SpeechTranscript.clean(text).isEmpty { restarts.heard() }
            if result.isFinal {
                hypothesis = ""
                callbacks.final(text)
                ended(.finished)
                return
            }
            if PartialRestart.isRestart(previous: hypothesis, next: text) {
                callbacks.final(hypothesis)
            }
            hypothesis = text
            callbacks.partial(text)
            return
        }
        if let error {
            let ns = error as NSError
            ended(RecognitionEnd.classify(domain: ns.domain, code: ns.code))
        }
    }

    private func ended(_ end: RecognitionEnd) {
        task = nil
        lock.lock()
        request = nil
        let finishing = ending
        lock.unlock()
        if finishing {
            // A hypothesis the task never finalized still counts (the session commits it).
            settle()
            return
        }
        switch restarts.after(end) {
        case .restart(let delay):
            if !hypothesis.isEmpty {
                callbacks.final(hypothesis)
                hypothesis = ""
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.begin() }
        case .giveUp(let code):
            gaveUp = true
            callbacks.giveUp(code, Self.message(code))
            settle()
        }
    }

    private func settle() {
        over = true
        done?.resume()
        done = nil
    }

    private static func message(_ code: String) -> String {
        switch code {
        case "language-unavailable": return "Das Sprachpaket für die lokale Erkennung fehlt."
        case "service-not-allowed": return "Siri und Diktierfunktion sind ausgeschaltet; die lokale Erkennung braucht sie."
        default: return "Die lokale Vorschau ist ausgefallen (\(code))."
        }
    }
}

#if os(iOS)
/// The app's audio session while a live dictation runs: record (and play, so the web view's
/// sounds keep working), speaker instead of receiver, Bluetooth headsets allowed. With
/// `UIBackgroundModes: audio` an active recording session keeps the app running when the
/// screen locks.
enum AudioSessionControl {
    static func activate() throws {
        let session = AVAudioSession.sharedInstance()
        var options: AVAudioSession.CategoryOptions = [.defaultToSpeaker]
        if #available(iOS 26, *) {
            options.insert(.allowBluetoothHFP)
        }
        else {
            options.insert(.allowBluetooth)
        }
        try session.setCategory(.playAndRecord, mode: .default, options: options)
        try session.setActive(true)
    }

    static func deactivate() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
#endif
