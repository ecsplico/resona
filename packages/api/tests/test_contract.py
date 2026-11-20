"""``contract/openapi.json`` is the published client contract: committed, regenerated, never stale."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contract" / "openapi.json"
FIX = "uv run python scripts/gen_openapi.py"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_openapi", ROOT / "scripts" / "gen_openapi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stale_message(committed: str, generated: str) -> str | None:
    if committed == generated:
        return None
    return f"contract/openapi.json is stale: the hub's client contract changed. Run `{FIX}` and commit the result."


def test_committed_contract_matches_the_hub(gen):
    committed = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""

    message = stale_message(committed, gen.render(gen.document()))

    assert message is None, message


def test_a_changed_response_field_fails_with_the_fix_command(gen):
    schema = gen.document()
    edited = json.loads(gen.render(schema))
    job_view = edited["components"]["schemas"]["JobView"]
    job_view["properties"]["result_ready"] = job_view["properties"].pop("result_available")

    message = stale_message(gen.render(edited), gen.render(schema))

    assert message is not None
    assert FIX in message
    assert gen.COMMAND == FIX


def test_document_is_the_client_contract_at_the_hub_release(gen):
    from resona_api.app import app
    from resona_api.releases import hub_release_id

    schema = gen.document()

    assert schema["info"]["version"] == hub_release_id()
    assert schema["paths"] == app.openapi_client()["paths"]
    assert app.openapi_client()["info"]["version"] == app.version  # the app's cached document is not mutated


def test_rendering_is_stable(gen):
    schema = gen.document()
    text = gen.render(schema)

    assert text.endswith("}\n")
    assert json.loads(text) == schema
    assert gen.render(json.loads(text)) == text


def test_check_mode_names_the_fix_command(gen, tmp_path, capsys):
    out = tmp_path / "openapi.json"
    out.write_text("{}\n", encoding="utf-8")

    assert gen.main(["--check", "--out", str(out)]) == 1
    assert FIX in capsys.readouterr().err

    assert gen.main(["--out", str(out)]) == 0
    assert gen.main(["--check", "--out", str(out)]) == 0
