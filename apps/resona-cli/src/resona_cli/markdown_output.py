"""Read and write transcripts as markdown files with YAML frontmatter."""
from pathlib import Path


def _yaml_scalar(value) -> str:
    """Render a value as a safely-quoted YAML scalar."""
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


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


def strip_frontmatter(text: str) -> tuple[str, dict]:
    """Split a `write_markdown`-style document into (body, meta).

    Returns the text unchanged with an empty meta dict when it has no
    ``---``-delimited frontmatter block at the start.
    """
    if not text.startswith("---\n") and text != "---":
        return text, {}
    lines = text.split("\n")
    try:
        end = lines.index("---", 1)
    except ValueError:
        return text, {}
    meta: dict = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = _unquote_yaml_scalar(value)
    body = "\n".join(lines[end + 1:])
    if body.startswith("\n"):
        body = body[1:]
    return body, meta


def read_markdown(path: Path) -> tuple[str, dict]:
    """Read a `write_markdown`-produced file back into (body, meta)."""
    return strip_frontmatter(path.read_text(encoding="utf-8"))
