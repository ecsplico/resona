package de.ecsplico.resona.speech;

import java.util.List;
import java.util.Locale;

/**
 * Maps Android's {@code RecognitionSupport} language lists (API 33) to the web app's
 * availability states ({@code DeviceSpeechAvailability} in {@code useDeviceSpeech.ts}).
 * A tag with a region ({@code de-DE}) needs that exact language; a bare language
 * ({@code de}) accepts any region. Tags compare case-insensitively, {@code _} = {@code -}.
 * Pure Java: unit-tested without Android.
 */
public final class LanguageSupport {

    private LanguageSupport() {}

    public static String status(String tag, List<String> installed, List<String> pending, List<String> supported) {
        if (matches(tag, installed)) return "available";
        if (matches(tag, pending)) return "downloading";
        if (matches(tag, supported)) return "downloadable";
        return "unavailable";
    }

    static boolean matches(String tag, List<String> languages) {
        if (languages == null || tag == null) return false;
        String wanted = normalize(tag);
        boolean bare = !wanted.contains("-");
        for (String language : languages) {
            String have = normalize(language);
            if (have.equals(wanted)) return true;
            if (bare && have.startsWith(wanted + "-")) return true;
        }
        return false;
    }

    private static String normalize(String tag) {
        return tag.trim().replace('_', '-').toLowerCase(Locale.ROOT);
    }
}
