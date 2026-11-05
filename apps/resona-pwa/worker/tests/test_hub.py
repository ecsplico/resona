import json

import httpx
import pytest
import respx

from resona_project_worker.hub import HubClient, HubError

HUB = "http://hub.test"


@respx.mock
@pytest.mark.anyio
async def test_upload_sends_file_with_bearer(tmp_path):
    route = respx.post(f"{HUB}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "up-1", "sha256": "x", "size": 3})
    )
    path = tmp_path / "a.bin"
    path.write_bytes(b"abc")
    hub = HubClient(HUB, "tok")
    assert await hub.upload(path, "a.wav") == "up-1"
    await hub.aclose()
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert b'name="file"' in req.content
    assert b"abc" in req.content


@respx.mock
@pytest.mark.anyio
async def test_create_job_omits_none_and_sends_private():
    route = respx.post(f"{HUB}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 7, "status": "pending"})
    )
    hub = HubClient(HUB, "tok")
    job_id = await hub.create_job(
        kind="audio", upload_id="up-1", idempotency_key="k-1", language="de",
    )
    await hub.aclose()
    assert job_id == 7
    req = route.calls.last.request
    assert req.headers["Idempotency-Key"] == "k-1"
    assert json.loads(req.content) == {
        "kind": "audio", "upload_id": "up-1", "private": True, "language": "de",
    }


@respx.mock
@pytest.mark.anyio
async def test_error_body_maps_to_hub_error():
    respx.get(f"{HUB}/v1/jobs/7").mock(
        return_value=httpx.Response(404, json={"error": "not_found", "message": "no job"})
    )
    hub = HubClient(HUB, "tok")
    with pytest.raises(HubError) as info:
        await hub.get_job(7)
    await hub.aclose()
    assert info.value.status == 404
    assert info.value.code == "not_found"
    assert "HTTP 404" in str(info.value)
    assert "no job" in str(info.value)


@respx.mock
@pytest.mark.anyio
async def test_error_without_body_uses_http_code():
    respx.get(f"{HUB}/v1/jobs/7/result").mock(return_value=httpx.Response(410))
    hub = HubClient(HUB, "tok")
    with pytest.raises(HubError) as info:
        await hub.get_result(7)
    await hub.aclose()
    assert info.value.status == 410
    assert info.value.code == "http_410"


@respx.mock
@pytest.mark.anyio
async def test_connect_error_is_status_zero():
    respx.get(f"{HUB}/v1/jobs/7").mock(side_effect=httpx.ConnectError("refused"))
    hub = HubClient(HUB, "tok")
    with pytest.raises(HubError) as info:
        await hub.get_job(7)
    await hub.aclose()
    assert info.value.status == 0
    assert "nicht erreichbar" in str(info.value)


@respx.mock
@pytest.mark.anyio
async def test_timeout_is_status_zero():
    respx.get(f"{HUB}/v1/jobs/7").mock(side_effect=httpx.ReadTimeout("slow"))
    hub = HubClient(HUB, "tok")
    with pytest.raises(HubError) as info:
        await hub.get_job(7)
    await hub.aclose()
    assert info.value.status == 0
    assert "Zeitüberschreitung" in str(info.value)


@respx.mock
@pytest.mark.anyio
async def test_get_result_and_delete():
    respx.get(f"{HUB}/v1/jobs/7/result").mock(
        return_value=httpx.Response(200, json={"markdown": "hallo", "segments": []})
    )
    delete = respx.delete(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(204))
    hub = HubClient(HUB, "tok")
    assert (await hub.get_result(7))["markdown"] == "hallo"
    await hub.delete_job(7)
    await hub.aclose()
    assert delete.called
