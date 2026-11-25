from typer.testing import CliRunner
from resona_cli.main import app


def test_profiles_list(monkeypatch):
    class FakeClient:
        def list_profiles(self): return [{"name": "a", "description": "AA"}]
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "list"])
    assert result.exit_code == 0
    assert "a" in result.stdout


def test_profiles_push(tmp_path, monkeypatch):
    f = tmp_path / "arzt.json"
    f.write_text('{"name": "arzt", "steps": []}')
    pushed = {}

    class FakeClient:
        def put_profile(self, name, profile): pushed["name"] = name
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "push", "arzt", str(f)])
    assert result.exit_code == 0
    assert pushed["name"] == "arzt"


def _write_profile(path, name, *includes, block=False):
    import json
    steps = [{"type": "include", "profile": inc} for inc in includes] or [{"type": "replacements", "rules": []}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "block": block, "steps": steps}))


class _RecordingClient:
    def __init__(self, stored=()):
        self.pushed = []
        self.stored = set(stored)

    def put_profile(self, name, profile):
        self.pushed.append(name)

    def get_profile(self, name, *, resolved=False):
        from resona_client.client import ResonaAPIError
        if name not in self.stored:
            raise ResonaAPIError(404, None, "Profile not found")
        return {"name": name, "steps": []}


def _use(monkeypatch, client):
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: client))


def test_profiles_push_orders_blocks_before_the_profiles_that_include_them(tmp_path, monkeypatch):
    # Profiles first in the arguments and in the directory listing; blocks nested two deep.
    _write_profile(tmp_path / "a-polish.json", "a-polish", "b-markdown", "polish-block")
    _write_profile(tmp_path / "b-markdown.json", "b-markdown", "headings-block", "dictation-commands-de")
    _write_profile(tmp_path / "blocks" / "headings-block.json", "headings-block", block=True)
    _write_profile(tmp_path / "blocks" / "polish-block.json", "polish-block", block=True)
    client = _RecordingClient()
    _use(monkeypatch, client)

    result = CliRunner().invoke(app, ["profiles", "push", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    # dictation-commands-de is a bundled block: not pushed, not looked up.
    assert client.pushed == ["headings-block", "b-markdown", "polish-block", "a-polish"]


def test_profiles_push_orders_explicit_files(tmp_path, monkeypatch):
    _write_profile(tmp_path / "outer.json", "outer", "inner")
    _write_profile(tmp_path / "inner.json", "inner", block=True)
    client = _RecordingClient()
    _use(monkeypatch, client)

    result = CliRunner().invoke(app, ["profiles", "push", str(tmp_path / "outer.json"), str(tmp_path / "inner.json")])

    assert result.exit_code == 0, result.stdout
    assert client.pushed == ["inner", "outer"]


def test_profiles_push_accepts_an_include_stored_on_the_hub(tmp_path, monkeypatch):
    _write_profile(tmp_path / "outer.json", "outer", "names-block")
    client = _RecordingClient(stored={"names-block"})
    _use(monkeypatch, client)

    result = CliRunner().invoke(app, ["profiles", "push", str(tmp_path / "outer.json")])

    assert result.exit_code == 0, result.stdout
    assert client.pushed == ["outer"]


def test_profiles_push_refuses_a_missing_include_before_any_upload(tmp_path, monkeypatch):
    _write_profile(tmp_path / "ok.json", "ok")
    _write_profile(tmp_path / "outer.json", "outer", "nowhere")
    client = _RecordingClient()
    _use(monkeypatch, client)

    result = CliRunner().invoke(app, ["profiles", "push", str(tmp_path)])

    assert result.exit_code == 1
    assert "'outer' includes 'nowhere'" in result.stdout
    assert client.pushed == []


def test_profiles_push_refuses_a_cycle_before_any_upload(tmp_path, monkeypatch):
    _write_profile(tmp_path / "ok.json", "ok")
    _write_profile(tmp_path / "x.json", "x", "y")
    _write_profile(tmp_path / "blocks" / "y.json", "y", "x", block=True)
    client = _RecordingClient()
    _use(monkeypatch, client)

    result = CliRunner().invoke(app, ["profiles", "push", str(tmp_path)])

    assert result.exit_code == 1
    assert "include cycle: x -> y -> x" in result.stdout
    assert client.pushed == []


def test_profiles_push_pushes_the_catalogue(monkeypatch):
    from pathlib import Path
    catalogue = Path(__file__).resolve().parents[3] / "profiles"
    client = _RecordingClient()
    _use(monkeypatch, client)

    result = CliRunner().invoke(app, ["profiles", "push", str(catalogue)])

    assert result.exit_code == 0, result.stdout
    pushed = client.pushed
    assert sorted(pushed) == sorted(p.stem for p in [*catalogue.glob("*.json"), *catalogue.glob("blocks/*.json")])
    assert pushed.index("transcript-markdown-de") < pushed.index("transcript-polish-de")
    assert pushed.index("polish-punctuation-de") < pushed.index("transcript-polish-de")
    assert pushed.index("names-medical-de") < pushed.index("dictation-medical-de")


def test_profiles_show(monkeypatch):
    class FakeClient:
        def get_profile(self, name, *, resolved=False): return {"name": name, "steps": [], "description": "test-desc"}
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "show", "medical"])
    assert result.exit_code == 0
    assert "medical" in result.stdout


def test_profiles_show_resolved_asks_for_the_flat_profile(monkeypatch):
    asked = {}

    class FakeClient:
        def get_profile(self, name, *, resolved=False):
            asked["resolved"] = resolved
            return {"name": name, "profile": {"steps": [{"type": "replacements", "rules": []}]},
                    "includes": [{"name": "dictation-commands-de", "revision": 0}]}
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "show", "medical", "--resolved"])
    assert result.exit_code == 0
    assert asked["resolved"] is True
    assert "dictation-commands-de" in result.stdout


def test_profiles_pull(tmp_path, monkeypatch):
    dest = tmp_path / "medical.json"

    class FakeClient:
        def get_profile(self, name, *, resolved=False): return {"name": name, "steps": [], "description": "pulled"}
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "pull", "medical", str(dest)])
    assert result.exit_code == 0
    assert dest.exists()
    import json
    data = json.loads(dest.read_text())
    assert data["name"] == "medical"


def test_profiles_delete(monkeypatch):
    deleted = {}

    class FakeClient:
        def delete_profile(self, name): deleted["name"] = name
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "delete", "medical"])
    assert result.exit_code == 0
    assert deleted["name"] == "medical"
