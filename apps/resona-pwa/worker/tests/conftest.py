import pytest


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    # Repo convention: drive async tests with anyio (no pytest-asyncio dep).
    return request.param


@pytest.fixture
def recording():
    return {
        "id": "rec-1",
        "title": "Befund",
        "audio_file": "file-1",
        "language": "de",
        "profile": "default",
        "status": "pending",
    }


@pytest.fixture
def document():
    return {
        "id": "doc-1",
        "title": "Arztbrief",
        "file": "file-2",
        "status": "pending",
    }


@pytest.fixture
def base_url():
    return "http://directus.test"
