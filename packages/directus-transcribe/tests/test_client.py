import httpx
import pytest
import respx

from resona_directus_transcribe.client import DirectusClient

TOKEN = "svc-token"


@respx.mock
@pytest.mark.anyio
async def test_list_pending_filters_and_authenticates(base_url, recording):
    route = respx.get(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(200, json={"data": [recording]})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    result = await client.list_pending(limit=10)
    await client.aclose()

    assert result == [recording]
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.url.params["filter[status][_eq]"] == "pending"
    assert req.url.params["limit"] == "10"


@respx.mock
@pytest.mark.anyio
async def test_claim_patches_status_to_transcribing(base_url):
    route = respx.patch(f"{base_url}/items/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "rec-1", "status": "transcribing"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    ok = await client.claim("rec-1")
    await client.aclose()

    assert ok is True
    import json
    assert json.loads(route.calls.last.request.content) == {"status": "transcribing"}
