import AVFAudio
import Foundation

/// Converts the microphone tap's buffers (the input node's own format, e.g. 48 kHz float32)
/// into one target format: 16 kHz mono int16 for the web app's `pcm` events, or the format
/// `SpeechAnalyzer` asks for. One `AVAudioConverter` lives across buffers, so the resampler
/// keeps its filter state between them (no clicks at buffer edges); it is rebuilt when the
/// input format changes (audio route change, e.g. a headset plugged in mid-dictation).
/// `flush()` drains what the converter still holds at the end of the session.
/// AVFAudio only (no Capacitor, no UIKit): tested on macOS by `ios/App/SpeechTests`.
final class PcmResampler {
    let outputFormat: AVAudioFormat
    private var converter: AVAudioConverter?

    init(outputFormat: AVAudioFormat) {
        self.outputFormat = outputFormat
    }

    /// 16 kHz mono little-endian int16, the format of the `pcm` events.
    static func pcm16k() -> AVAudioFormat {
        AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)!
    }

    /// The converted audio of `buffer`, or nil when the converter produced nothing yet (it
    /// may hold a few samples back) or the formats cannot be converted.
    func convert(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard buffer.frameLength > 0, let converter = converter(for: buffer.format) else { return nil }
        let ratio = outputFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 1024
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return nil }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            if supplied {
                // Not the end of the stream: the converter keeps its state for the next buffer.
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return buffer
        }
        if status == .error || output.frameLength == 0 { return nil }
        return output
    }

    /// The samples the converter still holds; it starts over afterwards.
    func flush() -> AVAudioPCMBuffer? {
        guard let converter else { return nil }
        defer { converter.reset() }
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: 4096) else { return nil }
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            inputStatus.pointee = .endOfStream
            return nil
        }
        if status == .error || output.frameLength == 0 { return nil }
        return output
    }

    private func converter(for input: AVAudioFormat) -> AVAudioConverter? {
        if let converter, converter.inputFormat == input { return converter }
        let next = AVAudioConverter(from: input, to: outputFormat)
        next?.downmix = true
        next?.sampleRateConverterQuality = AVAudioQuality.high.rawValue
        converter = next
        return next
    }

    /// The bytes of an int16 buffer (first channel; the target formats are mono), in the
    /// CPU's byte order, which is little-endian on every Apple device.
    static func int16Data(_ buffer: AVAudioPCMBuffer) -> Data {
        guard buffer.format.commonFormat == .pcmFormatInt16, let channel = buffer.int16ChannelData else { return Data() }
        return Data(bytes: channel[0], count: Int(buffer.frameLength) * MemoryLayout<Int16>.size)
    }
}
