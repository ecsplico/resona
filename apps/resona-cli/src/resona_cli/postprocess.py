"""``resona postprocess``: text files or an earlier job's result -> hub text jobs
(or ``--local``) -> ``<stem>.<profile>.md``.

A text job runs only a postprocess profile; nothing is transcribed again. Outputs
carry the profile in their name (``notes.md --profile fix`` -> ``notes.fix.md`` +
``notes.fix.result.json`` next to the input), so the default never replaces the
input and runs of different profiles on one input sit side by side. With
``--job`` the earlier job's unpolished Markdown (``raw_markdown``, else
``markdown``) is the input, the output stem is ``job-<id>.<profile>`` in the
current directory (the hub does not keep source file names) and the input is
reported as ``job:<id>``.
"""
import json
import re
import tempfile
from pathlib import Path
from typing import Optional

import typer
from resona_client.client import TextOptions

from .hub import INSTRUCTIONS_FILE_HELP, INSTRUCTIONS_HELP, check_privacy_flags, connect_hub, fail, instructions_arg
from .jobs_cli import _call
from .media import MediaRequest, run_media
from .transcribe import expand_inputs

EXTENSIONS = {"md", "markdown", "txt"}


def _job_text(job_id: int) -> tuple[str, str | None]:
    """The input of a re-run: the earlier result before any postprocessing, and its language."""
    result = _call(lambda: connect_hub().get_result(job_id))
    text = result.raw_markdown if result.raw_markdown is not None else result.markdown
    return text, result.language


def profile_label(profile: str) -> str:
    """The profile's part of the output name: its name, a profile file's stem, or an
    inline JSON profile's ``name``; reduced to file-name-safe characters."""
    text = profile.strip()
    if text.startswith("{"):
        try:
            name = json.loads(text).get("name")
        except (ValueError, AttributeError):
            name = None
        label = name if isinstance(name, str) else ""
    elif "/" in text or text.lower().endswith(".json"):
        label = Path(text).stem
    else:
        label = text
    return re.sub(r"[^\w.-]+", "-", label).strip(".-") or "postprocess"


def postprocess_files(
    inputs: Optional[list[str]] = typer.Argument(
        None, help="Text/Markdown files, glob patterns, or directories.", metavar="[INPUTS]..."),
    job: Optional[int] = typer.Option(None, "--job",
        help="Re-run the profile on this earlier job's result instead of files."),
    profile: str = typer.Option(..., "--profile",
        help="Postprocess profile name (a JSON file or inline JSON only with --local)."),
    instructions: Optional[str] = typer.Option(None, "--instructions", help=INSTRUCTIONS_HELP),
    instructions_file: Optional[Path] = typer.Option(None, "--instructions-file", help=INSTRUCTIONS_FILE_HELP),
    recursive: bool = typer.Option(False, "--recursive", "-r",
        help="Recurse into directories / use `**` in glob patterns."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir",
        help="Directory for <stem>.<profile>.md and <stem>.<profile>.result.json (default: next to "
             "each input; the current directory with --job)."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing outputs."),
    as_json: bool = typer.Option(False, "--json", help="Print a JSON report on stdout."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit and print job IDs; do not wait."),
    timeout: Optional[float] = typer.Option(None, "--timeout",
        help="Seconds to wait per job; the job keeps running on the hub afterwards."),
    private: bool = typer.Option(False, "--private",
        help="Require private processing (the default)."),
    allow_cloud: bool = typer.Option(False, "--allow-cloud",
        help="Allow cloud stages; still requires the organization to enable cloud processing."),
    language: Optional[str] = typer.Option(None, "--language",
        help="Language of the text (default: the earlier job's language with --job, else the organization's setting)."),
    local: bool = typer.Option(False, "--local",
        help="Run the profile on this machine with cached hub settings instead of the hub."),
):
    """Run a postprocess profile on text without transcribing again.

    Writes <stem>.<profile>.md and <stem>.<profile>.result.json. Nothing falls back
    automatically: an unreachable hub, a timeout or a policy denial is reported as an error.
    """
    want_private = check_privacy_flags(private, allow_cloud)
    user_instructions = instructions_arg(instructions, instructions_file)
    if (job is None) == (not inputs):
        fail("give either text files or --job <id>, not both")
    input_label = None
    with tempfile.TemporaryDirectory(prefix="resona-postprocess-") as tmp:
        if job is not None:
            source = Path(tmp) / f"job-{job}.md"
            text, job_language = _job_text(job)
            source.write_text(text, encoding="utf-8")
            language = language or job_language
            files = [source]
            output_dir = output_dir or Path.cwd()
            input_label = f"job:{job}"
        else:
            files = expand_inputs(inputs or [], recursive, EXTENSIONS)
            if not files:
                typer.echo("No text files found.", err=True)
                raise typer.Exit(1)
        run_media(MediaRequest(
            kind="text",
            files=files,
            output_dir=output_dir,
            force=force,
            as_json=as_json,
            no_wait=no_wait,
            wait_timeout=timeout,
            private=want_private,
            execution_profile=None,
            language=language,
            engine=None,
            model=None,
            profile=profile,
            options=TextOptions(),
            local=local,
            instructions=user_instructions,
            output_suffix=profile_label(profile),
            input_label=input_label,
        ))
