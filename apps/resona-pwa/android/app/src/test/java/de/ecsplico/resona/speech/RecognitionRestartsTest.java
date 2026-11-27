package de.ecsplico.resona.speech;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class RecognitionRestartsTest {

    @Test
    public void silenceAndCleanEndsRestartAtOnceWithoutLimit() {
        RecognitionRestarts restarts = new RecognitionRestarts();
        for (int i = 0; i < 50; i++) {
            assertEquals(0, restarts.afterError(RecognitionRestarts.ERROR_NO_MATCH));
            assertEquals(0, restarts.afterError(RecognitionRestarts.ERROR_SPEECH_TIMEOUT));
            assertEquals(0, restarts.afterEnd());
        }
    }

    @Test
    public void transientFailuresBackOffAndGiveUpAfterTheBudget() {
        RecognitionRestarts restarts = new RecognitionRestarts();
        assertEquals(250, restarts.afterError(RecognitionRestarts.ERROR_CLIENT));
        assertEquals(500, restarts.afterError(RecognitionRestarts.ERROR_SERVER));
        assertEquals(1000, restarts.afterError(RecognitionRestarts.ERROR_RECOGNIZER_BUSY));
        assertEquals(2000, restarts.afterError(RecognitionRestarts.ERROR_AUDIO));
        assertEquals(2000, restarts.afterError(RecognitionRestarts.ERROR_CLIENT));
        assertEquals(RecognitionRestarts.GIVE_UP, restarts.afterError(RecognitionRestarts.ERROR_CLIENT));
    }

    @Test
    public void newTextResetsTheBudget() {
        RecognitionRestarts restarts = new RecognitionRestarts();
        for (int i = 0; i < RecognitionRestarts.MAX_FAILURES; i++) {
            restarts.afterError(RecognitionRestarts.ERROR_CLIENT);
        }
        restarts.heard();
        assertEquals(250, restarts.afterError(RecognitionRestarts.ERROR_CLIENT));
    }

    @Test
    public void permissionAndLanguageErrorsGiveUpAtOnce() {
        for (int error : new int[] {
            RecognitionRestarts.ERROR_INSUFFICIENT_PERMISSIONS,
            RecognitionRestarts.ERROR_LANGUAGE_NOT_SUPPORTED,
            RecognitionRestarts.ERROR_LANGUAGE_UNAVAILABLE,
            RecognitionRestarts.ERROR_NETWORK,
        }) {
            assertEquals(RecognitionRestarts.GIVE_UP, new RecognitionRestarts().afterError(error));
        }
    }

    @Test
    public void errorCodesForTheWebApp() {
        assertEquals("not-allowed", RecognitionRestarts.code(RecognitionRestarts.ERROR_INSUFFICIENT_PERMISSIONS));
        assertEquals("language-not-supported", RecognitionRestarts.code(RecognitionRestarts.ERROR_LANGUAGE_NOT_SUPPORTED));
        assertEquals("language-unavailable", RecognitionRestarts.code(RecognitionRestarts.ERROR_LANGUAGE_UNAVAILABLE));
        assertEquals("no-speech", RecognitionRestarts.code(RecognitionRestarts.ERROR_NO_MATCH));
        assertEquals("busy", RecognitionRestarts.code(RecognitionRestarts.ERROR_TOO_MANY_REQUESTS));
        assertEquals("error-99", RecognitionRestarts.code(99));
    }

    @Test
    public void constantsMatchTheAndroidValues() {
        // android.speech.SpeechRecognizer.ERROR_* (API 33/34); inlined by javac.
        assertEquals(android.speech.SpeechRecognizer.ERROR_NO_MATCH, RecognitionRestarts.ERROR_NO_MATCH);
        assertEquals(android.speech.SpeechRecognizer.ERROR_SPEECH_TIMEOUT, RecognitionRestarts.ERROR_SPEECH_TIMEOUT);
        assertEquals(android.speech.SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS, RecognitionRestarts.ERROR_INSUFFICIENT_PERMISSIONS);
        assertEquals(android.speech.SpeechRecognizer.ERROR_SERVER_DISCONNECTED, RecognitionRestarts.ERROR_SERVER_DISCONNECTED);
        assertEquals(android.speech.SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED, RecognitionRestarts.ERROR_LANGUAGE_NOT_SUPPORTED);
        assertEquals(android.speech.SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE, RecognitionRestarts.ERROR_LANGUAGE_UNAVAILABLE);
        assertEquals(android.speech.SpeechRecognizer.ERROR_CANNOT_CHECK_SUPPORT, RecognitionRestarts.ERROR_CANNOT_CHECK_SUPPORT);
        assertEquals(android.speech.SpeechRecognizer.ERROR_CANNOT_LISTEN_TO_DOWNLOAD_EVENTS, RecognitionRestarts.ERROR_CANNOT_LISTEN_TO_DOWNLOAD_EVENTS);
    }
}
