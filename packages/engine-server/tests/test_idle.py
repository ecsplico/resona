import asyncio
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from resona_asr_core import registry
from resona_asr_core.protocol import TranscriptionResult
from resona_engine_server import app as app_module
from resona_engine_server.idle import IdleUnloader, parse_unload_after


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeModel:
    """Stands in for the registry singleton: loaded flag + release."""

    def __init__(self, loaded: bool = True):
        self.is_loaded = loaded
        self.releases = 0
        self.frees = 0

    def loaded(self) -> bool:
        return self.is_loaded

    def release(self) -> bool:
        was = self.is_loaded
        self.is_loaded = False
        self.releases += 1
        return was

    def free(self) -> None:
        self.frees += 1


def _unloader(unload_after, clock, model, **kwargs):
    return IdleUnloader(
        unload_after,
        clock=clock,
        release=model.release,
        loaded=model.loaded,
        free_memory=model.free,
        **kwargs,
    )


@pytest.mark.parametrize(
    "value, seconds",
    [("never", None), ("", None), ("NEVER", None), ("immediately", 0.0),
     ("0", 0.0), ("5", 300.0), ("0.5", 30.0)],
)
def test_parse_unload_after(value, seconds):
    assert parse_unload_after(value) == seconds


@pytest.mark.parametrize("value", ["-1", "soon", "nan", "inf"])
def test_parse_unload_after_rejects(value):
    with pytest.raises(ValueError, match="RESONA_ENGINE_UNLOAD_AFTER"):
        parse_unload_after(value)


def test_unloads_after_idle_timeout():
    clock, model = FakeClock(), FakeModel()
    unloader = _unloader(60.0, clock, model)
    clock.now += 59
    assert unloader.maybe_unload() is False
    assert model.is_loaded
    clock.now += 1
    assert unloader.maybe_unload() is True
    assert not model.is_loaded
    assert model.frees == 1


def test_never_does_not_unload():
    clock, model = FakeClock(), FakeModel()
    unloader = _unloader(None, clock, model)
    clock.now += 10**9
    assert unloader.maybe_unload() is False
    assert model.is_loaded


def test_in_flight_request_blocks_unload():
    clock, model = FakeClock(), FakeModel()
    unloader = _unloader(60.0, clock, model)
    unloader.begin()
    clock.now += 3600
    assert unloader.maybe_unload() is False
    assert model.is_loaded
    unloader.end()
    # The finished request counts as the last use.
    clock.now += 59
    assert unloader.maybe_unload() is False
    clock.now += 1
    assert unloader.maybe_unload() is True


def test_use_restarts_idle_clock():
    clock, model = FakeClock(), FakeModel()
    unloader = _unloader(60.0, clock, model)
    clock.now += 50
    with unloader.use():
        pass
    clock.now += 50
    assert unloader.maybe_unload() is False
    clock.now += 10
    assert unloader.maybe_unload() is True


def test_immediately_unloads_when_last_request_ends():
    clock, model = FakeClock(), FakeModel()
    unloader = _unloader(0.0, clock, model)
    unloader.begin()
    with unloader.use():
        pass
    assert model.is_loaded  # first request still in flight
    unloader.end()
    assert not model.is_loaded
    assert model.releases == 1


def test_not_loaded_is_not_released_twice():
    clock, model = FakeClock(), FakeModel(loaded=False)
    unloader = _unloader(1.0, clock, model)
    clock.now += 10
    assert unloader.maybe_unload() is False
    assert model.releases == 0 and model.frees == 0


@pytest.mark.anyio
async def test_background_loop_unloads():
    clock, model = FakeClock(), FakeModel()
    unloader = _unloader(60.0, clock, model, poll_interval=0.01)
    task = asyncio.create_task(unloader.run())
    try:
        await asyncio.sleep(0.05)
        assert model.is_loaded
        clock.now += 61
        for _ in range(100):
            if not model.is_loaded:
                break
            await asyncio.sleep(0.01)
        assert not model.is_loaded
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.fixture
def real_registry():
    """Real registry singleton with a mock transcriber factory."""
    registry.reset()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="hallo", language="de", segments=[]
    )
    factory = MagicMock(return_value=transcriber)
    with patch.object(registry, "_load_from_entrypoint", factory):
        yield factory
    registry.reset()


def _post(client):
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        return client.post(
            "/transcribe",
            files={"audio_file": ("t.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )


def test_app_unloads_and_reloads(real_registry, monkeypatch):
    clock = FakeClock()
    unloader = IdleUnloader(60.0, clock=clock, free_memory=lambda: None)
    monkeypatch.setattr(app_module, "unloader", unloader)
    with patch("resona_engine_server.auth.config", return_value=None):
        client = TestClient(app_module.app)
        assert client.get("/health").json()["loaded"] is False

        assert _post(client).json()["text"] == "hallo"
        assert real_registry.call_count == 1
        assert client.get("/health").json()["loaded"] is True

        clock.now += 61
        assert unloader.maybe_unload() is True
        assert client.get("/health").json()["loaded"] is False

        assert _post(client).json()["text"] == "hallo"
        assert real_registry.call_count == 2
        assert client.get("/health").json()["loaded"] is True


def test_app_immediately_unloads_after_each_request(real_registry, monkeypatch):
    unloader = IdleUnloader(0.0, clock=FakeClock(), free_memory=lambda: None)
    monkeypatch.setattr(app_module, "unloader", unloader)
    with patch("resona_engine_server.auth.config", return_value=None):
        client = TestClient(app_module.app)
        assert _post(client).status_code == 200
        assert client.get("/health").json()["loaded"] is False
        assert _post(client).status_code == 200
        assert real_registry.call_count == 2
        assert unloader.in_flight == 0
