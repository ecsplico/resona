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

Check the GPU from inside the image before you prepare:

```bash
docker run --rm --gpus all --entrypoint python <image> -c \
  "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0), torch.cuda.get_arch_list())"
```

## 1. Build the image

```bash
docker build -f apps/resona-worker/Dockerfile \
  --build-arg BASE_IMAGE=nvidia/cuda:13.0.1-runtime-ubuntu24.04 \
  --build-arg "WORKER_EXTRAS=--extra cuda --extra documents" \
  --build-arg RESONA_RELEASE_ID=<release> \
  -t resona-worker:<release>-cuda .
docker image inspect --format '{{.Id}}' resona-worker:<release>-cuda    # sha256:… digest
```

Use the image by digest only (`resona-worker@sha256:…` locally, or the released `ghcr.io/…@sha256:…`). Never use `:latest` or `:cuda`.

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
  prepare --profile linux-cuda --download --out /state/capabilities.json
```

`--download` fetches the pinned weights into the `models` volume: the openai-whisper `large-v3` checkpoint (its revision is the published SHA-256, verified after download) and the Docling layout (`docling-project/docling-layout-heron`) and table-structure (`docling-project/docling-models` `v2.3.0`) snapshots. RapidOCR's ONNX models are part of the `rapidocr` wheel. Jobs never download.

Then `prepare` runs a real synthetic job for each candidate in the private, offline execution subprocess, checks the recognized German words and checks that the primary stage ran on CUDA. It records seconds, peak RSS, peak CUDA memory (`torch.cuda.max_memory_allocated`) and the device of every stage. If a step fails, `prepare` exits non-zero and does not change the existing manifest.

Output on an RTX 5070 Ti (16 GB, driver 615.78.08):

```
ok audio: whisper large-v3 [cuda] 20.8s, peak RSS 6823 MiB, peak cuda 8962 MiB; asr:whisper=cuda; large-v3@e5b1a55b89c1
ok document: docling docling-standard [cuda] 18.9s, peak RSS 3197 MiB, peak cuda 575 MiB; layout:docling-layout=cuda, layout:docling-tableformer=cuda, ocr:rapidocr=cpu; docling-project/docling-layout-heron@8f39ad3c0b4c, docling-project/docling-models@fc0f2d45e221
prepared 2 capability tuple(s) for linux-cuda -> /state/capabilities.json
```

The first `--download` run took about 6 minutes; later runs without `--download` take about 45 seconds.

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
