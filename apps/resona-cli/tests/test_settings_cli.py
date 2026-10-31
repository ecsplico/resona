"""`resona settings get|pull|set` and the local settings cache."""
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli import settings_cache
from resona_cli.main import app
from resona_client.client import ResonaAPIError, RevisionConflict, SettingsDoc
from resona_client.config import EngineConfig
from resona_job_core import default_settings

runner = CliRunner()
HUB = "http://hub.test"


@pytest.fixture(autouse=True)
def _hub(monkeypatch, tmp_path):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr(settings_cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("RESONA_API_URL", HUB)
    monkeypatch.setenv("RESONA_API_KEY", "secret-token")


def _doc(revision=1, **changes):
    settings = default_settings().model_copy(update=changes).model_dump(mode="json")
    return SettingsDoc(organization_id="org-a", revision=revision, etag=f'"{revision}"', settings=settings)


def _client(doc):
    client = MagicMock()
    client.base_url = HUB
    client.api_key = "secret-token"
    client.get_settings.return_value = doc
    client.put_settings.side_effect = lambda settings, *, if_match: SettingsDoc(
        organization_id="org-a", revision=doc.revision + 1, etag=f'"{doc.revision + 1}"',
        settings=settings)
    return client


def _run(client, *args):
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        return runner.invoke(app, ["settings", *args])


def test_get_prints_and_caches_without_the_token(tmp_path):
    result = _run(_client(_doc(3)), "get")

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["organization_id"] == "org-a" and body["revision"] == 3
    [cache_file] = (tmp_path / "cache").glob("*.json")
    cached = json.loads(cache_file.read_text())
    assert cached["hub_url"] == HUB and cached["revision"] == 3 and cached["stale"] is False
    assert "secret-token" not in cache_file.read_text()


def test_pull_clears_stale_flag(tmp_path):
    _run(_client(_doc(3)), "pull")
    entry = settings_cache.find(HUB, "secret-token")
    settings_cache.mark_stale(entry)

    result = _run(_client(_doc(4)), "pull")

    assert result.exit_code == 0, result.output
    entry = settings_cache.find(HUB, "secret-token")
    assert entry.revision == 4 and entry.stale is False


def test_cache_is_keyed_by_hub_and_organization(tmp_path):
    settings_cache.store(_doc(1), hub_url=HUB, api_key="secret-token")
    settings_cache.store(_doc(2), hub_url="http://other.test", api_key="secret-token")
    other_org = SettingsDoc(organization_id="org-b", revision=5, etag='"5"', settings=_doc().settings)
    settings_cache.store(other_org, hub_url=HUB, api_key="other-token")

    assert len(list((tmp_path / "cache").glob("*.json"))) == 3
    assert settings_cache.find(HUB, "secret-token").revision == 1
    assert settings_cache.find(HUB, "other-token").organization_id == "org-b"


def test_set_key_value_sends_if_match_from_cache_and_updates_cache():
    client = _client(_doc(3))
    _run(client, "pull")

    result = _run(client, "set", "language=en", "polish=false")

    assert result.exit_code == 0, result.output
    sent, kwargs = client.put_settings.call_args.args[0], client.put_settings.call_args.kwargs
    assert kwargs["if_match"] == '"3"'
    assert sent["language"] == "en" and sent["execution_profiles"].keys() >= {"cpu-private"}
    assert settings_cache.find(HUB, "secret-token").revision == 4
    assert "applies to new jobs only" in result.output


def test_set_without_cache_uses_fresh_etag():
    client = _client(_doc(6))

    result = _run(client, "set", "default_execution_profile=cpu-private")

    assert result.exit_code == 0, result.output
    assert client.put_settings.call_args.kwargs["if_match"] == '"6"'


def test_set_from_file(tmp_path):
    client = _client(_doc(2))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(_doc(language="en").settings))

    result = _run(client, "set", "--file", str(path))

    assert result.exit_code == 0, result.output
    assert client.put_settings.call_args.args[0]["language"] == "en"


def test_set_conflict_exits_1_with_clear_message():
    client = _client(_doc(3))
    _run(client, "pull")
    client.put_settings.side_effect = RevisionConflict(409, "revision_conflict", "stale")

    result = _run(client, "set", "language=en")

    assert result.exit_code == 1
    assert "revision_conflict" in result.output and "resona settings pull" in result.output


@pytest.mark.parametrize("args, message", [
    (["nonsense=1"], "unknown settings key"),
    (["language"], "expected key=value"),
    (["cloud_enabled=maybe"], "invalid settings"),
    ([], "either --file or key=value"),
])
def test_set_validates_before_sending(args, message):
    client = _client(_doc(3))

    result = _run(client, "set", *args)

    assert result.exit_code == 1
    assert message in result.output
    client.put_settings.assert_not_called()


def test_set_requires_admin():
    client = _client(_doc(3))
    client.put_settings.side_effect = ResonaAPIError(403, "forbidden", "org_admin role required")

    result = _run(client, "set", "language=en")

    assert result.exit_code == 1
    assert "org_admin role required" in result.output
