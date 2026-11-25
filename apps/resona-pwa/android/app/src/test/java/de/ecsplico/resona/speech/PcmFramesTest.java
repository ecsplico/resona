package de.ecsplico.resona.speech;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import java.io.ByteArrayOutputStream;
import java.util.List;

import org.junit.Test;

public class PcmFramesTest {

    private static byte[] ramp(int length, int start) {
        byte[] out = new byte[length];
        for (int i = 0; i < length; i++) out[i] = (byte) (start + i);
        return out;
    }

    @Test
    public void groupsReadsIntoFixedFramesInOrder() {
        PcmFrames frames = new PcmFrames(8);
        ByteArrayOutputStream all = new ByteArrayOutputStream();
        ByteArrayOutputStream emitted = new ByteArrayOutputStream();
        int next = 0;
        for (int size : new int[] { 3, 6, 1, 10, 4 }) {
            byte[] read = ramp(size, next);
            next += size;
            all.write(read, 0, size);
            for (byte[] frame : frames.append(read, size)) {
                assertEquals(8, frame.length);
                emitted.write(frame, 0, frame.length);
            }
        }
        // 24 bytes in: three whole frames out, nothing left.
        assertEquals(24, emitted.size());
        assertArrayEquals(all.toByteArray(), emitted.toByteArray());
        assertNull(frames.flush());
    }

    @Test
    public void onlyCountsTheGivenBytesOfARead() {
        PcmFrames frames = new PcmFrames(4);
        byte[] buffer = ramp(10, 0);
        List<byte[]> out = frames.append(buffer, 6);
        assertEquals(1, out.size());
        assertArrayEquals(new byte[] { 0, 1, 2, 3 }, out.get(0));
        assertArrayEquals(new byte[] { 4, 5 }, frames.flush());
    }

    @Test
    public void flushKeepsWholeSamplesOnly() {
        PcmFrames frames = new PcmFrames(8);
        frames.append(ramp(5, 0), 5);
        assertArrayEquals(new byte[] { 0, 1, 2, 3 }, frames.flush());
        assertNull(frames.flush());
    }

    @Test
    public void framesAreIndependentCopies() {
        PcmFrames frames = new PcmFrames(2);
        List<byte[]> first = frames.append(new byte[] { 1, 2 }, 2);
        frames.append(new byte[] { 3, 4 }, 2);
        assertArrayEquals(new byte[] { 1, 2 }, first.get(0));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsFramesThatSplitSamples() {
        new PcmFrames(7);
    }
}
