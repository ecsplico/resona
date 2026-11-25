package de.ecsplico.resona.speech;

/**
 * The live preview of one {@code ResonaSpeech} session: committed text (finished segments,
 * never changed again) plus the tentative hypothesis of the segment in progress, and a
 * revision that grows with every change so the web app can drop stale events.
 *
 * <p>Same {@code {committed, tentative, revision}} shape as T3 Code's {@code SpeechStreamText}
 * (pingdotgg/t3code, MIT). Pure Java: unit-tested without Android.
 */
public final class SpeechTranscript {

    private final StringBuilder committed = new StringBuilder();
    private String tentative = "";
    private int revision = 0;

    /** Replaces the tentative text. Returns whether anything changed. */
    public boolean setTentative(String text) {
        String next = clean(text);
        if (next.equals(tentative)) return false;
        tentative = next;
        revision++;
        return true;
    }

    /**
     * Commits a finished segment and clears the tentative text. An empty segment only
     * clears the tentative text (the recognizer withdrew its hypothesis).
     */
    public boolean commit(String text) {
        String piece = clean(text);
        if (piece.isEmpty()) {
            return setTentative("");
        }
        if (committed.length() > 0 && !startsWithPunctuation(piece)) {
            committed.append(' ');
        }
        committed.append(piece);
        tentative = "";
        revision++;
        return true;
    }

    /** At the end of a session a trailing hypothesis counts as said. */
    public boolean commitTentative() {
        return commit(tentative);
    }

    public String committed() {
        return committed.toString();
    }

    public String tentative() {
        return tentative;
    }

    public int revision() {
        return revision;
    }

    /** Committed text followed by the tentative text. */
    public String text() {
        if (tentative.isEmpty()) return committed();
        if (committed.length() == 0) return tentative;
        return committed + " " + tentative;
    }

    private static String clean(String text) {
        return text == null ? "" : text.trim().replaceAll("\\s+", " ");
    }

    private static boolean startsWithPunctuation(String piece) {
        char first = piece.charAt(0);
        return first == '.' || first == ',' || first == ';' || first == ':' || first == '!' || first == '?';
    }
}
