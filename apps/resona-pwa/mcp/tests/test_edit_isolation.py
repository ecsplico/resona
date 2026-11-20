"""The write tools against a real Directus: the fields they change, conflict, confirm, two users.

Each test makes its own users, so the dictations of ``stack`` (read by
``test_isolation.py``) stay as they are.
"""
import json
import logging

from .conftest import tool_error, tool_payload
from .test_edit_tools import tool_message
from .test_isolation import call_tool, call_tools


def rows(admin, collection: str, **filter):
    """Rows as the admin sees them (also those the users' policy hides)."""
    query = {"filter": json.dumps({k: {"_eq": v} for k, v in filter.items()}), "fields": "*"}
    return admin.call("GET", f"/items/{collection}", params=query)


def test_edits_change_the_fields_the_app_changes(base, directus_admin, caplog):
    admin, user = directus_admin, directus_admin.user("edit")
    rid = user.dictation("Diktat vom 3.3.", "Alter Text.", title_auto=True)
    before = rows(admin, "transcripts", recording=rid)[0]

    read = tool_payload(call_tool(base, user.token, "get_dictation", {"id": rid}))
    with caplog.at_level(logging.INFO, logger="resona_dictations_mcp"):
        edited, renamed, released = (tool_payload(r) for r in call_tools(
            base, user.token,
            ("update_dictation_text", {"id": rid, "text": "Neuer Geheimtext.", "expected_sha256": read["text_sha256"]}),
            ("rename_dictation", {"id": rid, "title": "  Visite Station 3  "}),
            ("set_review_status", {"id": rid, "status": "ready"}),
        ))
    assert "Geheimtext" not in caplog.text and "Station 3" not in caplog.text

    transcript = rows(admin, "transcripts", recording=rid)[0]
    assert transcript["text"] == "Neuer Geheimtext."
    assert {k: v for k, v in transcript.items() if k not in ("text", "date_updated", "user_updated")} == \
        {k: v for k, v in before.items() if k not in ("text", "date_updated", "user_updated")}
    recording = rows(admin, "recordings", id=rid)[0]
    assert (recording["title"], recording["title_auto"], recording["review_status"]) == \
        ("Visite Station 3", False, "ready")
    assert "text" not in edited and edited["text_length"] == len("Neuer Geheimtext.")
    assert renamed["title"] == "Visite Station 3" and released["review_status"] == "ready"


def test_a_stale_hash_is_a_conflict_and_writes_nothing(base, directus_admin):
    admin, user = directus_admin, directus_admin.user("conflict")
    rid = user.dictation("Konflikt", "Stand beim Lesen.")
    read = tool_payload(call_tool(base, user.token, "get_dictation", {"id": rid}))
    # The user edits the text in the app meanwhile.
    tid = rows(admin, "transcripts", recording=rid)[0]["id"]
    admin.call("PATCH", f"/items/transcripts/{tid}", user.token, json={"text": "In der App geändert."})

    error = tool_message(call_tool(base, user.token, "update_dictation_text",
                                   {"id": rid, "text": "Vom Modell.", "expected_sha256": read["text_sha256"]}))
    assert error.startswith("conflict:")
    assert rows(admin, "transcripts", recording=rid)[0]["text"] == "In der App geändert."


def test_delete_needs_confirm_and_removes_transcript_and_parts(base, directus_admin):
    admin, user = directus_admin, directus_admin.user("delete")
    rid = user.dictation("Wird gelöscht", "Text.")
    audio = admin.call("POST", "/files", user.token, files={"file": ("part.wav", b"RIFF0000WAVEfmt ", "audio/wav")})
    admin.call("POST", "/items/recording_parts", user.token,
               json={"recording": rid, "seq": 1, "status": "pending", "audio": audio["id"]})

    refused = call_tools(base, user.token, ("delete_dictation", {"id": rid}),
                         ("delete_dictation", {"id": rid, "confirm": False}))
    assert all(tool_message(r).startswith("confirm_required:") for r in refused)
    assert rows(admin, "recordings", id=rid) and rows(admin, "recording_parts", recording=rid)

    deleted = tool_payload(call_tool(base, user.token, "delete_dictation", {"id": rid, "confirm": True}))
    assert deleted["deleted"] is True and deleted["id"] == rid
    assert rows(admin, "recordings", id=rid) == []
    assert rows(admin, "transcripts", recording=rid) == []
    assert rows(admin, "recording_parts", recording=rid) == []
    assert "not found" in tool_error(call_tool(base, user.token, "get_dictation", {"id": rid}))


def test_user_a_cannot_change_user_bs_dictations(base, directus_admin):
    admin = directus_admin
    a, b = admin.user("edit-a"), admin.user("edit-b")
    rid = b.dictation("Visite Beta", "Text von B.")
    recording, transcript = rows(admin, "recordings", id=rid)[0], rows(admin, "transcripts", recording=rid)[0]

    results = call_tools(
        base, a.token,
        ("update_dictation_text", {"id": rid, "text": "Von A."}),
        ("rename_dictation", {"id": rid, "title": "Von A"}),
        ("set_review_status", {"id": rid, "status": "ready"}),
        ("delete_dictation", {"id": rid, "confirm": True}),
    )
    assert all("not found" in tool_error(r) for r in results)
    assert rows(admin, "recordings", id=rid) == [recording]
    assert rows(admin, "transcripts", recording=rid) == [transcript]
    # B still can.
    assert tool_payload(call_tool(base, b.token, "rename_dictation", {"id": rid, "title": "Neu"}))["title"] == "Neu"
