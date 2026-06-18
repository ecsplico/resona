import pytest

from resona_directus_transcribe import run


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DIRECTUS_URL", "http://d:7700")
    monkeypatch.setenv("DIRECTUS_TOKEN", "tok")
    monkeypatch.setenv("RESONA_API_URL", "http://a:7710")
    monkeypatch.setenv("TRANSCRIBE_POLL_INTERVAL", "9")
    monkeypatch.setenv("TRANSCRIBE_CONCURRENCY", "4")
    monkeypatch.setenv("TRANSCRIBE_STALE_MINUTES", "30")
    monkeypatch.setenv("RESONA_API_KEY", "k")
    s = run.load_settings()
    assert s.directus_url == "http://d:7700"
    assert s.directus_token == "tok"
    assert s.resona_api_url == "http://a:7710"
    assert s.resona_api_key == "k"
    assert s.poll_interval == 9
    assert s.concurrency == 4
    assert s.stale_minutes == 30


def test_load_settings_requires_token(monkeypatch):
    monkeypatch.delenv("DIRECTUS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="DIRECTUS_TOKEN"):
        run.load_settings()
