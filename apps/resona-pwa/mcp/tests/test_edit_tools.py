"""The write tools against ``shared/dictationEdit.cases.json``: the writes the app makes.

The same cases drive the app's ``test/dictationEdit.spec.ts``. No Directus needed: a
fake records every write; ``test_edit_isolation.py`` runs the tools against a real one.
"""
import json
import re
from pathlib import Path

import anyio
import pytest
from mcp import Client

from resona_dictations_mcp.directus import NotFound
from resona_dictations_mcp.server import Caller, build_server, text_sha256

from .conftest import tool_error, tool_payload

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "dictationEdit.cases.json").read_text("utf-8"))
RECORDING, TRANSCRIPT = CASES["recording"], CASES["transcript"]
TOOLS = {
    "update_text": "update_dictation_text",
    "rename": "rename_dictation",
    "set_review_status": "set_review_status",
    "delete": "delete_dictation",
    "postprocess": "postprocess_dictation",
    "restore_raw": "restore_raw_transcript",
}


def tool_message(result) -> str:
    """The tool's own error text, without the SDK's 'Error executing tool <name>: ' prefix."""
    return re.sub(r"^Error executing tool \w+: ", "", tool_error(result))


class FakeDirectus:
    """One recording of the caller, its transcript (or none), and a log of writes."""

    def __init__(self, transcript):
        self.row = {"id": RECORDING, "title": "Diktat", "date_created": "2026-03-01T10:00:00.000Z",
                    "duration_seconds": 12.5, "language": "de", "status": "done", "review_status": "editing"}
        self.transcript = None if transcript is None else {"id": TRANSCRIPT, "recording": RECORDING, **transcript}
        self.writes = []

    def _own(self, collection, item_id):
        if collection != "recordings" or item_id != RECORDING:
            raise NotFound()

    async def item(self, collection, item_id, *, fields):
        self._own(collection, item_id)
        return dict(self.row)

    async def items(self, collection, *, fields, filter=None, sort=None, limit, offset=0):
        if collection == "transcripts" and self.transcript is not None:
            return [dict(self.transcript)]
        return []

    async def create(self, collection, data, *, fields):
        self.writes.append({"op": "create", "collection": collection, "data": data})
        if collection == "transcripts":
            self.transcript = {"id": TRANSCRIPT, **data}
        return {"id": TRANSCRIPT}

    async def update(self, collection, item_id, data, *, fields):
        if collection == "recordings":
            self._own(collection, item_id)  # Directus refuses before writing
        self.writes.append({"op": "update", "collection": collection, "id": item_id, "data": data})
        target = self.row if collection == "recordings" else self.transcript
        target.update(data)
        return dict(target)

    async def delete(self, collection, item_id):
        self._own(collection, item_id)
        self.writes.append({"op": "delete", "collection": collection, "id": item_id})


def run(fake, tool, args):
    server = build_server(lambda ctx: Caller(user_id="u", directus=fake))

    async def go():
        async with Client(server) as client:
            return await client.call_tool(tool, args)

    return anyio.run(go)


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_each_tool_writes_exactly_what_the_app_writes(case):
    fake = FakeDirectus(case.get("transcript"))
    result = run(fake, TOOLS[case["action"]], {"id": RECORDING, **case["input"]})

    ids = {"recording": RECORDING, "transcript": TRANSCRIPT}
    expected = [{**w, **({"id": ids[w["id"]]} if "id" in w else {})} for w in case["writes"]]
    assert fake.writes == expected
    if "error" in case:
        assert tool_message(result).startswith(f"{case['error']}:")
        return
    payload = tool_payload(result)
    assert payload["id"] == RECORDING
    assert "text" not in payload  # metadata only
    if case["action"] == "update_text":
        assert payload["text_sha256"] == text_sha256(case["input"]["text"])
        assert payload["text_length"] == len(case["input"]["text"])
    if case["action"] == "delete":
        assert payload["deleted"] is True
    if case["action"] == "postprocess":
        assert (payload["postprocess_status"], payload["postprocess_profile"]) == ("pending", case["input"]["profile"])
    if case["action"] == "restore_raw":
        assert payload["text_sha256"] == text_sha256(case["transcript"]["raw_text"])


def test_delete_without_confirm_deletes_nothing():
    fake = FakeDirectus({"text": "Alter Text.", "postprocess_status": None})
    assert tool_message(run(fake, "delete_dictation", {"id": RECORDING})).startswith("confirm_required:")
    assert fake.writes == []


def test_get_dictation_returns_the_hash_update_dictation_text_expects():
    stale = next(c for c in CASES["cases"] if c.get("error") == "conflict")
    fake = FakeDirectus({"text": "Alter Text.", "postprocess_status": None})
    read = tool_payload(run(fake, "get_dictation", {"id": RECORDING}))
    assert read["text_sha256"] == stale["input"]["expected_sha256"] == text_sha256("Alter Text.")


def test_a_foreign_id_writes_nothing():
    other = "00000000-0000-4000-8000-000000000000"
    for tool, args in [("update_dictation_text", {"text": "x"}), ("rename_dictation", {"title": "x"}),
                       ("set_review_status", {"status": "ready"}), ("delete_dictation", {"confirm": True})]:
        fake = FakeDirectus({"text": "Alter Text.", "postprocess_status": None})
        result = run(fake, tool, {"id": other, **args})
        assert tool_message(result).startswith("not found"), tool
        assert fake.writes == [], tool


def test_delete_description_says_it_cannot_be_undone():
    async def go():
        async with Client(build_server(lambda ctx: None)) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    tool = anyio.run(go)["delete_dictation"]
    assert "cannot be undone" in tool.description
    assert tool.input_schema["properties"]["confirm"]["default"] is False
