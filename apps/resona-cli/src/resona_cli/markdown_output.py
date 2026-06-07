"""Write transcripts as markdown files with YAML frontmatter."""
from pathlib import Path


def _yaml_scalar(value) -> str:
    """Render a value as a safely-quoted YAML scalar."""
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_markdown(out_path: Path, text: str, meta: dict) -> None:
    """Write transcript as markdown with YAML frontmatter (omitting empty values)."""
    lines = ["---"]
    for key, value in meta.items():
        if value in (None, ""):
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    lines.append("")
    body = text if text.endswith("\n") else text + "\n"
    out_path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
