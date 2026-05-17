"""One-shot migration: export legacy config tables into a profile file."""

import json
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).first()
    return row is not None


def migrate_config_tables_to_profile(engine: Engine, profiles_dir: Path) -> None:
    """Export `replacement` + `initialprompt` rows to `<profiles_dir>/default.json`,
    then drop both tables. No-op when neither table exists.
    """
    profiles_dir = Path(profiles_dir)
    with engine.connect() as conn:
        has_repl = _table_exists(conn, "replacement")
        has_prompt = _table_exists(conn, "initialprompt")
        if not has_repl and not has_prompt:
            return

        rules, prompts = [], []
        if has_repl:
            for row in conn.execute(text(
                "SELECT name, replacement FROM replacement WHERE active=1")):
                rules.append({"pattern": row[0], "replacement": row[1]})
        if has_prompt:
            for row in conn.execute(text(
                "SELECT phrase FROM initialprompt WHERE active=1")):
                prompts.append(row[0])

        default_path = profiles_dir / "default.json"
        if not default_path.exists():
            profiles_dir.mkdir(parents=True, exist_ok=True)
            profile = {
                "name": "default",
                "description": "Migrated from legacy replacement/prompt tables.",
                "initial_prompt": prompts,
                "steps": [{"type": "replacements", "name": "migrated", "rules": rules}],
            }
            default_path.write_text(
                json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("Migrated %d replacements + %d prompts to %s",
                     len(rules), len(prompts), default_path)
        else:
            log.info("%s already exists; keeping it, dropping legacy tables", default_path)

        if has_repl:
            conn.execute(text("DROP TABLE replacement"))
        if has_prompt:
            conn.execute(text("DROP TABLE initialprompt"))
        conn.commit()
