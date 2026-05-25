# Quick Start

Get from zero to a transcript in three steps. No server required.

## Step 1: Install

```bash
uv tool install --from ./apps/resona-cli resona-cli
```

Or, if you have the workspace cloned:

```bash
uv sync --all-packages
```

See [Installation](installation.md) for prerequisites and persona details.

## Step 2: Transcribe a file

```bash
resona transcribe recording.mp3
```

No server needs to be running. Resona spawns the faster-whisper engine in-process, transcribes the audio, applies default postprocessing (German dictation replacements), and exits.

You can also pass a directory, multiple files, or a glob:

```bash
resona transcribe ./recordings/          # all audio files in a directory
resona transcribe "recordings/*.mp3"     # quoted glob
resona transcribe a.mp3 b.mp3 c.mp3     # multiple files
```

!!! tip "GPU not required for cloud engines"
    If you don't have a local GPU, use a cloud engine instead:

    ```bash
    export OPENAI_API_KEY=sk-...
    resona transcribe recording.mp3 --engine openai
    ```

## Step 3: Find the output

The transcript is written as a `.txt` file next to your input:

```
recordings/
├── dictation-2026-05-17.mp3
└── dictation-2026-05-17.txt   ← output
```

To write output elsewhere:

```bash
resona transcribe ./recordings/ --output-dir ./transcripts/
```

## What it looks like

```
$ resona transcribe dictation.mp3
Transcribing dictation.mp3 ...
  engine : faster-whisper (in-process)
  lang   : de (detected)
  took   : 4.2 s

Output : dictation.txt
```

The transcript has postprocessing applied — spoken punctuation commands like "Komma" and "Punkt" are replaced with `,` and `.` automatically.

!!! note "First run downloads the model"
    On first use, faster-whisper downloads `large-v3` (~3 GB). Subsequent runs use the cached model. Set `DEFAULT_FASTWHISPER_MODEL=small` to use a smaller model.

## Next steps

- [Local-Only Mode](local-only.md) — engine resolution, output control, postprocessing config
- [CLI Reference](../guide/cli.md) — all `resona` commands and flags
- [Engine Selection](../guide/engines.md) — local vs cloud engines, `--engine` flag
- [Text Replacements](../guide/replacements.md) — customise postprocessing rules
