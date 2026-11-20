"""The webhook guide's verification examples (scripts/webhook_examples) accept a
hub-signed job event and reject a tampered one."""

import base64
import importlib.util
import json
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from resona_api.webhooks import new_secret
from resona_job_core.webhooks import JobEvent, sign

_EXAMPLES = Path(__file__).resolve().parents[1] / "webhook_examples"
_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "guide" / "webhooks.md"


def _signed_request() -> tuple[dict[str, str], bytes, str]:
    """Headers, body and secret exactly as the hub's dispatcher produces them."""
    secret = new_secret()
    event = JobEvent(
        type="job.failed",
        job_id=42,
        kind="audio",
        status="failed",
        error_code="deadline_exceeded",
        finished_at=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
    )
    body = event.model_dump_json().encode("utf-8")
    delivery_id = uuid.uuid4().hex
    timestamp = int(time.time())
    headers = {
        "content-type": "application/json",
        "webhook-id": delivery_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(secret, delivery_id, timestamp, body),
    }
    return headers, body, secret


def _tampered(body: bytes) -> bytes:
    return body.replace(b'"job_id":42', b'"job_id":43')


def test_guide_includes_both_examples():
    guide = _GUIDE.read_text(encoding="utf-8")
    assert '--8<-- "scripts/webhook_examples/verify_python.py"' in guide
    assert '--8<-- "scripts/webhook_examples/verify_node.mjs"' in guide


def test_python_example():
    spec = importlib.util.spec_from_file_location("verify_python", _EXAMPLES / "verify_python.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    headers, body, secret = _signed_request()

    assert module.handle_webhook(headers, _tampered(body), secret) == 401
    assert module.handle_webhook(headers, body, new_secret()) == 401
    assert module.handle_webhook(headers, body, secret) == 200
    assert module.seen_ids == {headers["webhook-id"]}
    assert module.handle_webhook(headers, body, secret) == 200  # duplicate, dropped
    assert module.seen_ids == {headers["webhook-id"]}


_NODE_RUNNER = """
import { verifyWebhook } from %s;
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const req = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const event = verifyWebhook(req.headers, Buffer.from(req.body, "base64"), req.secret);
process.stdout.write(JSON.stringify(event));
"""


def _run_node(headers: dict[str, str], body: bytes, secret: str):
    request = {"headers": headers, "body": base64.b64encode(body).decode("ascii"), "secret": secret}
    module_url = json.dumps((_EXAMPLES / "verify_node.mjs").as_uri())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", _NODE_RUNNER % module_url],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_example():
    headers, body, secret = _signed_request()

    event = _run_node(headers, body, secret)
    assert JobEvent.model_validate(event) == JobEvent.model_validate_json(body)
    assert _run_node(headers, _tampered(body), secret) is None
    assert _run_node(headers, body, new_secret()) is None
    stale = dict(headers, **{"webhook-timestamp": str(int(time.time()) - 3600)})
    assert _run_node(stale, body, secret) is None
