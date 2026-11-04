import decouple
import pytest

from resona_project_worker import run


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Hermetic: read only os.environ, never a repo/cwd `.env` file.
    monkeypatch.setattr(run, "config", decouple.Config(decouple.RepositoryEmpty()))
    for key in (
        "DIRECTUS_URL", "DIRECTUS_TOKEN", "RESONA_API_URL", "RESONA_API_KEY",
        "WORKER_ENGINE", "WORKER_POLL_INTERVAL", "WORKER_BATCH", "WORKER_STALE_MINUTES",
    ):
        monkeypatch.delenv(key, raising=False)


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DIRECTUS_URL", "http://d:7700")
    monkeypatch.setenv("DIRECTUS_TOKEN", "tok")
    monkeypatch.setenv("RESONA_API_URL", "http://hub:7000")
    monkeypatch.setenv("RESONA_API_KEY", "k")
    monkeypatch.setenv("WORKER_ENGINE", "whisper")
    monkeypatch.setenv("WORKER_POLL_INTERVAL", "9")
    monkeypatch.setenv("WORKER_BATCH", "3")
    monkeypatch.setenv("WORKER_STALE_MINUTES", "30")
    s = run.load_settings()
    assert s.directus_url == "http://d:7700"
    assert s.directus_token == "tok"
    assert s.resona_api_url == "http://hub:7000"
    assert s.resona_api_key == "k"
    assert s.engine == "whisper"
    assert s.poll_interval == 9
    assert s.batch == 3
    assert s.stale_minutes == 30


def test_load_settings_defaults(monkeypatch):
    monkeypatch.setenv("DIRECTUS_TOKEN", "tok")
    monkeypatch.setenv("RESONA_API_KEY", "k")
    s = run.load_settings()
    assert s.engine == ""
    assert s.poll_interval == 5
    assert s.batch == 5
    assert s.stale_minutes == 15


def test_load_settings_requires_directus_token(monkeypatch):
    monkeypatch.setenv("RESONA_API_KEY", "k")
    with pytest.raises(RuntimeError, match="DIRECTUS_TOKEN"):
        run.load_settings()


def test_load_settings_requires_hub_token(monkeypatch):
    monkeypatch.setenv("DIRECTUS_TOKEN", "tok")
    with pytest.raises(RuntimeError, match=r"RESONA_API_KEY is required \(hub client token\)"):
        run.load_settings()
