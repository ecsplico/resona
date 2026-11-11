"""``resona transcribe``: audio files -> hub audio jobs (or ``--local``) -> ``<stem>.md``."""
import glob as _glob
from pathlib import Path
from typing import Optional

import typer
from resona_client.client import AudioOptions

from .hub import INSTRUCTIONS_FILE_HELP, INSTRUCTIONS_HELP, check_privacy_flags, instructions_arg
from .media import MediaRequest, run_media

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def expand_inputs(inputs: list[str], recursive: bool, extensions: set[str]) -> list[Path]:
    """Expand file paths, glob patterns and directories into files with ``extensions``.

    Explicitly named files are taken as given; globs and directories are filtered.
    """
    out: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        rp = p.resolve()
        if rp in seen:
            return
        seen.add(rp)
        out.append(p)

    for raw in inputs:
        if any(ch in raw for ch in "*?["):
            for m in sorted(Path(m) for m in _glob.glob(raw, recursive=recursive)):
                if m.is_file() and m.suffix.lstrip(".").lower() in extensions:
                    _add(m)
            continue

        p = Path(raw)
        if p.is_dir():
            glob_fn = p.rglob if recursive else p.glob
            for ext in sorted(extensions):
                for f in sorted(glob_fn(f"*.{ext}")):
                    _add(f)
        elif p.is_file():
            _add(p)
        else:
            typer.echo(f"Not found: {raw}", err=True)

    return out


def transcribe_files(
    inputs: list[str] = typer.Argument(
        ..., help="Audio files, glob patterns, or directories.", metavar="INPUTS..."),
    recursive: bool = typer.Option(False, "--recursive", "-r",
        help="Recurse into directories / use `**` in glob patterns."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir",
        help="Directory for <stem>.md and <stem>.result.json (default: next to each input)."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing outputs."),
    as_json: bool = typer.Option(False, "--json", help="Print a JSON report on stdout."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit and print job IDs; do not wait."),
    timeout: Optional[float] = typer.Option(None, "--timeout",
        help="Seconds to wait per job; the job keeps running on the hub afterwards."),
    private: bool = typer.Option(False, "--private",
        help="Require private processing (the default)."),
    allow_cloud: bool = typer.Option(False, "--allow-cloud",
        help="Allow cloud stages; still requires the organization to enable cloud processing."),
    execution_profile: Optional[str] = typer.Option(None, "--execution-profile",
        help="Execution profile (approved engine candidates), e.g. mac-private, cpu-private."),
    language: Optional[str] = typer.Option(None, "--language",
        help="Language hint (default: the organization's setting)."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine override; must be an approved candidate of the execution profile."),
    model: Optional[str] = typer.Option(None, "--model",
        help="Model override; must be an approved candidate of the execution profile."),
    profile: Optional[str] = typer.Option(None, "--profile",
        help="Postprocess profile name (a JSON file or inline JSON only with --local)."),
    instructions: Optional[str] = typer.Option(None, "--instructions", help=INSTRUCTIONS_HELP),
    instructions_file: Optional[Path] = typer.Option(None, "--instructions-file", help=INSTRUCTIONS_FILE_HELP),
    translate: bool = typer.Option(False, "--translate", help="Translate to English."),
    word_timestamps: bool = typer.Option(False, "--word-timestamps", help="Request word timestamps."),
    diarize: bool = typer.Option(False, "--diarize",
        help="Label who spoke when: segments carry a speaker number, the Markdown starts each "
             "speaker section with **Sprecher N:** (German) or **Speaker N:**. Needs a worker "
             "with a verified diarizer; otherwise the job waits."),
    local: bool = typer.Option(False, "--local",
        help="Process on this machine with cached hub settings instead of the hub."),
):
    """Transcribe audio files through the hub (or explicitly with --local).

    Writes <stem>.md and <stem>.result.json. Nothing falls back automatically: an
    unreachable hub, a timeout or a policy denial is reported as an error.
    """
    want_private = check_privacy_flags(private, allow_cloud)
    user_instructions = instructions_arg(instructions, instructions_file)
    files = expand_inputs(inputs, recursive, EXTENSIONS)
    if not files:
        typer.echo("No audio files found.", err=True)
        raise typer.Exit(1)
    run_media(MediaRequest(
        kind="audio",
        files=files,
        output_dir=output_dir,
        force=force,
        as_json=as_json,
        no_wait=no_wait,
        wait_timeout=timeout,
        private=want_private,
        execution_profile=execution_profile,
        language=language,
        engine=engine,
        model=model,
        profile=profile,
        options=AudioOptions(translate=translate, word_timestamps=word_timestamps, diarize=diarize),
        local=local,
        instructions=user_instructions,
    ))
