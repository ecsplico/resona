"""Speaker attribution, DER and speaker count against reference turns."""
import pytest
from resona_eval.corpus import SpeakerTurn
from resona_eval.metrics import der, speaker_attribution, speaker_count_error
from resona_job_core import Segment

REFERENCE = [
    SpeakerTurn(speaker="Anna", start=0.0, end=4.0, text="guten tag was führt sie zu mir"),
    SpeakerTurn(speaker="Flo", start=5.0, end=9.0, text="seit zwei wochen habe ich kopfschmerzen"),
    SpeakerTurn(speaker="Anna", start=10.0, end=12.0, text="dann messen wir"),
]


def _hyp(*speakers):
    return [Segment(start=t.start, end=t.end, text=t.text, speaker=s) for t, s in zip(REFERENCE, speakers)]


def test_perfect_hypothesis_scores_perfect():
    hyp = _hyp(1, 2, 1)
    assert speaker_attribution(REFERENCE, hyp) == 1.0
    assert der(REFERENCE, hyp) == 0.0
    assert speaker_count_error(REFERENCE, hyp) == 0


def test_swapped_labels_are_still_perfect_through_the_mapping():
    hyp = _hyp(2, 1, 2)
    assert speaker_attribution(REFERENCE, hyp) == 1.0
    assert der(REFERENCE, hyp) == 0.0


def test_one_turn_with_the_wrong_speaker():
    hyp = _hyp(1, 2, 2)
    assert speaker_attribution(REFERENCE, hyp) == pytest.approx(13 / 16)  # "dann messen wir" wrong
    assert der(REFERENCE, hyp) == pytest.approx(2 / 10)  # 2 s confused of 10 s speech
    assert speaker_count_error(REFERENCE, hyp) == 0


def test_missed_speaker_cannot_be_mapped():
    hyp = _hyp(1, 1, 1)
    assert speaker_attribution(REFERENCE, hyp) == pytest.approx(10 / 16)
    assert der(REFERENCE, hyp) == pytest.approx(4 / 10)
    assert speaker_count_error(REFERENCE, hyp) == 1


def test_extra_speaker_maps_to_nobody():
    hyp = _hyp(1, 2, 3)
    assert speaker_attribution(REFERENCE, hyp) == pytest.approx(13 / 16)
    assert speaker_count_error(REFERENCE, hyp) == 1


def test_unattributed_and_missing_speech():
    assert speaker_attribution(REFERENCE, _hyp(None, None, None)) == 0.0
    assert der(REFERENCE, _hyp(None, None, None)) == pytest.approx(1.0)
    assert speaker_attribution(REFERENCE, []) == 0.0
    assert der(REFERENCE, []) == 1.0  # all reference speech missed


def test_der_counts_false_alarm_and_overlap():
    false_alarm = [*_hyp(1, 2, 1), Segment(start=13.0, end=14.0, text="hm", speaker=2)]
    assert der(REFERENCE, false_alarm) == pytest.approx(1 / 10)
    overlap = [*REFERENCE, SpeakerTurn(speaker="Flo", start=3.0, end=4.0, text="mhm")]
    # 11 s of reference speech; the overlapping second missed by a one-speaker hypothesis.
    assert der(overlap, _hyp(1, 2, 1)) == pytest.approx(1 / 11)


def test_attribution_follows_the_word_alignment_not_the_segment_boundaries():
    # One ASR segment running over the speaker change, split correctly by word speakers.
    hyp = [Segment(start=0.0, end=4.0, text="guten tag was führt sie zu mir", speaker=1),
           Segment(start=5.0, end=9.0, text="seit zwei wochen habe ich", speaker=2),
           Segment(start=9.0, end=12.0, text="kopfweh dann messen wir", speaker=1)]
    # "kopfschmerzen" → "kopfweh" is a substitution carrying the wrong speaker.
    assert speaker_attribution(REFERENCE, hyp) == pytest.approx(15 / 16)
