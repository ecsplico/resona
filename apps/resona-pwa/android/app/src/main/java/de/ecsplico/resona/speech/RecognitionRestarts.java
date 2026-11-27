package de.ecsplico.resona.speech;

/**
 * When a recognition ends while the microphone still runs, whether and how soon to start
 * the next one. Silence ({@code ERROR_NO_MATCH}, {@code ERROR_SPEECH_TIMEOUT}) and a clean
 * end (a recognizer without segmented sessions ends after each utterance) restart at once
 * and never count against the budget, so long pauses in a dictation do not end the preview.
 * Transient failures restart with a growing delay; after {@link #MAX_FAILURES} in a row
 * without new text, or on an error a restart cannot fix (permissions, language), the
 * preview gives up and the web app is told. The PCM for the hub correction keeps flowing
 * either way.
 *
 * <p>Error codes are {@code android.speech.SpeechRecognizer.ERROR_*} (compile-time
 * constants, repeated here so this class stays pure Java and unit-testable without Android).
 * Error naming follows T3 Code's {@code VoiceTranscriptionFailure} (pingdotgg/t3code, MIT).
 */
public final class RecognitionRestarts {

    public static final int ERROR_NETWORK_TIMEOUT = 1;
    public static final int ERROR_NETWORK = 2;
    public static final int ERROR_AUDIO = 3;
    public static final int ERROR_SERVER = 4;
    public static final int ERROR_CLIENT = 5;
    public static final int ERROR_SPEECH_TIMEOUT = 6;
    public static final int ERROR_NO_MATCH = 7;
    public static final int ERROR_RECOGNIZER_BUSY = 8;
    public static final int ERROR_INSUFFICIENT_PERMISSIONS = 9;
    public static final int ERROR_TOO_MANY_REQUESTS = 10;
    public static final int ERROR_SERVER_DISCONNECTED = 11;
    public static final int ERROR_LANGUAGE_NOT_SUPPORTED = 12;
    public static final int ERROR_LANGUAGE_UNAVAILABLE = 13;
    public static final int ERROR_CANNOT_CHECK_SUPPORT = 14;
    public static final int ERROR_CANNOT_LISTEN_TO_DOWNLOAD_EVENTS = 15;

    public static final int MAX_FAILURES = 5;
    static final long BASE_DELAY_MS = 250;
    static final long MAX_DELAY_MS = 2000;

    /** Returned by {@link #afterEnd} / {@link #afterError} when the preview gives up. */
    public static final long GIVE_UP = -1;

    private int failures = 0;

    /** The recognizer produced text: the failure budget starts over. */
    public void heard() {
        failures = 0;
    }

    /** A recognition ended normally (end of utterance or of a segmented session). */
    public long afterEnd() {
        return 0;
    }

    /** A recognition failed with {@code error}: restart delay in ms, or {@link #GIVE_UP}. */
    public long afterError(int error) {
        if (isSilence(error)) return 0;
        if (isFatal(error)) return GIVE_UP;
        failures++;
        if (failures > MAX_FAILURES) return GIVE_UP;
        return Math.min(MAX_DELAY_MS, BASE_DELAY_MS << (failures - 1));
    }

    public static boolean isSilence(int error) {
        return error == ERROR_NO_MATCH || error == ERROR_SPEECH_TIMEOUT;
    }

    /** Errors a restart cannot fix. Network errors count: the recognizer is on-device only. */
    public static boolean isFatal(int error) {
        switch (error) {
            case ERROR_INSUFFICIENT_PERMISSIONS:
            case ERROR_LANGUAGE_NOT_SUPPORTED:
            case ERROR_LANGUAGE_UNAVAILABLE:
            case ERROR_NETWORK:
            case ERROR_NETWORK_TIMEOUT:
                return true;
            default:
                return false;
        }
    }

    /** Stable error code for the web app ({@code error} event). */
    public static String code(int error) {
        switch (error) {
            case ERROR_NETWORK_TIMEOUT:
            case ERROR_NETWORK:
                return "network";
            case ERROR_AUDIO:
                return "audio";
            case ERROR_SERVER:
            case ERROR_SERVER_DISCONNECTED:
                return "service";
            case ERROR_CLIENT:
                return "client";
            case ERROR_SPEECH_TIMEOUT:
            case ERROR_NO_MATCH:
                return "no-speech";
            case ERROR_RECOGNIZER_BUSY:
            case ERROR_TOO_MANY_REQUESTS:
                return "busy";
            case ERROR_INSUFFICIENT_PERMISSIONS:
                return "not-allowed";
            case ERROR_LANGUAGE_NOT_SUPPORTED:
                return "language-not-supported";
            case ERROR_LANGUAGE_UNAVAILABLE:
                return "language-unavailable";
            default:
                return "error-" + error;
        }
    }
}
