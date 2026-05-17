# Testing

All tests use pytest. The workspace root `pyproject.toml` configures
`--import-mode=importlib` so packages do not need to be installed in
development mode for tests to find them (though they are, because of
`uv sync --all-packages`).

---

## Running tests

```bash
# All packages at once
uv run pytest

# Single package
uv run pytest packages/engine-server/tests/
uv run pytest packages/asr-core/tests/
uv run pytest packages/api/tests/
uv run pytest packages/client/tests/
uv run pytest packages/postprocess/tests/
uv run pytest apps/resona-cli/tests/

# Single test by name
uv run pytest -k test_transcribe

# Verbose output
uv run pytest -v packages/api/tests/test_endpoints.py
```

---

## Mocking strategy by package

Each package has a specific mock target. Using the wrong one leads to tests
that pass in isolation but break under integration.

| Package | What to mock | How |
|---|---|---|
| `resona-engine-server` | The transcriber singleton | `patch("resona_engine_server.app.get_transcriber", return_value=mock_t)` |
| `resona-api` | The engine HTTP call | `respx.mock` intercepting `POST /transcribe` on the engine URL |
| `resona-client` | The API HTTP call | `respx.mock` intercepting calls to the resona-api base URL |
| `resona-cli` | CLI entry points | `typer.testing.CliRunner`; patch client methods as needed |
| `resona-asr-core` | Entry-point discovery | `patch("resona_asr_core.registry.entry_points", return_value=[...])` |

### resona-engine-server

Mock at the singleton level so the FastAPI app never tries to load a real model:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from resona_asr_core.protocol import TranscriptionResult


@pytest.fixture
def mock_transcriber():
    t = MagicMock()
    t.transcribe.return_value = TranscriptionResult(
        text="raw transcript",
        language="de",
        segments=[{"start": 0.0, "end": 1.0, "text": "raw transcript"}],
    )
    return t


@pytest.fixture
def client(mock_transcriber):
    with patch("resona_engine_server.app.get_transcriber", return_value=mock_transcriber):
        with patch("resona_engine_server.auth.config", return_value=None):
            from resona_engine_server.app import app
            yield TestClient(app)
```

Also patch `load_audio` when sending raw bytes, so the audio parsing is bypassed:

```python
import numpy as np
from unittest.mock import patch

def test_transcribe(client, mock_transcriber):
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("t.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )
    assert resp.status_code == 200
```

### resona-api

Use `respx` to intercept the outbound httpx call to the engine. The tests
use a **real in-memory SQLite database** — see [Never mock the database](#never-mock-the-database).

```python
import respx
import httpx
import pytest

@pytest.fixture
def mock_engine(respx_mock):
    respx_mock.post("http://test-engine:9999/transcribe").mock(
        return_value=httpx.Response(
            200,
            json={"text": "hello", "language": "de", "segments": []},
        )
    )
```

The `RESONA_ENGINE_URL` env var in `conftest.py` is set to `http://test-engine:9999`
so all engine calls hit the respx mock.

### resona-client

Same pattern — use `respx.mock` to intercept calls to the resona-api base URL:

```python
import respx
import httpx
from resona_client.client import ResonaClient


def test_get_job():
    with respx.mock:
        respx.get("http://localhost:7000/jobs/123").mock(
            return_value=httpx.Response(200, json={"id": "123", "status": "COMPLETED"})
        )
        client = ResonaClient(base_url="http://localhost:7000")
        job = client.get_job("123")
    assert job["status"] == "COMPLETED"
```

### resona-cli

Use `typer.testing.CliRunner` to invoke commands without spawning a subprocess.
Patch the client or engine at the point where the CLI constructs it:

```python
from typer.testing import CliRunner
from unittest.mock import MagicMock, patch
from resona_cli.main import app

runner = CliRunner()


def test_transcribe_calls_gateway(tmp_path):
    wav = make_wav(tmp_path / "a.wav")   # helper that writes a minimal WAV
    mock_client = MagicMock()
    mock_client.create_transcription.return_value = {
        "text": "hello", "language": "de", "segments": []
    }

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(wav)])

    assert result.exit_code == 0
    mock_client.create_transcription.assert_called_once()
```

---

## Never mock the database

!!! warning "resona-api tests use a real SQLite database"
    Do not mock `Session`, `get_session`, or any SQLModel function in
    `resona-api` tests. The test suite creates a real in-memory SQLite
    database via `SQLModel.metadata.create_all(engine)` in `conftest.py` and
    truncates all tables between tests. Mocking the DB layer hides real SQL
    errors and makes tests meaningless.

The `conftest.py` fixtures that make this work:

```python
@pytest.fixture(scope="session", autouse=True)
def create_tables():
    from resona_api.db.engine import engine
    from resona_api.db.models import Job
    SQLModel.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def clean_db(create_tables):
    from resona_api.db.engine import engine
    with Session(engine) as session:
        session.execute(text("DELETE FROM job"))
        session.commit()
```

The environment variables in `conftest.py` must be set **before any
`resona_api` import**, because `paths.py` and `db/engine.py` read them at
module load time:

```python
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="resona_api_test_")
os.environ.setdefault("DATA_PATH", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/db/test.db")
```

---

## Audio fixtures

Keep audio fixtures small: **1–2 seconds, 16 kHz, mono WAV**. Larger files
slow down the test suite and add noise to diffs.

Place fixtures in `<pkg>/tests/fixtures/`:

```
packages/engine-server/tests/fixtures/sample.wav
packages/asr-core/tests/fixtures/silence.wav
```

Generate a minimal valid WAV in Python without any audio library:

```python
import io
import struct
import wave


def make_wav(path, frames: int = 160, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<" + "h" * frames, *([0] * frames)))
    data = buf.getvalue()
    path.write_bytes(data)
    return data
```

`160` frames at 16 kHz = 10 ms — short enough to be negligible but valid enough
for any WAV parser.

When you need a `numpy` array instead of bytes (for direct transcriber tests):

```python
import numpy as np

audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence at 16 kHz
```

---

## Test file layout

```
packages/engine-server/tests/
    conftest.py           # anyio backend fixture
    test_contract.py      # POST /transcribe contract — no md, no replacements, required fields
    test_app.py           # health endpoint, auth, error handling
    test_auth.py          # RESONA_ENGINE_KEY enforcement

packages/asr-core/tests/
    test_audio.py         # load_audio(), resampling
    test_protocol.py      # TranscriptionResult structure
    test_registry.py      # entry-point discovery, singleton, device detection

packages/api/tests/
    conftest.py           # DB setup, env vars, test_app fixture
    test_endpoints.py     # job CRUD, replacements, prompts
    test_audio_routes.py  # GET /v1/engines, POST /v1/audio/transcriptions, /speech
    test_engine_registry.py  # catalogue probing, resolve(), error hierarchy
    test_engine_client.py    # EngineClient.transcribe()
    test_job_lifecycle.py    # PENDING → COMPLETED with postprocessing
    test_tasks.py            # TranscribeTask background thread
    test_db_utils.py         # register_job(), get_active_replacements()
    test_cloud_routing.py    # cloud provider routing through audio_routes

packages/client/tests/
    test_client.py        # all ResonaClient methods via respx

packages/postprocess/tests/
    (replacements, llm, pipeline tests)

apps/resona-cli/tests/
    test_transcribe.py    # transcribe command via CliRunner
    test_engine.py        # RemoteEngine, InProcessEngine, CloudEngine
    test_engines.py       # engines subcommands (add, list, remove, status)
    test_watch.py         # watch command
    test_submit.py        # job submission helpers
    test_speech.py        # speech (TTS) command
    test_micrec.py        # RecordingSession, MicRecApp
    test_local_engine.py  # LocalEngine subprocess fallback
    test_cloud_engine.py  # CloudEngine via resona_cloud_stt
    test_extras.py        # optional dependency handling
    test_live_resample.py # live transcription resampling
```
