package de.ecsplico.resona.speech;

import static org.junit.Assert.assertEquals;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import org.junit.Test;

public class LanguageSupportTest {

    private static final List<String> NONE = Collections.emptyList();

    @Test
    public void installedBeatsPendingBeatsSupported() {
        List<String> installed = Arrays.asList("en-US");
        List<String> pending = Arrays.asList("de-DE");
        List<String> supported = Arrays.asList("de-DE", "en-US", "fr-FR");
        assertEquals("available", LanguageSupport.status("en-US", installed, pending, supported));
        assertEquals("downloading", LanguageSupport.status("de-DE", installed, pending, supported));
        assertEquals("downloadable", LanguageSupport.status("fr-FR", installed, pending, supported));
        assertEquals("unavailable", LanguageSupport.status("it-IT", installed, pending, supported));
    }

    @Test
    public void aRegionalTagNeedsThatRegion() {
        assertEquals("unavailable", LanguageSupport.status("de-DE", Arrays.asList("de-AT"), NONE, NONE));
    }

    @Test
    public void aBareLanguageTakesAnyRegion() {
        assertEquals("available", LanguageSupport.status("de", Arrays.asList("de-AT"), NONE, NONE));
        assertEquals("unavailable", LanguageSupport.status("de", Arrays.asList("dsb-DE"), NONE, NONE));
    }

    @Test
    public void tagsCompareCaseInsensitivelyWithUnderscores() {
        assertEquals("available", LanguageSupport.status("de-DE", Arrays.asList("de_de"), NONE, NONE));
    }

    @Test
    public void missingListsMeanUnavailable() {
        assertEquals("unavailable", LanguageSupport.status("de-DE", null, null, null));
    }
}
