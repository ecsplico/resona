"""Against a real Directus (the app's compose image + bootstrap): two users, one service.

The service adds no owner filter of its own, so these tests pass only while Directus'
``user`` policy scopes every read.
"""
import logging
import uuid

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from resona_dictations_mcp.http import create_app

from .conftest import A_TEXT, INIT, headers, serve, tool_error, tool_payload


def call_tools(base: str, token: str, *calls: tuple[str, dict]):
    """One MCP session as ``token``: the results of ``calls`` in order."""
    async def go():
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with http, streamable_http_client(f"{base}/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [await session.call_tool(name, args) for name, args in calls]

    return anyio.run(go)


def call_tool(base: str, token: str, name: str, args: dict):
    return call_tools(base, token, (name, args))[0]


def test_each_user_lists_only_their_own_dictations(base, stack):
    mine = tool_payload(call_tool(base, stack.a.token, "list_dictations", {}))
    theirs = tool_payload(call_tool(base, stack.b.token, "list_dictations", {}))

    assert {d["id"] for d in mine["dictations"]} == set(stack.a_ids)
    assert {d["id"] for d in theirs["dictations"]} == set(stack.b_ids)
    assert mine["next_cursor"] is None

    by_id = {d["id"]: d for d in mine["dictations"]}
    first = by_id[stack.a_ids[0]]
    assert first == {
        "id": stack.a_ids[0], "title": "Visite Alpha eins", "created": first["created"],
        "duration_seconds": 12.5, "language": "de", "status": "done",
        "review_status": "editing", "text_length": len(A_TEXT),
    }
    assert by_id[stack.a_ids[2]]["text_length"] is None  # no transcript yet
    assert "text" not in first


def test_list_is_newest_first_and_pages_with_the_cursor(base, stack):
    whole = tool_payload(call_tool(base, stack.a.token, "list_dictations", {}))["dictations"]
    keys = [(d["created"], d["id"]) for d in whole]
    assert keys == sorted(keys, reverse=True)

    page1 = tool_payload(call_tool(base, stack.a.token, "list_dictations", {"limit": 2}))
    assert [d["id"] for d in page1["dictations"]] == [d["id"] for d in whole[:2]]
    assert page1["next_cursor"]
    page2 = tool_payload(call_tool(
        base, stack.a.token, "list_dictations", {"limit": 2, "cursor": page1["next_cursor"]}))
    assert [d["id"] for d in page2["dictations"]] == [whole[2]["id"]]
    assert page2["next_cursor"] is None

    # A cursor is no way around the policy: B paging from A's cursor sees only B's rows.
    foreign = tool_payload(call_tool(
        base, stack.b.token, "list_dictations", {"cursor": page1["next_cursor"]}))
    assert {d["id"] for d in foreign["dictations"]} <= set(stack.b_ids)


def test_list_filters(base, stack):
    def ids(**args):
        return {d["id"] for d in tool_payload(
            call_tool(base, stack.a.token, "list_dictations", args))["dictations"]}

    assert ids(status="pending") == {stack.a_ids[2]}
    assert ids(review_status="ready") == {stack.a_ids[1]}
    assert ids(since="2000-01-01") == set(stack.a_ids)
    assert ids(since="2999-01-01T00:00:00Z") == set()
    assert "ISO 8601" in tool_error(call_tool(base, stack.a.token, "list_dictations", {"since": "gestern"}))
    assert "cursor" in tool_error(call_tool(base, stack.a.token, "list_dictations", {"cursor": "nonsense"}))


def test_search_matches_title_and_text_of_own_dictations_only(base, stack):
    def search(token, query):
        return tool_payload(call_tool(base, token, "search_dictations", {"query": query}))["dictations"]

    by_text = search(stack.a.token, "alpha-geheimwort")
    assert [(d["id"], d["matched_in"]) for d in by_text] == [(stack.a_ids[0], ["text"])]
    assert "text" not in by_text[0]

    both = search(stack.a.token, "Alpha")
    assert [(d["id"], d["matched_in"]) for d in both] == [(stack.a_ids[0], ["title", "text"])]

    assert {d["id"] for d in search(stack.a.token, "Visite")} == {stack.a_ids[0]}
    assert search(stack.a.token, "Beta-Geheimwort") == []
    assert {d["id"] for d in search(stack.b.token, "Visite")} == set(stack.b_ids)


def test_get_dictation_returns_metadata_and_text_ranges(base, stack):
    rid = stack.a_ids[0]
    first, rest, pending = call_tools(
        base, stack.a.token,
        ("get_dictation", {"id": rid, "limit": 40}),
        ("get_dictation", {"id": rid, "offset": 40}),
        ("get_dictation", {"id": stack.a_ids[2]}),
    )
    first, rest, pending = tool_payload(first), tool_payload(rest), tool_payload(pending)

    assert first["title"] == "Visite Alpha eins" and first["status"] == "done"
    assert (first["offset"], first["next_offset"], first["total_chars"]) == (0, 40, len(A_TEXT))
    assert rest["next_offset"] is None
    assert first["text"] + rest["text"] == A_TEXT
    assert (pending["text"], pending["total_chars"], pending["text_length"]) == ("", 0, None)


def test_another_users_id_reads_like_a_missing_one(base, stack):
    foreign, missing, malformed = call_tools(
        base, stack.a.token,
        ("get_dictation", {"id": stack.b_ids[0]}),
        ("get_dictation", {"id": str(uuid.uuid4())}),
        ("get_dictation", {"id": "../../users/me"}),
    )
    assert tool_error(foreign) == tool_error(missing)
    assert "not found" in tool_error(foreign)
    assert "Beta" not in tool_error(foreign)
    tool_error(malformed)
    # ... while its owner reads it.
    assert tool_payload(call_tool(base, stack.b.token, "get_dictation", {"id": stack.b_ids[0]}))["title"] == "Visite Beta"


def test_missing_or_invalid_bearer_is_401_before_any_tool_runs(base, stack, caplog):
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_dictations", "arguments": {}}}
    with caplog.at_level(logging.INFO):
        for body in (INIT, call):
            for token in (None, "forged-token", stack.admin.stamp):
                resp = httpx.post(f"{base}/mcp", json=body, headers=headers(token))
                assert resp.status_code == 401
                assert resp.headers["www-authenticate"].startswith("Bearer")
    assert "tool=" not in caplog.text


def test_session_cannot_be_reused_with_another_users_token(base, stack):
    opened = httpx.post(f"{base}/mcp", json=INIT, headers=headers(stack.a.token))
    assert opened.status_code == 200
    common = {"mcp-session-id": opened.headers["mcp-session-id"], "mcp-protocol-version": "2025-06-18"}
    httpx.post(f"{base}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers=headers(stack.a.token, **common))
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_dictations", "arguments": {}}}

    assert httpx.post(f"{base}/mcp", json=call, headers=headers(stack.b.token, **common)).status_code == 404
    own = httpx.post(f"{base}/mcp", json=call, headers=headers(stack.a.token, **common))
    assert own.status_code == 200
    assert stack.a_ids[0] in own.text and stack.b_ids[0] not in own.text


def test_origin_and_host_allowlists(base, stack):
    def post(**extra):
        return httpx.post(f"{base}/mcp", json=INIT, headers=headers(stack.a.token, **extra)).status_code

    assert post(Origin="https://evil.example") == 403
    assert post(Origin="https://app.example") == 200
    assert post(Host="evil.example") == 421


def test_revoked_token_is_refused_within_the_cache_window(base, stack):
    """The 30 s cache still holds the token when Directus revokes it."""
    user = stack.admin.user("revoked")
    rid = user.dictation("Wird widerrufen", "Text vor dem Widerruf.")
    assert tool_payload(call_tool(base, user.token, "get_dictation", {"id": rid}))["total_chars"] == 22

    opened = httpx.post(f"{base}/mcp", json=INIT, headers=headers(user.token))
    assert opened.status_code == 200
    common = {"mcp-session-id": opened.headers["mcp-session-id"], "mcp-protocol-version": "2025-06-18"}
    httpx.post(f"{base}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers=headers(user.token, **common))
    stack.admin.revoke(user)

    # Cached as valid, but the data call runs with the token itself: no text comes back.
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_dictation", "arguments": {"id": rid}}}
    refused = httpx.post(f"{base}/mcp", json=call, headers=headers(user.token, **common))
    assert refused.status_code == 200
    assert "no longer valid" in refused.text and "Widerruf" not in refused.text
    # That rejection dropped the cache entry: from now on the token is 401.
    assert httpx.post(f"{base}/mcp", json=call, headers=headers(user.token, **common)).status_code == 401
    assert httpx.post(f"{base}/mcp", json=INIT, headers=headers(user.token)).status_code == 401


def test_revoked_token_is_401_once_the_cache_entry_expires(stack):
    user = stack.admin.user("expired")
    app = create_app(directus_url=stack.admin.url, allowed_hosts=["127.0.0.1:*"], token_ttl=0.3)
    with serve(app) as url:
        assert httpx.post(f"{url}/mcp", json=INIT, headers=headers(user.token)).status_code == 200
        stack.admin.revoke(user)
        anyio.run(anyio.sleep, 0.4)
        assert httpx.post(f"{url}/mcp", json=INIT, headers=headers(user.token)).status_code == 401


def test_a_full_read_session_logs_ids_but_no_text_and_no_token(base, stack, caplog):
    rid = stack.a_ids[0]
    with caplog.at_level(logging.INFO):
        results = call_tools(
            base, stack.a.token,
            ("list_dictations", {}),
            ("search_dictations", {"query": "Geheimwort"}),
            ("get_dictation", {"id": rid}),
            ("get_dictation", {"id": stack.b_ids[0]}),
        )
    assert A_TEXT in tool_payload(results[2])["text"]  # the text did travel to the client

    logged = caplog.text
    for tool in ("list_dictations", "search_dictations", "get_dictation"):
        assert f"tool={tool} user={stack.a.id} outcome=ok" in logged
    assert f"tool=get_dictation user={stack.a.id} outcome=ok ids={rid}" in logged
    assert f"tool=get_dictation user={stack.a.id} outcome=not_found" in logged
    for secret in ("Geheimwort", "Kopfschmerzen", "Schwindel", "Visite", "Befund",
                   stack.a.token, stack.b.token, "Bearer"):
        assert secret not in logged, secret


@pytest.mark.parametrize("who", ["a", "b"])
def test_the_service_never_sees_more_than_the_users_policy_allows(base, stack, who):
    """Directus' own answer with the user's token is the upper bound of what tools return."""
    user = getattr(stack, who)
    visible = {row["id"] for row in stack.admin.call(
        "GET", "/items/recordings", user.token, params={"fields": "id", "limit": -1})}
    listed = {d["id"] for d in tool_payload(call_tool(base, user.token, "list_dictations", {"limit": 50}))["dictations"]}
    assert listed == visible == set(getattr(stack, f"{who}_ids"))
