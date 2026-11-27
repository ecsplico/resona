package de.ecsplico.resona.speech;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.Test;

public class AudioFeedTest {

    private static final class Sink implements AudioFeed.Sink {
        final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        final CountDownLatch closed = new CountDownLatch(1);
        final CountDownLatch gate;
        final CountDownLatch writing = new CountDownLatch(1);
        boolean failing = false;

        Sink(CountDownLatch gate) {
            this.gate = gate;
        }

        @Override
        public void write(byte[] data, int offset, int count) throws IOException {
            writing.countDown();
            if (gate != null) {
                try {
                    gate.await();
                }
                catch (InterruptedException e) {
                    throw new IOException(e);
                }
            }
            if (failing) throw new IOException("reader gone");
            synchronized (bytes) {
                bytes.write(data, offset, count);
            }
        }

        @Override
        public void close() {
            closed.countDown();
        }

        byte[] written() {
            synchronized (bytes) {
                return bytes.toByteArray();
            }
        }
    }

    @Test
    public void writesChunksInOrderAndClosesTheSinkOnFinish() throws Exception {
        AudioFeed feed = new AudioFeed(10);
        Sink sink = new Sink(null);
        feed.attach(sink);
        assertTrue(feed.offer(new byte[] { 1, 2 }));
        assertTrue(feed.offer(new byte[] { 3, 4 }));
        assertTrue(feed.offer(new byte[] { 5, 6 }));
        feed.finish();
        assertTrue(feed.awaitFinished(2000));
        assertTrue(sink.closed.await(1, TimeUnit.SECONDS));
        assertArrayEquals(new byte[] { 1, 2, 3, 4, 5, 6 }, sink.written());
        assertEquals(0, feed.droppedBytes());
    }

    @Test
    public void dropsAudioWhileNoRecognitionReadsAndResumesOnTheNextSink() throws Exception {
        AudioFeed feed = new AudioFeed(10);
        Sink first = new Sink(null);
        feed.attach(first);
        feed.offer(new byte[] { 1, 2 });
        // A restart: the old pipe closes, the next one is attached later.
        waitFor(() -> first.written().length == 2);
        feed.detach();
        assertTrue(first.closed.await(1, TimeUnit.SECONDS));
        feed.offer(new byte[] { 3, 4 });
        waitFor(() -> feed.droppedBytes() == 2);
        Sink second = new Sink(null);
        feed.attach(second);
        feed.offer(new byte[] { 5, 6 });
        feed.finish();
        assertTrue(feed.awaitFinished(2000));
        assertArrayEquals(new byte[] { 1, 2 }, first.written());
        assertArrayEquals(new byte[] { 5, 6 }, second.written());
    }

    @Test
    public void aSinkThatFailsIsClosedAndDropped() throws Exception {
        AudioFeed feed = new AudioFeed(10);
        Sink sink = new Sink(null);
        sink.failing = true;
        feed.attach(sink);
        feed.offer(new byte[] { 1, 2, 3, 4 });
        assertTrue(sink.closed.await(1, TimeUnit.SECONDS));
        feed.offer(new byte[] { 5, 6 });
        feed.finish();
        assertTrue(feed.awaitFinished(2000));
        assertEquals(6, feed.droppedBytes());
    }

    @Test
    public void aSlowRecognizerNeverBlocksTheMicrophone() throws Exception {
        CountDownLatch gate = new CountDownLatch(1);
        AudioFeed feed = new AudioFeed(2);
        Sink sink = new Sink(gate);
        feed.attach(sink);
        assertTrue(feed.offer(new byte[] { 1, 2 }));
        assertTrue(sink.writing.await(1, TimeUnit.SECONDS));
        // The writer is stuck on chunk 1; the queue takes two more, then refuses at once.
        assertTrue(feed.offer(new byte[] { 3, 4 }));
        assertTrue(feed.offer(new byte[] { 5, 6 }));
        assertFalse(feed.offer(new byte[] { 7, 8 }));
        assertEquals(2, feed.droppedBytes());
        gate.countDown();
        feed.finish();
        assertTrue(feed.awaitFinished(2000));
        assertArrayEquals(new byte[] { 1, 2, 3, 4, 5, 6 }, sink.written());
    }

    @Test
    public void shutdownClosesTheSink() throws Exception {
        AudioFeed feed = new AudioFeed(10);
        Sink sink = new Sink(null);
        feed.attach(sink);
        feed.shutdown();
        assertTrue(sink.closed.await(1, TimeUnit.SECONDS));
        assertTrue(feed.awaitFinished(2000));
    }

    private interface Condition {
        boolean holds();
    }

    private static void waitFor(Condition condition) throws InterruptedException {
        long deadline = System.currentTimeMillis() + 2000;
        while (!condition.holds()) {
            if (System.currentTimeMillis() > deadline) throw new AssertionError("condition not reached");
            Thread.sleep(5);
        }
    }
}
