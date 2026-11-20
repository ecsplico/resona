"""The hub's OpenAPI document: audiences, operation ids and the published client subset.

Every route carries exactly one audience tag. ``client`` routes are the published
contract (:meth:`ResonaAPI.openapi_client`); ``admin`` (management, evaluations,
testbench) and ``worker`` (pull-worker protocol) routes stay internal.

Operation ids never come from a Python function name. Client routes set
``operation_id`` (``<resource>_<action>``: ``jobs_create``, ``jobs_result``) at
the route; every other route gets :func:`operation_id` from its method and path.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

CLIENT = "client"
ADMIN = "admin"
WORKER = "worker"
AUDIENCES = (CLIENT, ADMIN, WORKER)

AUDIENCE_TAGS = [
    {"name": CLIENT, "description": "Published contract: uploads, jobs, results, audio, engines, profiles, settings."},
    {"name": ADMIN, "description": "Internal: management API, webhook endpoints, evaluations, testbench."},
    {"name": WORKER, "description": "Internal: pull-worker protocol."},
]


def operation_id(route: APIRoute) -> str:
    """``<method>_<path words>``: stable while method and path are, whatever the function is called."""
    method = sorted(route.methods)[0].lower()
    words = re.sub(r"[^0-9a-z]+", "_", route.path_format.lower()).strip("_")
    return f"{method}_{words}"


_DEFAULT_VALIDATION_REF = "#/components/schemas/HTTPValidationError"
_METHODS = ("get", "put", "post", "delete", "patch", "options", "head")


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"]} if isinstance(node.get("$ref"), str) else set()
        return found.union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    return set()


def drop_unreachable_validation_errors(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove the framework's default 422 from operations that take no input.

    FastAPI adds it to every route with a dependency parameter, also when the only
    ones are the credential headers, which never fail validation (a bad credential
    is 401). The two default models go too once nothing refers to them.
    """
    for item in schema.get("paths", {}).values():
        for method in _METHODS:
            operation = item.get(method)
            if not operation or operation.get("parameters") or operation.get("requestBody"):
                continue
            response = operation.get("responses", {}).get("422")
            if response and _refs(response) == {_DEFAULT_VALIDATION_REF}:
                del operation["responses"]["422"]
    models = schema.get("components", {}).get("schemas", {})
    if _DEFAULT_VALIDATION_REF not in _refs(schema.get("paths", {})):
        models.pop("HTTPValidationError", None)
        if "#/components/schemas/ValidationError" not in _refs(schema):
            models.pop("ValidationError", None)
    return schema


class ResonaAPI(FastAPI):
    """FastAPI app whose schema can be narrowed to the client audience."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("generate_unique_id_function", operation_id)
        super().__init__(**kwargs)
        self._client_schema: dict[str, Any] | None = None

    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            drop_unreachable_validation_errors(super().openapi())
        return self.openapi_schema

    def openapi_client(self) -> dict[str, Any]:
        """The OpenAPI document of the ``client`` routes only, with just the schemas they use.

        Filtered from the full document rather than built from ``self.routes``: newer
        FastAPI releases keep included routers as lazy entries in ``self.routes``.
        """
        if self._client_schema is None:
            schema = copy.deepcopy(self.openapi())
            paths = {}
            for path, item in schema.get("paths", {}).items():
                kept = {m: op for m, op in item.items() if m in _METHODS and CLIENT in op.get("tags", [])}
                if kept:
                    paths[path] = kept
            schema["paths"] = paths
            schema.pop("webhooks", None)
            used = {tag for item in paths.values() for op in item.values() for tag in op.get("tags", [])}
            if "tags" in schema:
                schema["tags"] = [tag for tag in schema["tags"] if tag["name"] in used]
            models = schema.get("components", {}).get("schemas")
            if models is not None:
                prefix = "#/components/schemas/"
                reachable: set[str] = set()
                frontier = {ref.removeprefix(prefix) for ref in _refs(paths) if ref.startswith(prefix)}
                while frontier:
                    name = frontier.pop()
                    if name in reachable or name not in models:
                        continue
                    reachable.add(name)
                    frontier |= {ref.removeprefix(prefix) for ref in _refs(models[name]) if ref.startswith(prefix)}
                schema["components"]["schemas"] = {name: model for name, model in models.items() if name in reachable}
            self._client_schema = schema
        return self._client_schema


__all__ = ["ADMIN", "AUDIENCES", "AUDIENCE_TAGS", "CLIENT", "WORKER", "ResonaAPI", "operation_id"]
