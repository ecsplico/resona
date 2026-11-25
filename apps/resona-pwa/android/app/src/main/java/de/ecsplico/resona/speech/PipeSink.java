package de.ecsplico.resona.speech;

import android.os.ParcelFileDescriptor;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;

import java.io.IOException;

/**
 * The write end of the pipe a recognition reads through {@code EXTRA_AUDIO_SOURCE}.
 * Non-blocking: when the recognizer stops reading, a chunk gives up after
 * {@link #WRITE_TIMEOUT_MS} instead of hanging the feed thread (T3 Code's {@code PcmPipe},
 * pingdotgg/t3code, MIT). Write and close share a lock, so a close never races a write on
 * a descriptor number that could already be reused.
 */
final class PipeSink implements AudioFeed.Sink {

    static final long WRITE_TIMEOUT_MS = 500;

    private final ParcelFileDescriptor writer;
    private boolean closed = false;

    PipeSink(ParcelFileDescriptor writer) throws ErrnoException {
        this.writer = writer;
        Os.fcntlInt(writer.getFileDescriptor(), OsConstants.F_SETFL, OsConstants.O_NONBLOCK);
    }

    @Override
    public synchronized void write(byte[] data, int offset, int count) throws IOException {
        long deadline = System.currentTimeMillis() + WRITE_TIMEOUT_MS;
        int done = 0;
        while (done < count) {
            if (closed) throw new IOException("pipe closed");
            try {
                int written = Os.write(writer.getFileDescriptor(), data, offset + done, count - done);
                if (written <= 0) throw new IOException("recognizer stopped reading");
                done += written;
            }
            catch (ErrnoException e) {
                if (e.errno != OsConstants.EAGAIN) throw new IOException("recognizer stopped reading", e);
                if (System.currentTimeMillis() > deadline) throw new IOException("recognizer fell behind", e);
                try {
                    wait(10);
                }
                catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw new IOException("interrupted", interrupted);
                }
            }
        }
    }

    @Override
    public synchronized void close() {
        if (closed) return;
        closed = true;
        try {
            writer.close();
        }
        catch (IOException ignored) {
            // Already gone.
        }
    }
}
