"""The OpenAPI document as a contract: audiences, operation ids, typed responses, errors."""
import re

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from resona_api import schemas
from resona_api.openapi import ADMIN, AUDIENCES, CLIENT, WORKER, ResonaAPI, operation_id

METHODS = ("get", "put", "post", "delete", "patch")

# The published names: generated client methods are called after them, so a change here is a
# breaking change of the contract, not a refactoring.
CLIENT_OPERATIONS = {
    ("post", "/v1/uploads"): "uploads_create",
    ("post", "/v1/upload-links"): "upload_links_create",
    ("post", "/v1/upload-links/upload"): "upload_links_upload",
    ("post", "/v1/jobs"): "jobs_create",
    ("get", "/v1/jobs"): "jobs_list",
    ("get", "/v1/jobs/{job_id}"): "jobs_get",
    ("delete", "/v1/jobs/{job_id}"): "jobs_delete",
    ("get", "/v1/jobs/{job_id}/result"): "jobs_result",
    ("get", "/v1/jobs/{job_id}/result/markdown"): "jobs_result_markdown",
    ("post", "/v1/jobs/{job_id}/cancel"): "jobs_cancel",
    ("post", "/v1/audio/transcriptions"): "audio_transcriptions_create",
    ("post", "/v1/audio/speech"): "audio_speech_create",
    ("get", "/v1/engines"): "engines_list",
    ("get", "/v1/listen/engines"): "listen_engines_list",
    ("post", "/v1/listen"): "listen_transcribe",
    ("get", "/profiles"): "profiles_list",
    ("get", "/profiles/{name}"): "profiles_get",
    ("put", "/profiles/{name}"): "profiles_put",
    ("delete", "/profiles/{name}"): "profiles_delete",
    ("get", "/v1/settings"): "settings_get",
    ("put", "/v1/settings"): "settings_put",
    ("get", "/v1/version"): "version_get",
    ("get", "/health"): "health_get",
    ("get", "/ready"): "ready_get",
}
# Routes that cannot fail: no credential, no input.
NO_ERRORS = {"health_get", "version_get"}
ERROR_MODELS = {"ErrorBody", "JobErrorBody", "DetailErrorBody", "ValidationErrorBody"}
CODED_MODELS = {"ErrorBody", "JobErrorBody"}
INTERNAL_PREFIXES = ("/v1/workers", "/v1/admin", "/v1/evaluations", "/v1/baselines", "/v1/testbench", "/v1/webhooks")


@pytest.fixture(scope="module")
def app():
    from resona_api.app import app
    return app


@pytest.fixture(scope="module")
def document(app):
    return app.openapi()


@pytest.fixture(scope="module")
def client_document(app):
    return app.openapi_client()


def operations(document):
    for path, item in document["paths"].items():
        for method in METHODS:
            if method in item:
                yield method, path, item[method]


def refs(node) -> set[str]:
    """Names of the component schemas a node refers to."""
    if isinstance(node, dict):
        found = {node["$ref"].rsplit("/", 1)[-1]} if isinstance(node.get("$ref"), str) else set()
        return found.union(*(refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(refs(value) for value in node))
    return set()


def response_problems(operation: dict) -> list[str]:
    """What a client operation's ``responses`` lack: a typed 2xx and declared, coded errors."""
    problems = []
    responses = operation["responses"]
    success = {status: r for status, r in responses.items() if status.startswith("2")}
    if not success:
        problems.append("no 2xx response")
    for status, response in success.items():
        typed = [media for media in response.get("content", {}).values() if media.get("schema")]
        if status != "204" and not typed:
            problems.append(f"{status} has no response schema")
    errors = {status: r for status, r in responses.items() if status[0] in "45"}
    if operation.get("security"):
        problems += [f"{status} is not declared" for status in ("401", "403") if status not in errors]
    if (operation.get("parameters") or operation.get("requestBody")) and "422" not in errors:
        problems.append("422 is not declared although the operation takes input")
    if not errors and operation["operationId"] not in NO_ERRORS:
        problems.append("no error response")
    for status, response in errors.items():
        if operation["operationId"] == "ready_get" and status == "503":
            continue  # the probe answers its own body, not an error
        models = refs(response.get("content", {}))
        if not models or not models <= ERROR_MODELS:
            problems.append(f"{status} is not an error model: {sorted(models)}")
        codes = response.get("x-error-codes")
        if models & CODED_MODELS and not codes:
            problems.append(f"{status} lists no error codes")
        if codes and not models & CODED_MODELS:
            problems.append(f"{status} lists codes without ErrorBody")
    return problems


# ── Audiences ─────────────────────────────────────────────────────────────


def test_every_route_has_exactly_one_audience(document):
    for method, path, operation in operations(document):
        audiences = [tag for tag in operation["tags"] if tag in AUDIENCES]
        assert len(audiences) == 1, f"{method} {path}: {operation['tags']}"


def test_audience_follows_the_path(document):
    for method, path, operation in operations(document):
        if path.startswith("/v1/workers"):
            expected = WORKER
        elif path.startswith(INTERNAL_PREFIXES):
            expected = ADMIN
        else:
            expected = CLIENT
        assert expected in operation["tags"], f"{method} {path}"


def test_client_document_is_the_client_routes_only(client_document):
    found = {(method, path) for method, path, _ in operations(client_document)}
    assert found == set(CLIENT_OPERATIONS)
    assert not [path for path in client_document["paths"] if path.startswith(INTERNAL_PREFIXES)]
    for _, _, operation in operations(client_document):
        assert CLIENT in operation["tags"]


def test_client_document_is_self_contained(client_document):
    models = client_document["components"]["schemas"]
    assert refs(client_document) <= set(models) | {"bearerAuth"}
    # The framework's default validation error only stands for an undeclared 422.
    assert "HTTPValidationError" not in models
    assert {tag["name"] for tag in client_document["tags"]} >= {CLIENT}
    assert not {ADMIN, WORKER} & {tag["name"] for tag in client_document["tags"]}


def test_served_document_is_the_full_one(app, document):
    served = TestClient(app).get("/openapi.json").json()
    assert served == document
    assert any(path.startswith("/v1/workers") for path in served["paths"])


# ── Operation ids ─────────────────────────────────────────────────────────


def test_operation_ids_are_unique(document):
    ids = [operation["operationId"] for _, _, operation in operations(document)]
    assert len(ids) == len(set(ids))


def test_client_operation_ids_are_the_published_names(document):
    found = {
        (method, path): operation["operationId"]
        for method, path, operation in operations(document)
        if CLIENT in operation["tags"]
    }
    assert found == CLIENT_OPERATIONS
    for name in found.values():
        assert re.fullmatch(r"[a-z]+(_[a-z]+)+", name), name


def test_internal_operation_ids_come_from_method_and_path(document):
    for method, path, operation in operations(document):
        if CLIENT in operation["tags"]:
            continue
        words = re.sub(r"[^0-9a-z]+", "_", path.lower()).strip("_")
        assert operation["operationId"] == f"{method}_{words}"


def test_operation_id_ignores_the_function_name():
    def build(function):
        router = APIRouter()
        router.add_api_route("/v1/things/{thing_id}", function, methods=["GET"])
        return router.routes[0]

    def first(thing_id: int):
        return {}

    def renamed(thing_id: int):
        return {}

    assert operation_id(build(first)) == operation_id(build(renamed)) == "get_v1_things_thing_id"


# ── Responses ─────────────────────────────────────────────────────────────


def test_every_client_route_declares_typed_responses_and_errors(client_document):
    problems = {
        operation["operationId"]: found
        for _, _, operation in operations(client_document)
        if (found := response_problems(operation))
    }
    assert not problems


def test_an_undeclared_client_route_is_reported():
    router = APIRouter(tags=[CLIENT])

    @router.get("/v1/bare/{item_id}", operation_id="bare_get")
    def bare(item_id: int):
        return {}

    @router.get("/v1/declared/{item_id}", operation_id="declared_get", response_model=schemas.UploadView,
                responses=schemas.error_responses({404: ["not_found"], 422: [schemas.VALIDATION]}, auth=False))
    def declared(item_id: int):
        return {}

    app = ResonaAPI()
    app.include_router(router)
    paths = app.openapi_client()["paths"]
    assert response_problems(paths["/v1/declared/{item_id}"]["get"]) == []
    problems = response_problems(paths["/v1/bare/{item_id}"]["get"])
    assert "200 has no response schema" in problems
    assert any("422 is not an error model" in problem for problem in problems)


def test_error_body_is_one_shared_model(client_document):
    body = client_document["components"]["schemas"]["ErrorBody"]
    assert set(body["properties"]) == {"error", "message"} == set(body["required"])


def test_documented_codes(client_document):
    def codes(method, path, status):
        return client_document["paths"][path][method]["responses"][status]["x-error-codes"]

    assert codes("get", "/v1/jobs/{job_id}", "404") == ["job_not_found"]
    assert codes("get", "/v1/jobs/{job_id}/result", "409") == ["not_ready"]
    assert codes("get", "/v1/jobs/{job_id}/result", "410") == ["result_expired", "content_unavailable"]
    assert {"idempotency_conflict", "settings_missing"} == set(codes("post", "/v1/jobs", "409"))
    assert "policy_denied" in codes("post", "/v1/jobs", "403")
    assert codes("post", "/v1/upload-links/upload", "401") == ["invalid_upload_link"]
    assert codes("post", "/v1/audio/transcriptions", "504") == ["timeout"]


def test_success_models(client_document):
    def model(method, path, status="200"):
        return refs(client_document["paths"][path][method]["responses"][status]["content"]["application/json"])

    assert model("post", "/v1/jobs", "202") == {"JobAccepted"}
    assert model("get", "/v1/jobs") == {"JobPage"}
    assert model("get", "/v1/jobs/{job_id}") == {"JobView"}
    assert model("get", "/v1/jobs/{job_id}/result") == {"JobResult"}
    assert model("get", "/v1/jobs/{job_id}/result/markdown") == {"MarkdownChunk"}
    assert model("get", "/v1/settings") == {"SettingsDoc"}
    assert model("get", "/ready", "503") == {"ReadyView"}
    assert "204" in client_document["paths"]["/v1/jobs/{job_id}"]["delete"]["responses"]
    speech = client_document["paths"]["/v1/audio/speech"]["post"]["responses"]["200"]["content"]
    assert speech == {"audio/*": {"schema": {"type": "string", "format": "binary"}}}


# ── Security ──────────────────────────────────────────────────────────────


def test_bearer_scheme_is_declared_once(document, client_document):
    for doc in (document, client_document):
        assert doc["components"]["securitySchemes"] == {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": doc["components"]["securitySchemes"]["bearerAuth"]["description"],
            }
        }
    for method, path, operation in operations(document):
        names = {parameter["name"].lower() for parameter in operation.get("parameters", [])}
        assert not names & {"authorization", "x-api-key"}, f"{method} {path}"
        if operation.get("security"):
            assert operation["security"] == [{"bearerAuth": []}]


def test_open_client_routes_carry_no_security(client_document):
    open_routes = {
        operation["operationId"] for _, _, operation in operations(client_document) if not operation.get("security")
    }
    assert open_routes == {"health_get", "ready_get", "version_get", "upload_links_upload"}


def test_secured_client_routes_answer_the_declared_401(app, client_document, create_tables):
    client = TestClient(app)
    for method, path, operation in operations(client_document):
        if not operation.get("security"):
            continue
        response = client.request(method.upper(), path.replace("{job_id}", "1").replace("{name}", "default"))
        assert response.status_code == 401, f"{method} {path}"
        body = response.json()
        assert set(body) == {"error", "message"}
        assert body["error"] in operation["responses"]["401"]["x-error-codes"]
