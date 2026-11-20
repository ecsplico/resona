"""The postprocess tools: profile catalogue, no hub client, and a run against a real Directus.

The writes themselves are pinned by ``shared/dictationEdit.cases.json``
(``test_edit_tools.py``); here the parts that case table cannot show.
"""
import ast
import json
import tomllib
from pathlib import Path

from resona_dictations_mcp import postprocess_tools
from resona_dictations_mcp.server import Caller, text_sha256

from .conftest import tool_payload
from .test_edit_isolation import rows
from .test_edit_tools import RECORDING, FakeDirectus, run, tool_message
from .test_isolation import call_tool

MCP_DIR = Path(__file__).resolve().parents[1]
SRC = MCP_DIR / "src" / "resona_dictations_mcp"
CATALOGUE = json.loads((MCP_DIR.parent / "shared" / "postprocessProfiles.json").read_text("utf-8"))


def test_profiles_are_the_apps_catalogue():
    assert [dict(p) for p in postprocess_tools.PROFILES] == CATALOGUE["profiles"]
    assert tool_payload(run(FakeDirectus(None), "list_profiles", {})) == {"profiles": CATALOGUE["profiles"]}


def test_unknown_profile_fails_before_any_directus_call():
    class Untouchable(FakeDirectus):
        async def item(self, *args, **kwargs):
            raise AssertionError("read before the profile was checked")

        items = create = update = delete = item

    result = run(Untouchable(None), "postprocess_dictation", {"id": RECORDING, "profile": "gibt-es-nicht"})
    assert tool_message(result).startswith("unknown_profile:")


def test_the_service_has_no_hub_client():
    """The project worker talks to the hub; this service only writes the request to Directus."""
    imported = set()
    for path in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add(node.module or "")
    assert not {name for name in imported if name.split(".")[0].startswith("resona")}, imported
    source = "\n".join(path.read_text("utf-8") for path in SRC.glob("*.py"))
    for name in ("RESONA_API_URL", "RESONA_API_KEY", "RESONA_HUB", "HUB_URL", "/v1/jobs", "/v1/uploads"):
        assert name not in source
    dependencies = tomllib.loads((MCP_DIR / "pyproject.toml").read_text("utf-8"))["project"]["dependencies"]
    assert not [dep for dep in dependencies if dep.startswith("resona")]
    # The service's only per-call backend is the caller's Directus client.
    assert set(Caller.__dataclass_fields__) <= {"user_id", "directus", "token_rejected"}


def test_get_dictation_reports_the_run():
    fake = FakeDirectus({"text": "Kapitel Befund", "raw_text": None, "applied_profile": None,
                         "processed_text": None, "postprocess_status": None})
    run(fake, "postprocess_dictation", {"id": RECORDING, "profile": "transcript-markdown-de"})
    read = tool_payload(run(fake, "get_dictation", {"id": RECORDING}))
    assert (read["postprocess_status"], read["postprocess_profile"]) == ("pending", "transcript-markdown-de")


def test_a_run_requested_over_mcp_completes_like_one_from_the_app(base, directus_admin):
    """Queue over MCP, then do what the project worker does for an app request (claim,
    write the result with its service credential): the user then reads the profile's
    output, and restore_raw_transcript brings the raw transcript back."""
    admin, user = directus_admin, directus_admin.user("postprocess")
    raw = "Kapitel Befund Absatz unauffällig Punkt"
    rid = user.dictation("Diktat vom 4.3.", raw)

    queued = tool_payload(call_tool(base, user.token, "postprocess_dictation", {
        "id": rid, "profile": "transcript-polish-de", "instructions": "Abkürzungen ausschreiben."}))
    assert queued["postprocess_status"] == "pending"
    row = rows(admin, "transcripts", recording=rid)[0]
    assert {k: row[k] for k in ("postprocess_status", "postprocess_profile", "postprocess_instructions", "text")} == {
        "postprocess_status": "pending", "postprocess_profile": "transcript-polish-de",
        "postprocess_instructions": "Abkürzungen ausschreiben.", "text": raw}
    # The worker's queue: pending transcripts (worker.py, list_by_status).
    pending = admin.call("GET", "/items/transcripts", params={
        "filter": json.dumps({"postprocess_status": {"_eq": "pending"}, "recording": {"_eq": rid}}), "fields": "id"})
    assert [p["id"] for p in pending] == [row["id"]]

    busy = call_tool(base, user.token, "restore_raw_transcript", {"id": rid, "confirm": True})
    assert tool_message(busy).startswith("busy:")

    output = "## Befund\n\nUnauffällig."
    admin.call("PATCH", f"/items/transcripts/{row['id']}", json={"postprocess_status": "processing",
                                                                  "postprocess_job_id": 7})
    admin.call("PATCH", f"/items/transcripts/{row['id']}", json={
        "text": output, "raw_text": raw, "applied_profile": "transcript-polish-de", "processed_text": output,
        "postprocess_status": "done", "postprocess_job_id": None, "postprocess_job_key": None,
        "postprocess_error": None, "postprocess_queue_reason": None, "postprocess_instructions": None})
    done = tool_payload(call_tool(base, user.token, "get_dictation", {"id": rid}))
    assert (done["postprocess_status"], done["applied_profile"], done["text"]) == ("done", "transcript-polish-de", output)

    restored = tool_payload(call_tool(base, user.token, "restore_raw_transcript", {"id": rid}))
    assert restored["text_sha256"] == text_sha256(raw)
    after = rows(admin, "transcripts", recording=rid)[0]
    assert (after["text"], after["raw_text"], after["applied_profile"], after["processed_text"]) == (raw, None, None, None)
