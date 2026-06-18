import httpx
import pytest
import respx

from resona_directus_transcribe.transcribe import TranscribeClient

API = "http://resona-api.test"


@respx.mock
@pytest.mark.anyio
async def test_transcribe_posts_multipart_verbose_json(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")
    route = respx.post(f"{API}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={
            "text": "hallo welt", "language": "de",
            "segments": [{"start": 0, "end": 1}],
        })
    )
    client = TranscribeClient(base_url=API, api_key="")
    result = await client.transcribe(audio, language="de", profile="default")
    await client.aclose()

    assert result["text"] == "hallo welt"
    assert result["language"] == "de"
    assert result["segments"] == [{"start": 0, "end": 1}]
    # structured absent in response -> defaults to None
    assert result["structured"] is None

    req = route.calls.last.request
    assert b'name="response_format"' in req.content
    assert b"verbose_json" in req.content
    assert b'name="language"' in req.content
    assert b'name="profile"' in req.content
