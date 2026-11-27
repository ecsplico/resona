package de.ecsplico.resona.speech;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Regroups microphone reads (any size) into frames of a fixed number of bytes of 16-bit
 * PCM for the web app's {@code pcm} events, so it gets a steady few events per second
 * whatever the recorder's read size. A lone odd byte waits for its sample's other half;
 * {@link #flush()} hands out the short tail at the end. Pure Java: unit-tested without Android.
 */
public final class PcmFrames {

    private final byte[] frame;
    private int filled = 0;

    public PcmFrames(int frameBytes) {
        if (frameBytes <= 0 || frameBytes % 2 != 0) {
            throw new IllegalArgumentException("frameBytes must be a positive even number");
        }
        frame = new byte[frameBytes];
    }

    /** Appends {@code count} bytes of {@code data}; returns the frames that became complete. */
    public List<byte[]> append(byte[] data, int count) {
        List<byte[]> done = new ArrayList<>();
        int offset = 0;
        while (offset < count) {
            int take = Math.min(count - offset, frame.length - filled);
            System.arraycopy(data, offset, frame, filled, take);
            filled += take;
            offset += take;
            if (filled == frame.length) {
                done.add(frame.clone());
                filled = 0;
            }
        }
        return done;
    }

    /** The buffered tail (whole samples only), or null when there is none. Resets the buffer. */
    public byte[] flush() {
        int whole = filled - (filled % 2);
        filled = 0;
        return whole == 0 ? null : Arrays.copyOf(frame, whole);
    }
}
