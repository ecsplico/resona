# Piper torch-free TTS engine + offline `speech` fallback

**Date:** 2026-06-24
**Status:** Approved (design)
**Scope:** `resona-tts-local`, `resona-cli`

## Problem

Resona's STT side has a local fallback: `resona transcribe` and `resona live`
run an engine in-process (or as a spawned subprocess) when no gateway
(resona-api) is reachable. The TTS side does **not** — `resona speech` only
calls the gateway `/v1/audio/speech` endpoint and fails outright when the API is
not running.

The capability to run TTS locally exists (`resona-tts-local` engines run
in-process; the api already dispatches `local-tts` that way), but two things
block an out-of-the-box offline `resona speech`:

1. `speech.py` has no fallback path.
2. Every existing local TTS engine (kokoro, chatterbox, qwen, qwen-custom-voice)
   requires **torch**. The default `resona-cli` install is deliberately
   torch-free (ctranslate2 faster-whisper), so none of them can ship by default.

Investigation (June 2026) confirmed no existing engine is torch-free, and that
LM Studio cannot serve TTS over its OpenAI-compatible API (no `/v1/audio/speech`
endpoint; requested but unimplemented). The torch-free option that fits an
in-process default is **Piper** (`piper-tts` / `piper1-gpl`): pure
ONNX/onnxruntime, cross-platform, CPU-realtime.

## Goals

- Add a torch-free Piper TTS engine to `resona-tts-local`, following the
  existing engine plugin pattern.
- Wire a gateway→local fallback into `resona speech`, mirroring `transcribe.py`.
- Ship Piper by default with `resona-cli` so offline TTS works out of the box,
  without pulling torch.
- Resolve three known friction points: TTS voice default/validation, the
  IPv6 `localhost` connection failure, and a `just` install recipe gap.

## Non-goals

- No changes to the gateway TTS path (`/v1/audio/speech` already dispatches
  `local-tts`, including Piper once it is installed in the api env).
- No voice cloning, streaming TTS, or mp3/opus transcoding in the local
  fallback (local engines return WAV; parity with the gateway's local-tts path
  which already ignores `response_format`).
- Piper does **not** become the global `recommended_engine()` — kokoro stays the
  recommended engine for the gateway (where torch is acceptable); Piper is the
  torch-free **fallback** default only.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Packaging | Piper ships as a **base dependency** of `resona-cli` (with `resona-tts-local`). |
| Default engine | **Fallback-only**: `recommended_engine()` stays `kokoro`; the CLI fallback picks Piper via a new `recommended_offline_engine()`. |
| Default voice/language | **German**: `de_DE-thorsten-medium`, default language `de`; language→voice map; explicit `--voice <piper-id>` overrides. |

## Design

### 1. New engine — `packages/tts-local/src/resona_tts_local/engines/piper.py`

`PiperEngine` implements the `LocalTTSEngine` protocol. It does **not** import
`_base.py` (which is torch-centric).

- `__init__`: calls `configure_model_cache()`; initializes an empty
  per-voice-id cache `self._voices: dict[str, PiperVoice] = {}`.
- Native lib via `lazy_import("piper", install="pip install piper-tts")`. A
  missing lib raises `EngineUnavailableError` with that hint.
- **Voices directory**: resolved by a `_voices_dir()` helper. Order:
  `PIPER_VOICES_DIR` env (via `config()`), else `<model_cache_dir()>/piper`.
  Created if absent.
- **Voice resolution** `_resolve_voice(voice, language)`:
  - explicit `voice` that looks like a Piper voice id (e.g. `de_DE-thorsten-medium`)
    is used as-is;
  - else `LANG_DEFAULT_VOICE.get(language)`;
  - else `DEFAULT_VOICE` (`de_DE-thorsten-medium`).
  - `DEFAULT_LANGUAGE = "de"`.
  - `LANG_DEFAULT_VOICE` seeds a small set: `de`, `en`, plus a few common
    languages with known Piper voices (`es`, `fr`, `it`, `nl`, `ru`).
- **Model loading** `_ensure_voice(voice_id)`: if not cached, ensure
  `<voices_dir>/<voice_id>.onnx` exists — download via Piper's programmatic
  download API into the voices dir when missing; if download is unavailable
  (offline) raise a clear error including
  `python -m piper.download_voices <voice_id> --download-dir <dir>`. Then
  `PiperVoice.load(<onnx path>)` and cache it.
  - The exact download entry point (`piper.download_voices`) is confirmed during
    implementation via TDD; the engine falls back to the documented CLI command
    text in its error message.
- **Synthesis** `synthesize_array(text, *, language="de", voice=None, speed=1.0,
  seed=None, **_) -> (np.ndarray float32, int)`:
  - `cfg = SynthesisConfig(length_scale=1.0 / speed)` (Piper: larger
    `length_scale` = slower, so `speed` is its inverse);
  - iterate `voice.synthesize(text, syn_config=cfg)`, collect
    `chunk.audio_int16_bytes`, decode to float32 (`int16 / 32768.0`),
    concatenate; `sr = chunk.sample_rate` (typically 22050). Empty result →
    `(np.zeros(sr), sr)`.
- `synthesize(text, **kwargs) -> SpeechResult`: delegates to
  `synthesize_array` then `wav_result(samples, sr)`. `ref_audio`/`ref_text`/
  `instruct` accepted for protocol compatibility and ignored (Piper has preset
  voices, no cloning/instruct).

### 2. Registry — `packages/tts-local/src/resona_tts_local/registry.py`

- Add `"piper": ("resona_tts_local.engines.piper", "PiperEngine")` to `_CLASSES`.
- Add `ENGINE_INFO["piper"]`:
  ```python
  {
      "display_name": "Piper",
      "languages": ["de", "en", "es", "fr", "it", "nl", "ru"],
      "cloning": False,
      "presets": True,
      "instruct": False,
      "torch_free": True,
      "sample_rate": 22050,
      "extra": "piper",
  }
  ```
- `recommended_engine()` is **unchanged** (returns `"kokoro"`).
- New `recommended_offline_engine() -> str` returns `"piper"` — the torch-free
  default used by the CLI fallback. Unit-testable, keeps the policy in one place.

### 3. Packaging

- `packages/tts-local/pyproject.toml`: add optional dependency
  `piper = ["piper-tts>=1.4"]`.
- `apps/resona-cli/pyproject.toml`:
  - add `resona-tts-local` and `piper-tts>=1.4` to base `dependencies`;
  - add `resona-tts-local = { workspace = true }` under `[tool.uv.sources]`.
  - `piper-tts` pulls `onnxruntime` (torch-free); verify it co-resolves with the
    existing torch-free default stack and does not drag torch in.

### 4. `speech.py` — gateway→local fallback

Mirror `transcribe.py`'s structure:

- Add `--language/-l` option, default `"de"`.
- Build kwargs and try `client.create_speech(...)` (gateway path, unchanged).
- On `(httpx.ConnectError, httpx.TimeoutException, RuntimeError)` →
  `_speak_local_fallback(...)`. **Do not** fall back on
  `httpx.HTTPStatusError` (a reachable gateway returning 4xx/5xx is a real
  error, surfaced as today).
- `_speak_local_fallback`:
  - `from resona_tts_local.registry import get_engine, recommended_offline_engine, ENGINES`
  - engine name = `engine` if it is a registered local-tts engine, else
    `recommended_offline_engine()` (`"piper"`).
  - `result = get_engine(name).synthesize(text, voice=voice, language=language, speed=speed)`
  - `result["audio"]` is WAV bytes. Output format is **WAV** offline; if the
    user requested a non-wav `--format`, emit a one-line warning that format is
    ignored in offline mode and write/play `.wav`.
  - Honor `--play` / `--output -` / default output path (`.wav` extension when
    falling back).
  - `EngineUnavailableError` / `UnknownEngineError` → clear typer error.

### 5. Cleanup items

- **Voice default + validation**: change `speech.py` `--voice` default from
  `"alloy"` to `None`. When `None`, the engine picks its own default (Piper →
  German default voice; gateway forwards `None` as today). An invalid Piper
  voice id surfaces as a clear error from `_ensure_voice`.
- **IPv6 localhost fix**: in `packages/client/src/resona_client/client.py`
  change the default base URL from `http://localhost:7000` to
  `http://127.0.0.1:7000` (uvicorn binds IPv4 `0.0.0.0`; httpx resolving
  `localhost` to `::1` causes connection refused).
- **`just` recipe**: add `install-cli-kokoro` (opt-in higher-quality torch TTS
  via `resona-cli[... ]` / `resona-tts-local[kokoro]`) and a comment noting
  Piper ships by default. Document both in the install docs.

## Data flow

```
resona speech "text"
  → ResonaClient.create_speech → gateway POST /v1/audio/speech
      → [reachable] gateway dispatches engine (cloud or local-tts) → audio bytes
      → [ConnectError / Timeout / RuntimeError]
          → _speak_local_fallback
              → resona_tts_local.get_engine("piper").synthesize(text, ...)
              → WAV bytes
  → write to file / play / stdout
```

## Error handling

| Condition | Behavior |
|-----------|----------|
| Gateway unreachable | Fall back to local Piper engine |
| Gateway reachable, returns 4xx/5xx | Surface `Error <code>: <body>`, no fallback |
| `piper-tts` not importable | `EngineUnavailableError("... pip install piper-tts")` |
| Voice `.onnx` missing + offline | Clear error incl. `python -m piper.download_voices ...` |
| Unknown engine name in fallback | `UnknownEngineError` → typer error listing known engines |
| Non-wav `--format` offline | Warn; write `.wav` |

## Testing (TDD, native lib mocked)

`packages/tts-local/tests/test_piper.py`:
- `synthesize()` returns `SpeechResult` with `audio` WAV bytes, `content_type
  "audio/wav"`, `sample_rate` from the chunk.
- language→voice mapping (`de`→thorsten, `en`→lessac, unknown→German default).
- `speed` maps to `length_scale = 1/speed`.
- explicit `voice` overrides language.
- missing lib → `EngineUnavailableError` with install hint.

`packages/tts-local/tests/test_registry.py` (extend):
- `"piper"` in `ENGINES`, `ENGINE_INFO`, `_CLASSES`.
- `recommended_offline_engine() == "piper"`; `recommended_engine() == "kokoro"`.

`apps/resona-cli/tests/test_speech.py`:
- gateway `ConnectError` → local engine `synthesize` called, file written
  (mock `get_engine`).
- gateway `HTTPStatusError` (reachable) → no fallback, exit 1.
- `--play` path invokes player with audio bytes.

`packages/client/tests/` (extend):
- default base URL is `http://127.0.0.1:7000` when no env vars set.

## Rollout / verification

- `uv sync --all-packages` resolves with `piper-tts` + `resona-tts-local` in the
  CLI base deps, no torch pulled into the default set.
- `uv run pytest` green across tts-local, resona-cli, client.
- Manual: with no resona-api running, `resona speech "Guten Tag" --play` (or
  `--output out.wav`) synthesizes via Piper offline.
```
