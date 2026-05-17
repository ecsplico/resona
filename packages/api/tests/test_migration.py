import json
from sqlalchemy import create_engine, text
from resona_api.migration import migrate_config_tables_to_profile


def _legacy_db(path):
    eng = create_engine(f"sqlite:///{path}")
    with eng.connect() as c:
        c.execute(text("CREATE TABLE replacement (id INTEGER PRIMARY KEY, "
                        "name TEXT, replacement TEXT, active BOOLEAN)"))
        c.execute(text("CREATE TABLE initialprompt (id INTEGER PRIMARY KEY, "
                        "phrase TEXT, active BOOLEAN)"))
        c.execute(text("INSERT INTO replacement (name, replacement, active) "
                        "VALUES ('Komma', ',', 1)"))
        c.execute(text("INSERT INTO initialprompt (phrase, active) "
                        "VALUES ('Befund', 1)"))
        c.commit()
    return eng


def test_migration_exports_and_drops(tmp_path):
    db = tmp_path / "jobs.sqlite"
    eng = _legacy_db(db)
    profiles_dir = tmp_path / "profiles"

    migrate_config_tables_to_profile(eng, profiles_dir)

    written = json.loads((profiles_dir / "default.json").read_text())
    assert written["initial_prompt"] == ["Befund"]
    rules = written["steps"][0]["rules"]
    assert {"pattern": "Komma", "replacement": ","} in rules

    with eng.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))]
    assert "replacement" not in tables
    assert "initialprompt" not in tables


def test_migration_noop_when_tables_absent(tmp_path):
    db = tmp_path / "jobs.sqlite"
    eng = create_engine(f"sqlite:///{db}")
    with eng.connect() as c:
        c.execute(text("CREATE TABLE job (id INTEGER PRIMARY KEY)"))
        c.commit()
    profiles_dir = tmp_path / "profiles"
    migrate_config_tables_to_profile(eng, profiles_dir)  # must not raise
    assert not (profiles_dir / "default.json").exists()


def test_migration_skips_if_default_exists(tmp_path):
    db = tmp_path / "jobs.sqlite"
    eng = _legacy_db(db)
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "default.json").write_text('{"name": "default", "steps": []}')
    migrate_config_tables_to_profile(eng, profiles_dir)
    # existing default.json is preserved, tables still dropped
    assert json.loads((profiles_dir / "default.json").read_text())["steps"] == []
