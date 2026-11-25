package de.ecsplico.resona.speech;

import android.annotation.SuppressLint;
import android.content.Context;
import android.content.Intent;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;

import androidx.annotation.RequiresApi;

import java.io.IOException;
import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * One live dictation on the phone: this app's own {@link AudioRecord} (16 kHz mono PCM16)
 * feeds two consumers. (1) The on-device {@link SpeechRecognizer}
 * ({@code createOnDeviceSpeechRecognizer}) through a pipe ({@code EXTRA_AUDIO_SOURCE},
 * API 33) in a segmented session: its partial and segment results become the live preview
 * ({@link SpeechTranscript}). (2) The web app, as {@code pcm} frames, which the hub
 * correction re-transcribes and which become the saved audio. Because the app captures
 * the audio itself (not the recognizer), it runs on while the screen is locked as long as
 * the microphone foreground service is up (the web app starts it before {@code start()}).
 *
 * <p>Threads: capture on {@code resona-speech-capture}, recognizer input on the
 * {@link AudioFeed} thread, every {@link SpeechRecognizer} call and callback on the main
 * looper (the API requires it). {@link #stop()} blocks its caller (never the main thread).
 *
 * <p>Structure ported from T3 Code's Android voice module ({@code LiveVoiceRecorder},
 * {@code LivePcmCapture}, {@code PcmPipe}, {@code GoogleVoiceTranscriber};
 * pingdotgg/t3code@b70b9e3, MIT), which drives ML Kit GenAI speech; this one drives the
 * platform recognizer and streams instead of transcribing after stop.
 */
@RequiresApi(Build.VERSION_CODES.TIRAMISU)
public final class LiveSpeechSession {

    public interface Listener {
        void onUpdate(String committed, String tentative, int revision);

        void onPcm(byte[] frame);

        /** {@code fatal}: the microphone stopped, so the session cannot go on. */
        void onError(String code, String message, boolean fatal);
    }

    public static final int SAMPLE_RATE = 16000;
    /** One microphone read: 100 ms. */
    static final int READ_BYTES = 3200;
    /** One {@code pcm} event: 250 ms. */
    static final int FRAME_BYTES = 8000;
    /** Recognizer input queue: 10 s of reads. */
    static final int FEED_CHUNKS = 100;
    static final long STOP_TIMEOUT_MS = 3000;

    private static final String TAG = "ResonaSpeech";

    private final Context context;
    private final String language;
    private final boolean emitPcm;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final SpeechTranscript transcript = new SpeechTranscript();
    private final RecognitionRestarts restarts = new RecognitionRestarts();
    private final PcmFrames frames = new PcmFrames(FRAME_BYTES);
    private final AudioFeed feed = new AudioFeed(FEED_CHUNKS);
    private final CountDownLatch recognitionEnded = new CountDownLatch(1);
    private final Runnable restart = this::startRecognition;

    private AudioRecord recorder;
    private Thread captureThread;
    private volatile boolean stopping = false;
    private volatile boolean captureEnded = false;

    // Main looper only.
    private SpeechRecognizer recognizer;
    private ParcelFileDescriptor readEnd;
    private boolean listening = false;
    private boolean previewDead = false;

    public LiveSpeechSession(Context context, String language, boolean emitPcm, Listener listener) {
        this.context = context.getApplicationContext();
        this.language = language;
        this.emitPcm = emitPcm;
        this.listener = listener;
    }

    /** Opens the microphone and the recognizer. Throws when the microphone cannot start. */
    @SuppressLint("MissingPermission") // ResonaSpeechPlugin checks RECORD_AUDIO first.
    public void start() throws IOException {
        int capacity = Math.max(32000, AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT));
        AudioRecord record = new AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            .setAudioFormat(new AudioFormat.Builder()
                .setSampleRate(SAMPLE_RATE)
                .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .build())
            .setBufferSizeInBytes(capacity)
            .build();
        if (record.getState() != AudioRecord.STATE_INITIALIZED) {
            record.release();
            feed.shutdown();
            throw new IOException("Das Mikrofon ließ sich nicht öffnen.");
        }
        record.startRecording();
        if (record.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
            record.release();
            feed.shutdown();
            throw new IOException("Das Mikrofon ließ sich nicht starten (wird es von einer anderen App benutzt?).");
        }
        recorder = record;
        captureThread = new Thread(() -> capture(capacity), "resona-speech-capture");
        captureThread.start();
        main.post(() -> {
            if (stopping) {
                recognitionEnded.countDown();
                return;
            }
            recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(context);
            recognizer.setRecognitionListener(new Callbacks());
            startRecognition();
        });
    }

    /**
     * Ends the session: drains the microphone (the last PCM frame goes out before this
     * returns), lets the recognizer finish the audio it has, commits a trailing hypothesis
     * and returns the whole on-device text.
     */
    public String stop() throws InterruptedException {
        stopping = true;
        if (captureThread != null) captureThread.join(STOP_TIMEOUT_MS);
        feed.awaitFinished(STOP_TIMEOUT_MS);
        main.post(() -> {
            main.removeCallbacks(restart);
            if (!listening) recognitionEnded.countDown();
        });
        recognitionEnded.await(STOP_TIMEOUT_MS, TimeUnit.MILLISECONDS);
        FutureTask<String> finish = new FutureTask<>(() -> {
            if (transcript.commitTentative()) emitUpdate();
            release();
            return transcript.text();
        });
        main.post(finish);
        try {
            return finish.get(STOP_TIMEOUT_MS, TimeUnit.MILLISECONDS);
        }
        catch (ExecutionException | TimeoutException e) {
            return transcript.committed();
        }
    }

    /** Drops everything at once (plugin destroyed, a new session replaces this one). */
    public void abort() {
        stopping = true;
        feed.shutdown();
        main.post(this::release);
    }

    // --- capture thread -------------------------------------------------------------

    private void capture(int capacity) {
        byte[] buffer = new byte[READ_BYTES];
        String failure = null;
        try {
            while (!stopping) {
                int count = recorder.read(buffer, 0, buffer.length, AudioRecord.READ_BLOCKING);
                if (count < 0) {
                    failure = "Das Mikrofon lieferte keine Daten mehr (Fehler " + count + ").";
                    break;
                }
                deliver(buffer, count);
            }
            if (failure == null) {
                // Drain what the recorder buffered before it stops, bounded by its capacity.
                int drained = 0;
                while (drained <= capacity) {
                    int count = recorder.read(buffer, 0, buffer.length, AudioRecord.READ_NON_BLOCKING);
                    if (count <= 0) break;
                    drained += count;
                    deliver(buffer, count);
                }
            }
        }
        finally {
            try {
                recorder.stop();
            }
            catch (IllegalStateException ignored) {
                // Not recording any more.
            }
            recorder.release();
            byte[] tail = frames.flush();
            if (tail != null && emitPcm) listener.onPcm(tail);
            captureEnded = true;
            feed.finish();
            if (feed.droppedBytes() > 0) {
                Log.i(TAG, "recognizer skipped " + feed.droppedBytes() + " bytes of audio");
            }
        }
        if (failure != null && !stopping) {
            listener.onError("audio-capture", failure, true);
        }
    }

    private void deliver(byte[] buffer, int count) {
        if (count == 0) return;
        byte[] chunk = new byte[count];
        System.arraycopy(buffer, 0, chunk, 0, count);
        feed.offer(chunk);
        for (byte[] frame : frames.append(chunk, count)) {
            if (emitPcm) listener.onPcm(frame);
        }
    }

    // --- main looper ----------------------------------------------------------------

    private void startRecognition() {
        if (recognizer == null || stopping || captureEnded || previewDead) {
            if (stopping) recognitionEnded.countDown();
            return;
        }
        ParcelFileDescriptor[] ends;
        try {
            ends = ParcelFileDescriptor.createPipe();
            feed.attach(new PipeSink(ends[1]));
        }
        catch (Exception e) {
            givePreviewUp("client", "Die lokale Spracherkennung ließ sich nicht verbinden.");
            return;
        }
        readEnd = ends[0];
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, language);
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true);
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, readEnd);
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1);
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT);
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, SAMPLE_RATE);
        // One session for the whole dictation, segment by segment, until the pipe closes.
        intent.putExtra(RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_AUDIO_SOURCE);
        listening = true;
        recognizer.startListening(intent);
    }

    /** A recognition is over (end of stream, end of utterance or error). */
    private void recognitionOver(long restartDelay, int error) {
        if (!listening) return;
        listening = false;
        closePipe();
        if (stopping || captureEnded) {
            recognitionEnded.countDown();
            return;
        }
        if (restartDelay == RecognitionRestarts.GIVE_UP) {
            givePreviewUp(RecognitionRestarts.code(error), previewMessage(error));
            return;
        }
        main.postDelayed(restart, restartDelay);
    }

    private void givePreviewUp(String code, String message) {
        previewDead = true;
        listener.onError(code, message, false);
    }

    private void closePipe() {
        feed.detach();
        if (readEnd != null) {
            try {
                readEnd.close();
            }
            catch (IOException ignored) {
                // Already closed.
            }
            readEnd = null;
        }
    }

    private void release() {
        main.removeCallbacks(restart);
        listening = false;
        closePipe();
        if (recognizer != null) {
            recognizer.cancel();
            recognizer.destroy();
            recognizer = null;
        }
        recognitionEnded.countDown();
    }

    private void emitUpdate() {
        listener.onUpdate(transcript.committed(), transcript.tentative(), transcript.revision());
    }

    private static String previewMessage(int error) {
        switch (error) {
            case RecognitionRestarts.ERROR_LANGUAGE_NOT_SUPPORTED:
                return "Die lokale Spracherkennung kann diese Sprache nicht.";
            case RecognitionRestarts.ERROR_LANGUAGE_UNAVAILABLE:
                return "Das Sprachpaket für die lokale Erkennung fehlt.";
            case RecognitionRestarts.ERROR_INSUFFICIENT_PERMISSIONS:
                return "Die lokale Spracherkennung darf das Mikrofon nicht nutzen.";
            default:
                return "Die lokale Vorschau ist ausgefallen (" + RecognitionRestarts.code(error) + ").";
        }
    }

    private static String best(Bundle results) {
        if (results == null) return "";
        ArrayList<String> texts = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return texts == null || texts.isEmpty() || texts.get(0) == null ? "" : texts.get(0);
    }

    private final class Callbacks implements RecognitionListener {
        @Override public void onReadyForSpeech(Bundle params) {}

        @Override public void onBeginningOfSpeech() {}

        @Override public void onRmsChanged(float rmsdB) {}

        @Override public void onBufferReceived(byte[] buffer) {}

        @Override public void onEndOfSpeech() {}

        @Override public void onEvent(int eventType, Bundle params) {}

        @Override
        public void onPartialResults(Bundle partialResults) {
            if (!listening) return;
            String text = best(partialResults);
            if (!text.isBlank()) restarts.heard();
            if (transcript.setTentative(text)) emitUpdate();
        }

        @Override
        public void onSegmentResults(Bundle segmentResults) {
            if (!listening) return;
            String text = best(segmentResults);
            if (!text.isBlank()) restarts.heard();
            if (transcript.commit(text)) emitUpdate();
        }

        @Override
        public void onEndOfSegmentedSession() {
            recognitionOver(restarts.afterEnd(), 0);
        }

        @Override
        public void onResults(Bundle results) {
            if (!listening) return;
            // A recognizer without segmented sessions: one utterance per recognition.
            String text = best(results);
            if (!text.isBlank()) restarts.heard();
            if (transcript.commit(text)) emitUpdate();
            recognitionOver(restarts.afterEnd(), 0);
        }

        @Override
        public void onError(int error) {
            recognitionOver(restarts.afterError(error), error);
        }
    }
}
