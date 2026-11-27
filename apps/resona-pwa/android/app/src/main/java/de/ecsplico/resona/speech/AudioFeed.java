package de.ecsplico.resona.speech;

import java.io.IOException;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Hands captured PCM to the speech recognizer on its own thread, so a recognizer that
 * reads slowly never stalls the microphone (whose PCM also goes to the web app for the
 * hub correction). The sink is the write end of the pipe the current recognition reads
 * ({@code EXTRA_AUDIO_SOURCE}); a restarted recognition attaches a new one. Chunks that
 * arrive while no sink is attached, overflow the queue or fail to write are dropped and
 * counted: the on-device text is a preview, the hub correction gets every sample.
 * {@link #finish()} writes what is queued and then closes the sink, which ends the
 * recognition (end of stream).
 *
 * <p>Idea from T3 Code's {@code LivePcmCapture} / {@code PcmPipe} (pingdotgg/t3code, MIT):
 * capture and recognizer input decoupled by a bounded channel. Pure Java: unit-tested
 * without Android.
 */
public final class AudioFeed {

    /** Where queued PCM goes. {@code write} may block briefly; {@code close} is idempotent. */
    public interface Sink {
        void write(byte[] data, int offset, int count) throws IOException;

        void close();
    }

    private static final byte[] END = new byte[0];

    private final ArrayBlockingQueue<byte[]> queue;
    private final Thread thread;
    private final AtomicLong dropped = new AtomicLong();
    private Sink sink;

    public AudioFeed(int capacityChunks) {
        queue = new ArrayBlockingQueue<>(capacityChunks + 1);
        thread = new Thread(this::run, "resona-speech-feed");
        thread.setDaemon(true);
        thread.start();
    }

    /** Queues one chunk; false (and counted as dropped) when the queue is full. */
    public boolean offer(byte[] chunk) {
        if (queue.remainingCapacity() <= 1 || !queue.offer(chunk)) {
            dropped.addAndGet(chunk.length);
            return false;
        }
        return true;
    }

    /** Replaces the sink; the previous one is closed. */
    public void attach(Sink next) {
        Sink old;
        synchronized (this) {
            old = sink;
            sink = next;
        }
        if (old != null) old.close();
    }

    /** Closes the current sink; later chunks are dropped until the next {@link #attach}. */
    public void detach() {
        Sink old;
        synchronized (this) {
            old = sink;
            sink = null;
        }
        if (old != null) old.close();
    }

    /** No more audio: write what is queued, then close the sink. */
    public void finish() {
        try {
            if (!queue.offer(END, 1, TimeUnit.SECONDS)) {
                thread.interrupt();
            }
        }
        catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            thread.interrupt();
        }
    }

    /** Waits for {@link #finish()} to complete; true when it did within {@code millis}. */
    public boolean awaitFinished(long millis) throws InterruptedException {
        thread.join(millis);
        return !thread.isAlive();
    }

    /** Stops at once, dropping what is queued. */
    public void shutdown() {
        thread.interrupt();
        detach();
    }

    public long droppedBytes() {
        return dropped.get();
    }

    private void run() {
        try {
            while (true) {
                byte[] chunk = queue.take();
                if (chunk == END) break;
                Sink target;
                synchronized (this) {
                    target = sink;
                }
                if (target == null) {
                    dropped.addAndGet(chunk.length);
                    continue;
                }
                try {
                    target.write(chunk, 0, chunk.length);
                }
                catch (IOException e) {
                    // The recognizer stopped reading (session ended or failed): wait for the next sink.
                    dropped.addAndGet(chunk.length);
                    synchronized (this) {
                        if (sink == target) sink = null;
                    }
                    target.close();
                }
            }
        }
        catch (InterruptedException ignored) {
            // shutdown()
        }
        finally {
            detach();
        }
    }
}
