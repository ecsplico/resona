"""Remote live-transcription backends for the `resona live` TUI.

Both backends expose the *same* pull-based surface as the in-process
:class:`resona_asr_core.live_transcriber.LiveTranscriber` — ``add_audio``,
``has_enough_audio``, ``process_sync``, ``flush_sync``, ``get_full_transcript``,
and the ``_audio_event_sync`` wake event — so :class:`WSLiveApp` drives any of
them with identical worker/feed loops.

* :class:`RemoteLiveTranscriber` → an engine-server ``/ws/live`` (Resona-native
  JSON: ``partial``/``final`` with deltas). Used for ``resona live --remote URL``.
* :class:`GatewayLiveTranscriber` → a resona-api ``/v1/listen`` (Deepgram wire
  protocol: ``Results``/``Metadata``). Used for ``resona live --remote URL
  --engine deepgram|elevenlabs``, letting the TUI drive cloud streaming.

Shared threading lives in :class:`_BaseRemoteLive`; subclasses override only the
URL, the finish control frame, and the message classifier.
"""
import base64
import json
import logging
import os
import queue
import threading
from urllib.parse import parse_qsl, urlencode

import numpy as np

from resona_asr_core.live_transcriber import TranscriptionResult

log = logging.getLogger(__name__)

# Both engine /ws/live and the Deepgram /v1/listen bridge accept 16 kHz mono
# int16 PCM, carried as a base64 JSON {"type":"audio",...} frame.
WS_SAMPLE_RATE = 16000


def _normalize_ws_base(url: str) -> str:
    """Coerce http(s)/ws(s)/bare-host to a ``ws(s)://`` base (no path changes)."""
    u = url.strip()
    if u.startswith("http://"):
        return "ws://" + u[len("http://"):]
    if u.startswith("https://"):
        return "wss://" + u[len("https://"):]
    if u.startswith(("ws://", "wss://")):
        return u
    return "ws://" + u


def _build_ws_url(url: str, default_path: str, params: dict) -> str:
    """Normalize ``url`` to ws(s), ensure ``default_path``, and merge ``params``.

    Existing query params on ``url`` win over ``params`` (so a caller-supplied
    ``?token=`` or ``?language=`` is never clobbered).
    """
    base, _, query = _normalize_ws_base(url).partition("?")
    if default_path not in base:
        base = base.rstrip("/") + default_path
    merged = dict(params)
    merged.update(dict(parse_qsl(query)))  # caller query overrides defaults
    return f"{base}?{urlencode(merged)}"


def _to_ws_url(url: str, language: str) -> str:
    """Engine-server ``/ws/live`` URL with ``?language=`` (kept for callers/tests)."""
    return _build_ws_url(url, "/ws/live", {"language": language})


def _pcm_b64(audio_float32: np.ndarray) -> str:
    """Encode mono float32 [-1, 1] @ 16 kHz as base64 int16 little-endian PCM."""
    clipped = np.clip(audio_float32, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    return base64.b64encode(pcm).decode("ascii")


def _default_connect():
    from websockets.sync.client import connect
    return connect


class _BaseRemoteLive:
    """Shared duplex streaming machinery; subclasses define the wire protocol."""

    def __init__(self, url: str, language: str = "de", *, connect=None, headers=None):
        self.language = language
        self._url = url
        self._headers = headers or {}
        self._connect = connect  # injectable for tests; defaults to websockets
        self._ws = None
        self._connect_error: Exception | None = None

        self._send_q: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._audio_event_sync = threading.Event()
        self._stop = threading.Event()        # stop sending → triggers finish frame
        self._closed = threading.Event()       # tear down the connection
        self._stopped_event = threading.Event()  # upstream signalled end-of-stream

        self._confirmed = ""
        self._run_thread: threading.Thread | None = None
        self._recv_thread: threading.Thread | None = None

    # ── subclass hooks ───────────────────────────────────────────────

    def _finish_frame(self) -> str:
        """JSON control frame telling the upstream to flush and finalize."""
        raise NotImplementedError

    def _classify(self, data: dict):
        """Map an upstream JSON message to one of:

        * ``("result", (confirmed_full | None, delta, partial))``
        * ``("end", None)`` — upstream finished
        * ``("error", message)``
        * ``None`` — ignore
        """
        raise NotImplementedError

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        self._run_thread = threading.Thread(target=self._run, daemon=True)
        self._run_thread.start()

    def _run(self) -> None:
        try:
            connect = self._connect or _default_connect()
            kwargs = {"open_timeout": 10}
            if self._headers:
                kwargs["additional_headers"] = self._headers
            self._ws = connect(self._url, **kwargs)
            log.info("Live remote connected: %s", self._url)
        except Exception as e:  # noqa: BLE001 - surface any connection failure
            self._connect_error = e
            log.error("Live remote connection failed: %s", e)
            self._results.put(("error", str(e)))
            self._audio_event_sync.set()
            return

        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        self._send_loop()

    def _send_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._send_q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._ws.send(item)
            except Exception as e:  # noqa: BLE001
                log.error("Live remote send failed: %s", e)
                return
        try:
            self._ws.send(self._finish_frame())
        except Exception:  # noqa: BLE001
            pass

    def _recv_loop(self) -> None:
        while not self._closed.is_set():
            try:
                msg = self._ws.recv(timeout=1.0)
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001 - closed / disconnected
                break
            if isinstance(msg, (bytes, bytearray)):
                continue
            try:
                data = json.loads(msg)
            except (TypeError, ValueError):
                continue
            action = self._classify(data)
            if action is None:
                continue
            kind = action[0]
            if kind == "result":
                self._results.put(action)
                self._audio_event_sync.set()
            elif kind == "end":
                self._stopped_event.set()
                break
            elif kind == "error":
                log.error("Live remote stream error: %s", action[1])
                self._results.put(action)
                self._audio_event_sync.set()

    def close(self) -> None:
        self._closed.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    # ── LiveTranscriber-compatible surface ───────────────────────────

    def add_audio(self, audio: np.ndarray) -> None:
        """Queue a mono float32 16 kHz chunk to send upstream."""
        if self._stop.is_set() or self._connect_error is not None:
            return
        self._send_q.put(json.dumps({
            "type": "audio",
            "data": _pcm_b64(np.asarray(audio, dtype=np.float32)),
            "sample_rate": WS_SAMPLE_RATE,
        }))

    def has_enough_audio(self) -> bool:
        return not self._results.empty()

    def process_sync(self) -> TranscriptionResult | None:
        try:
            tag, payload = self._results.get_nowait()
        except queue.Empty:
            return None
        if tag == "error":
            return None
        return self._apply(payload)

    def flush_sync(self) -> TranscriptionResult:
        self._stop.set()
        if self._ws is not None and self._connect_error is None:
            self._stopped_event.wait(timeout=5.0)
        deltas: list[str] = []
        while True:
            try:
                tag, payload = self._results.get_nowait()
            except queue.Empty:
                break
            if tag != "result":
                continue
            result = self._apply(payload)
            if result.confirmed_delta:
                deltas.append(result.confirmed_delta)
        self.close()
        return TranscriptionResult(
            confirmed=self._confirmed,
            partial="",
            language=self.language,
            confirmed_delta=" ".join(deltas),
        )

    def get_full_transcript(self) -> str:
        return self._confirmed

    # ── mapping ──────────────────────────────────────────────────────

    def _apply(self, step) -> TranscriptionResult:
        confirmed_full, delta, partial = step
        if confirmed_full is not None:
            self._confirmed = confirmed_full
        elif delta:
            self._confirmed = f"{self._confirmed} {delta}".strip() if self._confirmed else delta
        return TranscriptionResult(
            confirmed=self._confirmed,
            partial=partial or "",
            language=self.language,
            confirmed_delta=delta or "",
        )


class RemoteLiveTranscriber(_BaseRemoteLive):
    """Streams to an engine-server ``/ws/live`` (Resona-native JSON protocol)."""

    def __init__(self, url: str, language: str = "de", *, connect=None):
        super().__init__(_to_ws_url(url, language), language=language, connect=connect)

    def _finish_frame(self) -> str:
        return json.dumps({"type": "stop"})

    def _classify(self, data: dict):
        mtype = data.get("type")
        if mtype == "partial":
            return ("result", (data.get("confirmed", ""), data.get("delta", ""), data.get("text", "")))
        if mtype == "final":
            return ("result", (data.get("text", ""), data.get("delta", ""), ""))
        if mtype == "stopped":
            return ("end", None)
        if mtype == "error":
            return ("error", data.get("message"))
        return None

    def _to_result(self, kind: str, data: dict):
        """Back-compat shim: classify a single ``{type: kind, **data}`` message."""
        action = self._classify({"type": kind, **data})
        if action is None or action[0] != "result":
            return None
        return self._apply(action[1])


class GatewayLiveTranscriber(_BaseRemoteLive):
    """Streams to a resona-api ``/v1/listen`` (Deepgram-compatible wire protocol).

    The gateway's ``engine`` query param selects the backend (``deepgram``,
    ``elevenlabs``, a local engine name, …), so the TUI can drive cloud streaming
    without speaking each provider's native protocol.
    """

    def __init__(self, url: str, engine: str, language: str = "de", *,
                 connect=None, api_key: str | None = None):
        self.engine = engine
        key = api_key if api_key is not None else os.getenv("RESONA_API_KEY")
        headers = {"Authorization": f"Token {key}"} if key else None
        gw_url = _build_ws_url(url, "/v1/listen", {
            "engine": engine,
            "language": language,
            "encoding": "linear16",
            "sample_rate": str(WS_SAMPLE_RATE),
            "interim_results": "true",
        })
        super().__init__(gw_url, language=language, connect=connect, headers=headers)

    def _finish_frame(self) -> str:
        return json.dumps({"type": "CloseStream"})

    def _classify(self, data: dict):
        mtype = data.get("type")
        if mtype == "Results":
            alternatives = data.get("channel", {}).get("alternatives") or []
            transcript = alternatives[0].get("transcript", "") if alternatives else ""
            if data.get("is_final"):
                return ("result", (None, transcript, ""))   # final → accumulate delta
            return ("result", (None, "", transcript))         # interim → partial
        if mtype == "Metadata":
            return ("end", None)
        if mtype == "Error":
            return ("error", data.get("description"))
        return None
