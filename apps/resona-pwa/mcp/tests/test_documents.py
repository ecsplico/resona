"""Document tools against a real Directus (see conftest): two users, one service.

Rows are created as the app does (``POST /items/documents`` with the user's token);
the hub result (``markdown``, ``pages``, ``status``) is written with the admin token,
as the project-worker does.
"""
import logging
import uuid

import anyio
import pytest
from mcp import Client

from resona_dictations_mcp.server import Caller, build_server

from .conftest import tool_error, tool_payload
from .test_isolation import call_tool, call_tools

A_MARKDOWN = "# Arztbrief Alpha-Dokumentwort\n\nDiagnose: Migräne.\n\n" * 4
B_MARKDOWN = "# Befund Beta-Dokumentwort\n\nKeine Auffälligkeiten."
# The SDK puts "Error executing tool <name>: " before every ToolError text (not-found too).
CONFIRM_REQUIRED = ("Error executing tool delete_document: confirm_required: pass confirm=true to "
                    "delete; this cannot be undone. Nothing was deleted.")


def make_document(stack, user, title, markdown=None, pages=None):
    row = stack.admin.call("POST", "/items/documents", user.token, json={"title": title, "status": "pending"})
    if markdown is not None:
        stack.admin.call("PATCH", f"/items/documents/{row['id']}",
                         json={"markdown": markdown, "pages": pages or [], "status": "done"})
    return row["id"]


@pytest.fixture(scope="module")
def docs(stack):
    a = [
        make_document(stack, stack.a, "Arztbrief Alpha", A_MARKDOWN, pages=[{"page": 1}, {"page": 2}]),
        make_document(stack, stack.a, "Laborwerte zwei", "Kurz."),
        make_document(stack, stack.a, "Noch in Arbeit"),
    ]
    b = [make_document(stack, stack.b, "Befund Beta", B_MARKDOWN)]
    return {"a": a, "b": b}


def own_document_ids(stack, user):
    return {row["id"] for row in stack.admin.call(
        "GET", "/items/documents", user.token, params={"fields": "id", "limit": -1})}


def test_each_user_lists_only_their_own_documents(base, stack, docs):
    mine = tool_payload(call_tool(base, stack.a.token, "list_documents", {"limit": 50}))
    theirs = tool_payload(call_tool(base, stack.b.token, "list_documents", {"limit": 50}))

    mine_ids = {d["id"] for d in mine["documents"]}
    assert set(docs["a"]) <= mine_ids and not mine_ids & set(docs["b"])
    assert mine_ids == own_document_ids(stack, stack.a)
    assert {d["id"] for d in theirs["documents"]} == own_document_ids(stack, stack.b)
    assert not {d["id"] for d in theirs["documents"]} & set(docs["a"])

    by_id = {d["id"]: d for d in mine["documents"]}
    first = by_id[docs["a"][0]]
    assert first == {
        "id": docs["a"][0], "title": "Arztbrief Alpha", "created": first["created"],
        "updated": first["updated"], "status": "done", "markdown_length": len(A_MARKDOWN),
    }
    assert by_id[docs["a"][2]]["markdown_length"] is None
    assert by_id[docs["a"][2]]["status"] == "pending"


def test_list_documents_is_newest_first_and_pages_with_the_cursor(base, stack, docs):
    whole = tool_payload(call_tool(base, stack.a.token, "list_documents", {"limit": 50}))["documents"]
    keys = [(d["created"], d["id"]) for d in whole]
    assert keys == sorted(keys, reverse=True)

    seen, cursor = [], None
    for _ in range(len(whole) + 1):
        args = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = tool_payload(call_tool(base, stack.a.token, "list_documents", args))
        seen += [d["id"] for d in page["documents"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [d["id"] for d in whole]

    first = tool_payload(call_tool(base, stack.a.token, "list_documents", {"limit": 1}))
    foreign = tool_payload(call_tool(base, stack.b.token, "list_documents", {"cursor": first["next_cursor"]}))
    assert not {d["id"] for d in foreign["documents"]} & own_document_ids(stack, stack.a)
    assert "list_documents" in tool_error(call_tool(base, stack.a.token, "list_documents", {"cursor": "nonsense"}))


def test_get_document_returns_metadata_and_markdown_ranges(base, stack, docs):
    did = docs["a"][0]
    first, rest, pending = call_tools(
        base, stack.a.token,
        ("get_document", {"id": did, "limit": 50}),
        ("get_document", {"id": did, "offset": 50}),
        ("get_document", {"id": docs["a"][2]}),
    )
    first, rest, pending = tool_payload(first), tool_payload(rest), tool_payload(pending)

    assert first["title"] == "Arztbrief Alpha" and first["status"] == "done"
    assert first["page_count"] == 2
    assert (first["offset"], first["next_offset"], first["total_chars"]) == (0, 50, len(A_MARKDOWN))
    assert rest["next_offset"] is None
    assert first["markdown"] + rest["markdown"] == A_MARKDOWN
    assert (pending["markdown"], pending["total_chars"], pending["markdown_length"]) == ("", 0, None)
    assert "limit" in tool_error(call_tool(base, stack.a.token, "get_document", {"id": did, "limit": 32769}))


def test_another_users_document_reads_like_a_missing_one(base, stack, docs):
    foreign, missing, malformed = call_tools(
        base, stack.a.token,
        ("get_document", {"id": docs["b"][0]}),
        ("get_document", {"id": str(uuid.uuid4())}),
        ("get_document", {"id": "../../users/me"}),
    )
    assert tool_error(foreign) == tool_error(missing)
    assert "not found: no such document" in tool_error(foreign)
    assert "Beta" not in tool_error(foreign)
    tool_error(malformed)
    assert tool_payload(call_tool(base, stack.b.token, "get_document", {"id": docs["b"][0]}))["title"] == "Befund Beta"


def test_rename_document_returns_metadata_only_and_is_owner_only(base, stack):
    mine = make_document(stack, stack.a, "Alter Titel", "Inhalt bleibt.")
    theirs = make_document(stack, stack.b, "Fremder Titel", B_MARKDOWN)

    renamed = tool_payload(call_tool(base, stack.a.token, "rename_document", {"id": mine, "title": "  Neuer Titel "}))
    assert set(renamed) == {"id", "title", "created", "updated", "status"}
    assert (renamed["id"], renamed["title"], renamed["status"]) == (mine, "Neuer Titel", "done")
    got = tool_payload(call_tool(base, stack.a.token, "get_document", {"id": mine}))
    assert (got["title"], got["markdown"]) == ("Neuer Titel", "Inhalt bleibt.")

    assert "blank" in tool_error(call_tool(base, stack.a.token, "rename_document", {"id": mine, "title": "   "}))
    foreign, missing = call_tools(
        base, stack.a.token,
        ("rename_document", {"id": theirs, "title": "Gekapert"}),
        ("rename_document", {"id": str(uuid.uuid4()), "title": "Gekapert"}),
    )
    assert tool_error(foreign) == tool_error(missing)
    assert tool_payload(call_tool(base, stack.b.token, "get_document", {"id": theirs}))["title"] == "Fremder Titel"


def test_delete_document_without_confirm_deletes_nothing(base, stack):
    did = make_document(stack, stack.a, "Bleibt bestehen", "Text.")
    absent, false = call_tools(
        base, stack.a.token,
        ("delete_document", {"id": did}),
        ("delete_document", {"id": did, "confirm": False}),
    )
    for result in (absent, false):
        assert tool_error(result) == CONFIRM_REQUIRED
    assert did in own_document_ids(stack, stack.a)
    assert tool_payload(call_tool(base, stack.a.token, "get_document", {"id": did}))["title"] == "Bleibt bestehen"


def test_delete_document_with_confirm_deletes_own_only(base, stack):
    mine = make_document(stack, stack.a, "Wird gelöscht", "Weg damit.")
    theirs = make_document(stack, stack.b, "Gehört B", B_MARKDOWN)

    foreign = call_tool(base, stack.a.token, "delete_document", {"id": theirs, "confirm": True})
    assert "not found: no such document" in tool_error(foreign)
    assert theirs in own_document_ids(stack, stack.b)

    deleted = tool_payload(call_tool(base, stack.a.token, "delete_document", {"id": mine, "confirm": True}))
    assert deleted == {"id": mine, "title": "Wird gelöscht", "deleted": True}
    assert mine not in own_document_ids(stack, stack.a)
    assert "not found" in tool_error(call_tool(base, stack.a.token, "get_document", {"id": mine}))


def test_document_tools_log_ids_but_no_title_or_markdown(base, stack, docs, caplog):
    did = docs["a"][0]
    with caplog.at_level(logging.INFO):
        call_tools(
            base, stack.a.token,
            ("list_documents", {}),
            ("get_document", {"id": did}),
            ("rename_document", {"id": did, "title": "Arztbrief Alpha"}),
            ("delete_document", {"id": did}),
        )
    logged = caplog.text
    assert f"tool=get_document user={stack.a.id} outcome=ok ids={did}" in logged
    assert f"tool=rename_document user={stack.a.id} outcome=ok ids={did}" in logged
    assert f"tool=delete_document user={stack.a.id} outcome=invalid" in logged
    for secret in ("Alpha", "Dokumentwort", "Migräne", "Arztbrief", stack.a.token):
        assert secret not in logged, secret


class RecordingDirectus:
    """No Directus: records every call, so a refused delete provably sent nothing."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def record(*args, **kwargs):
            self.calls.append(name)
            return {"id": args[1] if len(args) > 1 else "x", "title": "t"}
        return record


def test_delete_document_without_confirm_never_reaches_directus():
    fake = RecordingDirectus()
    server = build_server(lambda ctx: Caller(user_id="u", directus=fake))
    did = "00000000-0000-0000-0000-000000000000"

    async def go():
        async with Client(server) as client:
            return [await client.call_tool("delete_document", args)
                    for args in ({"id": did}, {"id": did, "confirm": False})]

    for result in anyio.run(go):
        assert tool_error(result) == CONFIRM_REQUIRED
    assert fake.calls == []
