from resona_job_core.features import (
    INSTRUCTIONS,
    known_features,
    release_features,
    required_features,
    worker_features,
)


def test_old_release_lacks_options_added_later():
    job = required_features("document", {"ocr": "auto", "remove_repeated_headers": True})
    assert not job <= release_features("0.1.0")
    assert job <= release_features("0.2.1")
    assert job <= release_features("0.2.2+local")


def test_instructions_need_0_2_0():
    job = required_features("text", {}, instructions=True)
    assert job == {INSTRUCTIONS}
    assert not job <= release_features("0.1.0")
    assert job <= release_features("0.2.0")


def test_unparseable_or_missing_release_knows_only_the_first_release():
    assert release_features(None) == release_features("0.1.0") == release_features("dev")
    assert "audio.translate" in release_features(None)


def test_newer_unreporting_release_keeps_the_last_legacy_entry():
    assert release_features("9.0.0") == release_features("0.2.3")


def test_reported_features_win_over_the_release():
    assert worker_features(["audio.diarize"], "0.1.0") == {"audio.diarize"}
    assert worker_features([], "0.2.3") == frozenset()
    assert worker_features(None, "0.2.3") == release_features("0.2.3")


def test_installed_release_knows_every_legacy_feature():
    assert release_features("0.2.3") <= known_features()
    assert "document.remove_repeated_headers" in known_features()
