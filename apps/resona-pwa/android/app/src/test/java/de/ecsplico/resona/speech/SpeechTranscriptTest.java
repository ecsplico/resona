package de.ecsplico.resona.speech;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class SpeechTranscriptTest {

    @Test
    public void tentativeTextChangesBumpTheRevisionOnlyWhenTheyDiffer() {
        SpeechTranscript t = new SpeechTranscript();
        assertTrue(t.setTentative("Der Pat"));
        assertEquals(1, t.revision());
        assertFalse(t.setTentative("  Der Pat "));
        assertEquals(1, t.revision());
        assertTrue(t.setTentative("Der Patient"));
        assertEquals("", t.committed());
        assertEquals("Der Patient", t.tentative());
        assertEquals("Der Patient", t.text());
    }

    @Test
    public void segmentsJoinWithSpacesAndClearTheHypothesis() {
        SpeechTranscript t = new SpeechTranscript();
        t.setTentative("Der Patient");
        t.commit("Der Patient kommt.");
        t.setTentative("Heute");
        t.commit("Heute geht es besser");
        t.commit(", sagt er.");
        assertEquals("Der Patient kommt. Heute geht es besser, sagt er.", t.committed());
        assertEquals("", t.tentative());
    }

    @Test
    public void repeatedSegmentsAreKept() {
        SpeechTranscript t = new SpeechTranscript();
        t.commit("Noch einmal.");
        t.commit("Noch einmal.");
        assertEquals("Noch einmal. Noch einmal.", t.committed());
    }

    @Test
    public void anEmptySegmentOnlyWithdrawsTheHypothesis() {
        SpeechTranscript t = new SpeechTranscript();
        t.commit("Anfang.");
        t.setTentative("ähm");
        int before = t.revision();
        assertTrue(t.commit("   "));
        assertEquals("Anfang.", t.committed());
        assertEquals("", t.tentative());
        assertEquals(before + 1, t.revision());
        assertFalse(t.commit(""));
    }

    @Test
    public void aTrailingHypothesisCountsAtTheEnd() {
        SpeechTranscript t = new SpeechTranscript();
        t.commit("Anfang.");
        t.setTentative("unfertiges Ende");
        assertEquals("Anfang. unfertiges Ende", t.text());
        assertTrue(t.commitTentative());
        assertEquals("Anfang. unfertiges Ende", t.committed());
        assertFalse(t.commitTentative());
    }

    @Test
    public void whitespaceInsideResultsCollapses() {
        SpeechTranscript t = new SpeechTranscript();
        t.commit("zwei\n  Wörter");
        assertEquals("zwei Wörter", t.committed());
    }
}
