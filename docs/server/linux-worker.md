# Linux Worker (NVIDIA, Docker)

A `resona-worker` container on a Linux host with an NVIDIA GPU serves the `linux-cuda` execution profile:

| Kind | Engine | Model | Accelerator |
|------|--------|-------|-------------|
| audio | `whisper` (openai-whisper, PyTorch) | `large-v3` | CUDA |
| document | `docling` + `rapidocr` | `docling-standard` | CUDA (layout and table structure), CPU (RapidOCR, onnxruntime) |

The worker is outbound-only. It long-polls the hub and opens no port. It gets no Docker socket. The hub and the approved worker can read job content. External AI providers cannot: private jobs run with offline model caches, with telemetry disabled and without cloud credentials (see [Mac Worker](mac-worker.md#private-execution-environment); the same execution subprocess runs in the container).

The host is allowed to be offline. Jobs that the `private-auto` profile can also run on the Mac stay queued and are claimed by the Mac worker.

## Requirements

- NVIDIA driver and the NVIDIA Container Toolkit (`docker info` lists the `nvidia` runtime).
- The image ships PyTorch built for CUDA 13.0 (`torch 2.12.0+cu130`, arch list up to `sm_120`). Blackwell GPUs (RTX 50xx, compute capability 12.0) are supported. The driver must support CUDA 13.0.
- About 16 GB of disk for the image and 5 GB for the model cache. Whisper `large-v3` uses about 9 GB of VRAM.
- The image installs `tesseract-ocr` (with the `osd` traineddata): document OCR turns every page upright before conversion (see [CLI: page orientation](../guide/cli.md#page-orientation)).

Check the GPU from inside the image before you prepare:

```bash
docker run --rm --gpus all --entrypoint python <image> -c \
  "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0), torch.cuda.get_arch_list())"
```

## 1. Build the image

```bash
docker build -f apps/resona-worker/Dockerfile \
  --build-arg BASE_IMAGE=nvidia/cuda:13.0.1-runtime-ubuntu24.04 \
  --build-arg "WORKER_EXTRAS=--extra cuda --extra documents --extra diarize" \
  --build-arg RESONA_RELEASE_ID=<release> \
  -t resona-worker:<release>-cuda .
docker image inspect --format '{{.Id}}' resona-worker:<release>-cuda    # sha256:… digest
```

Without `--extra cuda` (the default `WORKER_EXTRAS="--extra audio-cpu --extra documents"`) the same Dockerfile builds the CPU image: it installs the torch/torchvision versions from `uv.lock` as CPU wheels from `https://download.pytorch.org/whl/cpu` (`TORCH_CPU_INDEX` build arg) and no `nvidia-*`, `cuda-*` or `triton` packages. `uv.lock` itself pins the cu130 wheels on Linux; `apps/resona-worker/docker/sync.sh` swaps them only in the CPU image.

Use the image by digest only (`resona-worker@sha256:…` locally, or the released `ghcr.io/…@sha256:…`). Never use `:latest` or `:cuda`.

The released CUDA image (`ghcr.io/ecsplico/resona-worker-cuda`) includes speaker diarization (`--extra diarize`): `prepare` downloads the pinned `nvidia/Nemotron-3-Diarization` weights with the others and runs a two-speaker check on the GPU; only a worker whose manifest records the diarizer takes diarized jobs. Leave `--extra diarize` out of `WORKER_EXTRAS` to build an image without it.

## 2. Enroll on the hub

On the hub host:

```bash
resona-admin worker enroll gpu-pc --organization acme   # prints worker_id and token once
```

On the Linux host, store the token in a file that only UID 1000 can read. The container user `resona` has UID 1000 and refuses token files with a wider mode:

```bash
mkdir -p ~/resona-worker && cd ~/resona-worker
(umask 077; printf '%s\n' '<token>' > worker.token)
```

## 3. Configure `docker-compose.worker.yml`

Copy `docker-compose.worker.yml` from the repository root and create `.env` next to it:

```bash
RESONA_WORKER_IMAGE=resona-worker@sha256:<digest>
RESONA_HUB_URL=https://resona.ecsplico.de
RESONA_WORKER_TOKEN_FILE=/home/<user>/resona-worker/worker.token
```

The service:

| Item | Setting |
|------|---------|
| GPU | `deploy.resources.reservations.devices`: driver `nvidia`, `count: 1`, `capabilities: [gpu]` |
| Model cache | named volume `models` at `/models` (`HF_HOME` and the openai-whisper checkpoints under `/models/cache`) |
| Capabilities manifest | named volume `state` at `/state` |
| Job directory | `tmpfs` at `/work` (4 GB, mode 0700): empty after every start, never on disk |
| Token | bind mount of the 0600 file at `/run/secrets/resona-worker-token`, read-only |
| Isolation | no ports, no Docker socket, `cap_drop: [ALL]`, `no-new-privileges`, user UID 1000 |
| Restart | `unless-stopped` |

## 4. Prepare

```bash
docker compose -f docker-compose.worker.yml run --rm worker \
  prepare --profile linux-cuda --out /state/capabilities.json
```

`prepare` takes the model pins of the image's release (`resona_worker/models.json` in the `resona-worker` wheel; `--release-manifest` selects another manifest) and downloads each missing pinned revision into the `models` volume: the openai-whisper `large-v3` checkpoint (its revision is the published SHA-256, verified after download) and the Docling layout (`docling-project/docling-layout-heron`) and table-structure (`docling-project/docling-models`) snapshots at their pinned commits. A model without a pin fails `prepare`; an unpinned `main` snapshot is never used. RapidOCR's ONNX models are part of the `rapidocr` wheel. Add `--offline` to forbid downloads: a missing pin then fails with its `repo@revision`. Jobs never download.

Then `prepare` runs a real synthetic job for each candidate in the private, offline execution subprocess, checks the recognized German words, checks that the primary stage ran on CUDA and that every HuggingFace model the run recorded is the pinned revision. It records seconds, peak RSS, peak CUDA memory (`torch.cuda.max_memory_allocated`) and the device of every stage. If a step fails, `prepare` exits non-zero and does not change the existing manifest.

Output on an RTX 5070 Ti (16 GB, driver 615.78.08):

```
ok audio: whisper large-v3 [cuda] 20.8s, peak RSS 6823 MiB, peak cuda 8962 MiB; asr:whisper=cuda; large-v3@e5b1a55b89c1
ok document: docling docling-standard [cuda] 18.9s, peak RSS 3197 MiB, peak cuda 575 MiB; layout:docling-layout=cuda, layout:docling-tableformer=cuda, ocr:rapidocr=cpu; docling-project/docling-layout-heron@8f39ad3c0b4c, docling-project/docling-models@fc0f2d45e221
prepared 2 capability tuple(s) for linux-cuda -> /state/capabilities.json
```

The first run, which downloaded the weights, took about 6 minutes; later runs with the weights cached take about 45 seconds.

## 5. Approve and run

Print the tested tuples and give them to the hub admin:

```bash
docker compose -f docker-compose.worker.yml run --rm -T worker capabilities /state/capabilities.json > caps-approve.json
resona-admin worker approve <worker-id> --private --capabilities-file caps-approve.json   # on the hub
```

Start the worker:

```bash
docker compose -f docker-compose.worker.yml up -d
docker compose -f docker-compose.worker.yml logs -f     # never contains content, filenames or tokens
docker compose -f docker-compose.worker.yml down        # stop; keeps the models and state volumes
```

Each job runs in its own subprocess. The model is loaded for that job and is released when the subprocess exits, so the GPU is free between jobs.

## LLM polish on the worker host

Document jobs can get an OCR-correction pass from a local model on the worker host. The bundled `document-polish-de` profile (in the `resona-postprocess` wheel, `resona_postprocess/profiles/document-polish-de.json`) sends the Docling Markdown in chunks of about 12000 characters, split on block boundaries and never inside a table, to the endpoint `local-llm`. The profile names no host: an org admin registers a `local_llm` endpoint with the id `local-llm`, a loopback `url` and the approved worker's `worker_id` (see [Enable it](#enable-it)). Its prompt allows only OCR fixes (hyphenation, umlauts/ß, stray characters, merged/split words) and requires headings, tables, page markers and the `<!-- image -->` / `<!-- formula-not-decoded -->` placeholders to stay unchanged. A chunk reply that differs from the chunk by more than 30 % in length, or that changes placeholders, heading levels or table shape, is discarded: the raw chunk is kept and the result gets the warning `polish_chunk_rejected:<n>:<length|markup>`. A chunk that fails 3 calls fails the job (`postprocess_failed`, retryable); the polish is never skipped silently. `markdown` is the polished text. The converter's Markdown before polish, tables included, stays in the result's `raw_markdown` (set whenever a postprocess step changed the Markdown); `raw_text` is the converter's plain text, which has no tables. See [Postprocessing Pipeline](../architecture/postprocessing.md#chunked-mode-long-markdown).

### Privacy

The endpoint is a `local_llm` endpoint. The execution policy admits it for private jobs only with a loopback `url`, and the hub hands the job only to the worker the endpoint is assigned to (`worker_id`). The text therefore goes from the worker process to a model server on the same host and nowhere else. A job whose profile uses a `local_llm` endpoint with a non-loopback `url` is refused at submission (`policy_denied`), and the policy is checked again before every chunk. A non-loopback `unload_url` is already refused when the settings are saved. Before each LLM stage the worker asks the hub for a fresh policy decision; when the hub is unreachable the stage does not start. Calls to `local_llm` endpoints carry a fixed placeholder API key, never an `OPENAI_API_KEY` from the environment, and litellm uses its bundled model price map instead of fetching it.

### Reaching a model server on the host's loopback

On `gpu-box`, llama-swap listens on the host's `127.0.0.1:9292` (OpenAI-compatible, `/v1`). A container on the default bridge network cannot reach the host's loopback, and a bridge address such as `172.17.0.1` is not loopback, so the policy would deny it. `docker-compose.worker.llm.yml` overrides the worker service with `network_mode: host`. In the host namespace `127.0.0.1` is the host's loopback, so the endpoint URL `http://127.0.0.1:9292/v1` reaches llama-swap and passes the loopback check.

The worker still opens no port (it has no listener) and gets no Docker socket. `cap_drop: [ALL]`, `no-new-privileges`, UID 1000, the read-only token, the `tmpfs` job directory and the GPU reservation stay as in the base file. Host networking does give the container the host's network namespace, including every service bound to the host's loopback.

Use both files for every command that starts the worker:

```bash
docker compose -f docker-compose.worker.yml -f docker-compose.worker.llm.yml up -d
docker compose -f docker-compose.worker.yml -f docker-compose.worker.llm.yml logs -f
```

### GPU sharing with llama-swap

llama-swap keeps a loaded model resident until its `ttl` expires (1800 s for `qwen3.6-27b` on gpu-box), using most of the 16 GB of VRAM. A whisper `large-v3` job (about 9 GB) started in that window would run out of memory. An endpoint can therefore name an `unload_url`: after the job's last LLM stage on that endpoint, and also when the job fails after calling it, the worker sends `POST <unload_url>` (no body, 10-second timeout). The request is best effort and never fails the job. A 2xx answer is logged at info level; any other status (for example `404`/`405` from a server without that route) is logged as a warning with the endpoint id and the status code, and a transport error as a warning with the endpoint id and the exception type. A warning means the model may still hold the GPU. `unload_url` must be a loopback `http(s)` URL and is allowed only on `local_llm` endpoints; anything else is rejected when the settings are validated.

llama-swap's API (from its `docs/kb/guides/model-runtime/ttl-and-unloading.md`): `POST /api/models/unload/<model_id>` unloads one model, `POST /api/models/unload` unloads all models. Older releases offer `GET /unload` instead, which the worker's `POST` does not reach (the worker logs the resulting `404`/`405` as a warning). The llama-swap installed on gpu-box answered `200` to `POST /api/models/unload/<model_id>`, `POST /api/models/unload` and `GET /unload`. Check with `curl -X POST http://127.0.0.1:9292/api/models/unload/qwen3.6-27b` on the host.

### Enable it

1. Deploy a release that contains this feature to the hub **and** to every worker first. An older hub rejects `unload_url` in the settings (`422`); an older worker fails postprocess jobs whose snapshot contains it (`invalid_snapshot`).
2. Start the worker with the override (above).
3. As org admin, add the endpoint to the organization settings. The bundled profile calls the endpoint id `local-llm`, so register it under that id (or change the profile's `endpoint` before uploading it). `url` and `unload_url` point to the model server on the worker host's loopback; `worker_id` is the id that `resona-admin worker enroll` printed for the Linux worker:

    ```json
    {
      "id": "local-llm",
      "kind": "local_llm",
      "model": "openai/qwen3.6-27b",
      "url": "http://127.0.0.1:9292/v1",
      "worker_id": "<linux worker id>",
      "unload_url": "http://127.0.0.1:9292/api/models/unload/qwen3.6-27b"
    }
    ```

    The `openai/` prefix makes litellm speak the OpenAI chat API to `url`; llama-swap receives the model name `qwen3.6-27b`.

    ```bash
    resona settings get | jq '.settings | .endpoints = ([.endpoints[] | select(.id != "local-llm")] + [input])' \
      - local-llm.json > settings.json
    resona settings set --file settings.json
    ```

4. Upload the bundled profile as an organization profile (org admin):

    ```bash
    python -c "from importlib import resources; print(resources.files('resona_postprocess').joinpath('profiles/document-polish-de.json').read_text())" > document-polish-de.json
    resona profiles push document-polish-de document-polish-de.json
    ```

    (Or copy the file from `packages/postprocess/src/resona_postprocess/profiles/` in the repository.)

5. Request it per document job:

    ```bash
    resona doc scan.pdf --execution-profile linux-cuda --profile document-polish-de --output-dir out/
    ```

    Leave the organization settings `polish: false`: with `polish: true` and `postprocess_profile: "document-polish-de"` every job, audio included, would get the document prompt. A job that names the profile is claimed only by the worker that the endpoint is assigned to.

6. Optionally add instructions for a single job. The profile's `prompt` stays the system message, and the instructions go in the user message ahead of each chunk ([User instructions](../reference/api.md#user-instructions)):

    ```bash
    resona doc scan.pdf --profile document-polish-de \
      --instructions 'Eigennamen wie "Dr. Hämmerle" nicht ändern. Abkürzungen nicht ausschreiben.'
    resona postprocess --job 42 --profile transcript-polish-de --instructions-file instructions.txt
    ```

    Instructions don't relax the guards: a reply that changes a chunk's length beyond `max_length_deviation` or its markup is still rejected. Only steps that set these guards are checked (the bundled polish profiles do). The worker must run a release with instruction support; an older worker runs the profile without the instructions.

Measured on 2026-11-02 (evaluation corpus, gpu-box, `linux-cuda`, `qwen3.6-27b` via llama-swap; CER = character error rate):

| Fixture | Plain | `document-polish-de` |
|---|---|---|
| degraded scan | 0.002 | 0.000 |
| small print scan | 0.196 | 0.191 |
| invisible text layer re-OCRed in `auto` (`bad-text-layer`) | 0.004 | 0.000 |
| dense table scan | 3 cells missing\* | 0 cells missing |
| French scan, `auto` (`fr-scan-pdf`, 2026-11-04, endpoint `local-llm`) | 0.200 | 0.200 |
| all other fixtures | 0.000 | 0.000 |

\* Before the `°℃` normalization (608e6e7); the plain run now also misses no cell.

- Polish fixes single misread characters. It cannot restore text that OCR dropped, as on the small print scan; `--ocr force` fixes that case (see [CLI `doc`](../guide/cli.md#doc)).
- It adds 36–77 s per one-page document, including reloading the model, because the worker unloads it after each job.
- No chunk was rejected by the length or markup guards.
- The polished `markdown` is still a model rewrite: keep `raw_markdown` (the converter's Markdown before polish) as the reference.

## When CUDA is not available

At start, the worker asks the execution environment whether each accelerator of its manifest works (`python -m resona_worker.execute --probe-accelerator cuda`). If CUDA does not work (no GPU reservation, driver failure, `CUDA_VISIBLE_DEVICES=""`), the worker logs `cuda is not available on this host; not offering 2 tuple(s)` and heartbeats with no CUDA tuples. It never claims a CUDA job that it cannot run. The hub reports such jobs as `queue_reason: no_capable_worker` (approved tuples that the worker does not offer do not count). `private-auto` jobs stay eligible for the Mac worker, which claims them when it is online. A `prepare` without a GPU fails with `unsupported_capability` and leaves the previous manifest unchanged.

## Result provenance

Every stage records its real device and, on CUDA/Metal stages, the peak device memory of the job's process (`peak_accelerator_bytes`). The Docling layout and table-structure stages run in one engine call and share the value:

```
asr     whisper              large-v3@e5b1a55b…                              cuda  8962 MiB
layout  docling-layout       docling-project/docling-layout-heron@8f39ad3c…  cuda   575 MiB
layout  docling-tableformer  docling-project/docling-models@fc0f2d45…        cuda   575 MiB
ocr     rapidocr             rapidocr-ppocr@3.9.2                            cpu
```

## Silence

openai-whisper has no voice-activity filter of its own and turns noise into text such as "Vielen Dank.". The engine applies faster-whisper's bundled Silero VAD when the job's `vad_filter` option is set (the default): only detected speech is decoded, and audio without speech gives an empty transcript.

## Baseline

The `linux-cuda/linux-cuda` baseline in `eval/baselines.json` was promoted from a remote evaluation on this worker (`resona eval --profile linux-cuda --platform linux-cuda --remote`, then `resona eval promote <id>`). See [Evaluation & Baselines](../guide/evaluation.md).
