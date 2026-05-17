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


def test_profiles_show(monkeypatch):
    class FakeClient:
        def get_profile(self, name): return {"name": name, "steps": [], "description": "test-desc"}
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "show", "medical"])
    assert result.exit_code == 0
    assert "medical" in result.stdout


def test_profiles_pull(tmp_path, monkeypatch):
    dest = tmp_path / "medical.json"

    class FakeClient:
        def get_profile(self, name): return {"name": name, "steps": [], "description": "pulled"}
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
