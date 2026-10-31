# resona-cli

Command-line interface for [Resona](https://github.com/ecsplico/resona) — a modular transcription platform with pluggable ASR engines. Ships the `resona` command with `transcribe`, `doc`, `watch`, `jobs`, `settings`, `rec`, `live`, `ui`, `speech`, `engines`, and `profiles` subcommands. `transcribe`, `doc` and `watch` run private jobs on a Resona hub; `--local` processes explicitly on this machine.

```bash
uv tool install --from resona-cli resona-cli
resona transcribe recording.mp3
```

See the [main repository](https://github.com/ecsplico/resona) for full documentation, server setup, and the rest of the Resona stack.
