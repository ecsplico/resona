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
