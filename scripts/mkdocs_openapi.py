"""MkDocs hook: render the committed ``contract/openapi.json`` into a reference page.

A page holding the marker ``<!-- openapi: contract/openapi.json -->`` gets the document
rendered as Markdown (operations, parameters, request bodies, responses, schemas) at
build time. Offline: no plugin, no network, nothing beyond MkDocs itself. The document
itself is published next to the page as ``openapi.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File

MARKER = re.compile(r"<!--\s*openapi:\s*(\S+)\s*-->")
METHODS = ("get", "put", "post", "delete", "patch")
SCHEMA_PREFIX = "#/components/schemas/"
PAGE = "reference/http-api.md"
SOURCE = "contract/openapi.json"


def _root(config) -> Path:
    return Path(config["config_file_path"]).resolve().parent


def on_config(config, **kwargs):
    config["watch"].append(str(_root(config) / "contract"))
    return config


def on_files(files, config, **kwargs):
    source = _root(config) / SOURCE
    if source.is_file():
        files.append(File.generated(config, str(Path(PAGE).parent / "openapi.json"), abs_src_path=str(source)))
    return files


def on_page_markdown(markdown: str, page, config, **kwargs) -> str:
    def replace(match: re.Match) -> str:
        path = _root(config) / match.group(1)
        if not path.is_file():
            raise PluginError(f"{page.file.src_path}: {match.group(1)} not found; run `uv run python scripts/gen_openapi.py`")
        return render(json.loads(path.read_text(encoding="utf-8")))

    return MARKER.sub(replace, markdown)


# ── Rendering ─────────────────────────────────────────────────────────


def _anchor(name: str) -> str:
    return "schema-" + re.sub(r"[^0-9A-Za-z_-]+", "-", name)


def _cell(text: Any) -> str:
    return str(text or "").strip().replace("|", "\\|").replace("\n", "<br>")


def type_of(schema: Any) -> str:
    """One-line Markdown type of a JSON schema; references link to their schema section."""
    if not isinstance(schema, dict) or not schema:
        return "any"
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(SCHEMA_PREFIX):
        name = ref.removeprefix(SCHEMA_PREFIX)
        return f"[{name}](#{_anchor(name)})"
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " or ".join(type_of(option) for option in schema[key])
    if "allOf" in schema:
        return " and ".join(type_of(part) for part in schema["allOf"])
    if "const" in schema:
        return f"`{json.dumps(schema['const'])}`"
    if "enum" in schema:
        return ", ".join(f"`{json.dumps(value)}`" for value in schema["enum"])
    kind = schema.get("type")
    if isinstance(kind, list):
        return " or ".join(type_of({**schema, "type": one}) for one in kind)
    if kind == "array":
        return f"array of {type_of(schema.get('items'))}"
    if kind == "object":
        extra = schema.get("additionalProperties")
        return f"object of {type_of(extra)}" if isinstance(extra, dict) else "object"
    if kind:
        return f"{kind} ({schema['format']})" if "format" in schema else str(kind)
    return "any"


def _content(content: dict[str, Any]) -> str:
    return "<br>".join(f"`{media}`: {type_of(body.get('schema'))}" for media, body in sorted(content.items()))


def _operation(method: str, path: str, op: dict[str, Any]) -> list[str]:
    lines = [f"### `{method.upper()} {path}`", ""]
    lines += [f"Operation id: `{op.get('operationId', '')}`", ""]
    if op.get("summary"):
        lines += [f"**{op['summary']}**", ""]
    if op.get("description"):
        lines += [op["description"].strip(), ""]
    params = op.get("parameters") or []
    if params:
        lines += ["| Parameter | In | Type | Required | Description |", "|---|---|---|---|---|"]
        for p in params:
            lines.append(
                f"| `{p.get('name')}` | {p.get('in')} | {_cell(type_of(p.get('schema')))} | "
                f"{'yes' if p.get('required') else 'no'} | {_cell(p.get('description'))} |"
            )
        lines.append("")
    body = op.get("requestBody")
    if body:
        required = " (required)" if body.get("required") else ""
        lines += [f"Request body{required}: {_content(body.get('content', {}))}", ""]
    responses = op.get("responses") or {}
    if responses:
        lines += ["| Status | Description | Body |", "|---|---|---|"]
        for status, response in sorted(responses.items()):
            lines.append(
                f"| {status} | {_cell(response.get('description'))} | {_cell(_content(response.get('content', {})))} |"
            )
        lines.append("")
    return lines


def _schema(name: str, schema: dict[str, Any]) -> list[str]:
    lines = [f'<a id="{_anchor(name)}"></a>', "", f"### {name}", ""]
    if schema.get("description"):
        lines += [schema["description"].strip(), ""]
    properties = schema.get("properties") or {}
    if properties:
        required = set(schema.get("required") or [])
        lines += ["| Field | Type | Required | Description |", "|---|---|---|---|"]
        for field, prop in properties.items():
            lines.append(
                f"| `{field}` | {_cell(type_of(prop))} | {'yes' if field in required else 'no'} | "
                f"{_cell(prop.get('description'))} |"
            )
        lines.append("")
    else:
        lines += [f"Type: {type_of(schema)}", ""]
    return lines


def render(document: dict[str, Any]) -> str:
    info = document.get("info", {})
    lines = [f"Contract version `{info.get('version', '')}` (OpenAPI {document.get('openapi', '')}).", ""]
    if info.get("description"):
        lines += [info["description"].strip(), ""]
    lines += ["## Operations", ""]
    for path, item in document.get("paths", {}).items():
        for method in METHODS:
            if method in item:
                lines += _operation(method, path, item[method])
    schemas = document.get("components", {}).get("schemas", {})
    if schemas:
        lines += ["## Schemas", ""]
        for name, schema in schemas.items():
            lines += _schema(name, schema)
    return "\n".join(lines)
