# Local-Only Mode

Local mode lets you transcribe audio or convert documents on this machine without a hub. It is
**explicit only**: pass `--local` to `resona transcribe`, `resona doc` or `resona watch`. It is
never chosen automatically.

## What works in local mode

| Operation | Local | Requires hub |
|-----------|-------|--------------|
| `resona transcribe --local` | Yes | — |
| `resona doc --local` | Yes | — |
| `resona watch --local` | Yes | — |
| `resona rec` | Yes | — |
| `resona live` | Yes | — |
| `resona transcribe` / `doc` / `watch` without `--local` | No | resona-api (:7000) + `resona-worker` |
| `resona jobs`, `resona settings` | No | resona-api (:7000) |
| Profiles CRUD | No | resona-api (:7000) |

## No automatic fallback

Without `--local` the CLI always uses the hub. An unreachable hub, a timeout or a failed job is
reported as an error (exit 1), and a policy denial exits with code 2. Nothing is ever retried
locally or in the cloud.

## Settings and policy

`--local` runs the same policy path as hub admission:

1. Settings come from the cache written by `resona settings pull` for the configured hub and
   credential (`~/.resona/settings-cache/<hash>.json`). Local mode never contacts the hub, so a
   used cache is marked `stale: true`. Without a cache, built-in private defaults apply.
2. The execution profile (`--execution-profile` or the organization default) supplies the approved
   engine candidates; `--engine`/`--model` must match one of them.
3. `check_policy` runs on every stage before processing. Cloud engines (`deepgram`, `openai`,
   `elevenlabs`) count as cloud egress and are denied unless the settings allow cloud and you pass
   `--allow-cloud`.

```bash
resona settings pull                                   # cache the hub's settings
resona transcribe recording.mp3 --local                # default execution profile
resona transcribe recording.mp3 --local --execution-profile cpu-private
resona doc scan.pdf --local --ocr force
```

## Execution

The candidate runs in the worker's execution subprocess (`resona-worker` is a dependency of
`resona-cli`) with the same private environment as on a worker: offline model caches and no
credentials. Prepare model weights beforehand with `resona-worker prepare`; a missing model is
reported as `unsupported_capability`.

## Output files

Local runs write the same outputs as hub jobs, next to the input by default:

```
recordings/
├── note-2026-05-17.mp3
├── note-2026-05-17.md
└── note-2026-05-17.result.json
```

Existing outputs are not overwritten without `--force`. Override the location with `--output-dir`:

```bash
resona transcribe ./recordings/ --local --output-dir ./transcripts/
```

## Postprocessing in local mode

`--profile` takes a profile name (looked up in `~/.resona/profiles/<name>.json`), a path to a
JSON file, or inline JSON — file and inline profiles are only accepted with `--local`. Without
`--profile`, the cached organization settings decide whether the organization's postprocess
profile applies. LLM steps pass the same policy check; cloud models are denied in private runs.

See [Postprocessing](../guide/postprocessing.md) for full pipeline documentation.
