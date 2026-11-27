import Foundation

/// Regroups converted microphone audio (any size) into frames of a fixed number of bytes of
/// 16-bit PCM for the web app's `pcm` events (250 ms at 16 kHz = 8000 bytes), so it gets a
/// steady few events per second whatever the tap's buffer size. A lone odd byte waits for its
/// sample's other half; `flush()` hands out the short tail at the end. Port of the Android
/// `PcmFrames.java`. Foundation only: tested by `ios/App/SpeechTests` without Xcode.
struct PcmFrames {
    let frameBytes: Int
    private var pending = Data()

    init(frameBytes: Int) {
        precondition(frameBytes > 0 && frameBytes % 2 == 0, "frameBytes must be a positive even number")
        self.frameBytes = frameBytes
        pending.reserveCapacity(frameBytes)
    }

    /// Appends `data`; returns the frames that became complete.
    mutating func append(_ data: Data) -> [Data] {
        var done: [Data] = []
        var offset = data.startIndex
        while offset < data.endIndex {
            let take = min(data.endIndex - offset, frameBytes - pending.count)
            pending.append(data[offset..<(offset + take)])
            offset += take
            if pending.count == frameBytes {
                done.append(pending)
                pending = Data()
                pending.reserveCapacity(frameBytes)
            }
        }
        return done
    }

    /// The buffered tail (whole samples only), or nil when there is none. Resets the buffer.
    mutating func flush() -> Data? {
        let whole = pending.count - pending.count % 2
        let tail = whole == 0 ? nil : Data(pending.prefix(whole))
        pending = Data()
        return tail
    }
}
