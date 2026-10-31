# Mac Worker (Apple Silicon)

A native `resona-worker` on an Apple Silicon Mac serves the `mac-private` execution profile:

| Kind | Engine | Model | Accelerator |
|------|--------|-------|-------------|
| audio | `mlx-whisper` | `mlx-community/whisper-large-v3-mlx` | Metal (MLX) |
| document | `docling` + `ocrmac` (Apple Vision) | `docling-standard` | Metal (layout), Apple Vision (OCR); Docling's table model runs on CPU |

Run it natively. Docker on macOS has no access to Metal, MLX or Apple Vision.

The worker is outbound-only. It long-polls the hub over HTTPS and opens no listening port. The hub and the approved worker can read job content. External AI providers cannot: private jobs run with offline model caches, with telemetry disabled and without cloud credentials.

## 1. Install

From a checkout of the repository:

```bash
uv sync --all-packages --extra apple --extra documents
```

The worker uses the workspace `.venv` (`.venv/bin/resona-worker`). It does not download weights while it serves jobs. Put the weights into the HuggingFace cache once, while you are online:

```bash
uv run hf download mlx-community/whisper-large-v3-mlx
uv run hf download docling-project/docling-layout-heron
uv run hf download docling-project/docling-models --revision v2.3.0
```

## 2. Enroll on the hub

On the hub host:

```bash
resona-admin worker enroll mac-studio --organization acme   # prints worker_id and token once
```

On the Mac, store the token in a file that only your user can read. `run` and `install` refuse token files with wider modes or symlinks:

```bash
(umask 077; printf '%s\n' '<token>' > ~/.resona/worker.token)
```

Do not put the worker token into `~/.resona/config.json`, into environment variables or into command arguments.

## 3. Prepare

```bash
.venv/bin/resona-worker prepare --profile mac-private --out ~/.resona/worker-caps.json
```

For each candidate of the profile, `prepare`:

1. verifies that the weights are in the local HuggingFace cache. If the profile pins `model_revision`, that snapshot must exist. The snapshot commit hash is recorded as the model revision.
2. runs a real synthetic job through the same execution subprocess and private environment as production jobs: a short German WAV for audio and a scanned German PDF for documents. It checks the recognized German words.
3. checks that the primary stage ran on the requested accelerator (`asr` on Metal, Docling `layout` on Metal).
4. records seconds, peak RSS, peak Metal memory and the device of every stage.

Use `--settings-file settings.json` to prepare the organization's current profiles (the body of `GET /v1/settings`) instead of the seeded defaults. If a step fails, `prepare` exits non-zero and does not change the existing manifest.

Example output on an M5 Pro (64 GB):

```
ok audio: mlx-whisper mlx-community/whisper-large-v3-mlx [metal] 2.5s, peak RSS 3473 MiB, peak metal 3838 MiB; asr:mlx-whisper=metal; mlx-community/whisper-large-v3-mlx@49e6aa286ad6
ok document: docling docling-standard [metal] 7.4s, peak RSS 1129 MiB; layout:docling-layout=metal, layout:docling-tableformer=cpu, ocr:ocrmac=metal; docling-project/docling-layout-heron@8f39ad3c0b4c, docling-project/docling-models@fc0f2d45e221
```

## 4. Approve

Print the tested tuples and give them to the hub admin:

```bash
.venv/bin/resona-worker capabilities ~/.resona/worker-caps.json > caps-approve.json
resona-admin worker approve <worker-id> --private --capabilities-file caps-approve.json   # on the hub
```

The worker offers a tuple only if it is in **both** sets: its own tested manifest and the hub-approved list that it receives in every heartbeat reply. The hub also requires an exact tuple match on every claim. A heartbeat cannot widen the approval.

## 5. Run as a launchd service

```bash
.venv/bin/resona-worker install --profile mac-private \
  --hub https://resona.ecsplico.de \
  --token-file ~/.resona/worker.token \
  --capabilities-file ~/.resona/worker-caps.json
```

This command:

- writes `~/Library/LaunchAgents/de.ecsplico.resona-worker.plist` (`--label` changes the name) and loads it with `launchctl bootstrap gui/$UID`. The service starts at login and restarts after a crash.
- puts only the token *file path* into the plist. The plist environment contains only `PATH`, `PYTHONUNBUFFERED` and the HuggingFace cache location; no credentials.
- refuses a token file with a mode wider than 0600, a hub URL that is not `https://` (plain `http://` is allowed only for loopback), and a manifest that was prepared for a different profile.
- uses `~/Library/Caches/resona-worker/<label>` as the per-job scratch directory and writes the log to `~/Library/Logs/resona-worker/<label>.log`. The log never contains content, filenames or tokens.

Check and remove:

```bash
launchctl print gui/$(id -u)/de.ecsplico.resona-worker | grep -E 'state|pid'
.venv/bin/resona-worker uninstall            # boots out the agent and deletes the plist
```

To run the worker in the foreground instead of as a service:

```bash
.venv/bin/resona-worker run --hub https://resona.ecsplico.de --token-file ~/.resona/worker.token \
  --capabilities-file ~/.resona/worker-caps.json --work-dir /tmp/resona-worker
```

## Private execution environment

Each job runs in its own subprocess. For private jobs, the subprocess environment is an allowlist: system and locale variables, model-cache paths and engine tuning variables. Names that look like credentials (`*_API_KEY`, `*_TOKEN`, `*_SECRET`, …) are always removed, and so are `OPENAI*`, `DEEPGRAM*`, `ELEVENLABS*`, `AWS_*`, `AZURE_*`, `GOOGLE_*`, `ANTHROPIC*` and `RESONA_API_KEY`. The worker always sets:

```
HF_HUB_OFFLINE=1  TRANSFORMERS_OFFLINE=1  HF_DATASETS_OFFLINE=1
HF_HUB_DISABLE_TELEMETRY=1  HF_HUB_DISABLE_IMPLICIT_TOKEN=1  DO_NOT_TRACK=1
```

Docling runs in-process. Its remote services stay at the default (disabled), and it loads its layout and table models from the same offline HuggingFace cache.

## Out of memory

The worker treats a job as out of memory if the engine subprocess is killed with SIGKILL (for example by jetsam), raises `MemoryError`, or prints an out-of-memory marker such as MLX's `[metal::malloc]`. The worker then:

- fails the attempt as retryable `out_of_memory`. The hub requeues the job until its attempt limit is reached, and another worker can claim it.
- stops offering that tuple until the worker restarts. The hub also removes the tuple from the worker's approved capabilities. To use the tuple again, run `prepare` again and have the admin approve it again.

## Result provenance

Every result records the real device of each stage, for example:

```
asr     mlx-whisper          mlx-community/whisper-large-v3-mlx@49e6aa28…  metal
layout  docling-layout       docling-project/docling-layout-heron@8f39ad3c…  metal
layout  docling-tableformer  docling-project/docling-models@fc0f2d45…        cpu
ocr     ocrmac               apple-vision                                    metal
```
