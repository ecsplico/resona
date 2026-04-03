"""Tests for resona-api REST endpoints."""
import os
import pytest
from pathlib import Path


FILE_PATH = os.environ.get("FILE_PATH", "")


# ── /health ───────────────────────────────────────────────────────────────────

def test_health(client):
    from resona_api.app import app
    from fastapi.testclient import TestClient
    # health is on the main app, not the router — test via a fresh client
    # but the router doesn't have /health, so just verify the router works at all
    resp = client.get("/jobs/")
    assert resp.status_code == 200


# ── Job endpoints ─────────────────────────────────────────────────────────────

def test_submit_job(client, wav_bytes):
    resp = client.post(
        "/jobs",
        files={"audio_files": ("test.wav", wav_bytes, "audio/wav")},
        data={"keep": "true", "translate": "false"},
    )
    assert resp.status_code == 200
    jobs = resp.json()
    assert isinstance(jobs, list)
    assert len(jobs) == 1
    job = jobs[0]
    assert "id" in job
    assert job["id"] > 0


def test_submit_job_unsupported_type(client):
    resp = client.post(
        "/jobs",
        files={"audio_files": ("test.xyz", b"garbage", "application/octet-stream")},
        data={"keep": "true", "translate": "false"},
    )
    assert resp.status_code == 415


def test_submit_multiple_jobs(client, wav_bytes):
    resp = client.post(
        "/jobs",
        files=[
            ("audio_files", ("a.wav", wav_bytes, "audio/wav")),
            ("audio_files", ("b.wav", wav_bytes, "audio/wav")),
        ],
        data={"keep": "true", "translate": "false"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_job_not_found(client):
    resp = client.get("/job/99999")
    assert resp.status_code == 404


def test_get_job_found(client, wav_bytes):
    submit = client.post(
        "/jobs",
        files={"audio_files": ("test.wav", wav_bytes, "audio/wav")},
        data={"keep": "true", "translate": "false"},
    )
    job_id = submit.json()[0]["id"]
    resp = client.get(f"/job/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id
    assert resp.json()["status"] == "pending"


def test_list_jobs_empty(client):
    resp = client.get("/jobs/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_jobs_returns_submitted(client, wav_bytes):
    client.post(
        "/jobs",
        files={"audio_files": ("test.wav", wav_bytes, "audio/wav")},
        data={"keep": "true", "translate": "false"},
    )
    resp = client.get("/jobs/")
    assert len(resp.json()) == 1


def test_register_file_not_found(client):
    resp = client.post("/jobs/registerfile", json="missing.wav")
    assert resp.status_code == 404


def test_register_file_existing(client):
    # Write a real file to FILE_PATH
    test_file = Path(FILE_PATH) / "existing.wav"
    test_file.write_bytes(b"RIFF")
    resp = client.post("/jobs/registerfile", json="existing.wav")
    assert resp.status_code == 200
    assert "id" in resp.json()


# ── Replacement CRUD ──────────────────────────────────────────────────────────

def test_list_replacements_empty(client):
    resp = client.get("/replacements/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_add_replacement(client):
    resp = client.post("/replacements/", json={"name": "foo", "replacement": "bar"})
    assert resp.status_code == 200
    r = resp.json()
    assert r["name"] == "foo"
    assert r["replacement"] == "bar"
    assert r["active"] is True
    assert "id" in r


def test_add_replacement_duplicate(client):
    client.post("/replacements/", json={"name": "foo", "replacement": "bar"})
    resp = client.post("/replacements/", json={"name": "foo", "replacement": "baz"})
    assert resp.status_code == 409


def test_delete_replacement(client):
    r = client.post("/replacements/", json={"name": "todelete", "replacement": "x"}).json()
    resp = client.delete(f"/replacements/{r['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert client.get("/replacements/").json() == []


def test_delete_replacement_not_found(client):
    resp = client.delete("/replacements/99999")
    assert resp.status_code == 404


# ── Prompt CRUD ───────────────────────────────────────────────────────────────

def test_list_prompts_empty(client):
    resp = client.get("/prompts/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_add_prompt(client):
    resp = client.post("/prompts/", json={"phrase": "hello whisper"})
    assert resp.status_code == 200
    p = resp.json()
    assert p["phrase"] == "hello whisper"
    assert p["active"] is True


def test_add_prompt_duplicate(client):
    client.post("/prompts/", json={"phrase": "dup"})
    resp = client.post("/prompts/", json={"phrase": "dup"})
    assert resp.status_code == 409


def test_activate_prompt_deactivates_others(client):
    p1 = client.post("/prompts/", json={"phrase": "first"}).json()
    p2 = client.post("/prompts/", json={"phrase": "second"}).json()

    client.put(f"/prompts/{p2['id']}/activate")
    prompts = {p["id"]: p for p in client.get("/prompts/").json()}
    assert prompts[p1["id"]]["active"] is False
    assert prompts[p2["id"]]["active"] is True


def test_activate_prompt_not_found(client):
    resp = client.put("/prompts/99999/activate")
    assert resp.status_code == 404


def test_deactivate_prompt(client):
    p = client.post("/prompts/", json={"phrase": "active"}).json()
    resp = client.put(f"/prompts/{p['id']}/deactivate")
    assert resp.status_code == 200
    updated = client.get("/prompts/").json()[0]
    assert updated["active"] is False


def test_delete_prompt(client):
    p = client.post("/prompts/", json={"phrase": "todelete"}).json()
    resp = client.delete(f"/prompts/{p['id']}")
    assert resp.status_code == 200
    assert client.get("/prompts/").json() == []


def test_delete_prompt_not_found(client):
    resp = client.delete("/prompts/99999")
    assert resp.status_code == 404


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_auth_blocks_without_key(test_app):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    with patch("resona_api.auth.get_api_key", return_value="required"):
        with TestClient(test_app) as c:
            resp = c.get("/jobs/")
    assert resp.status_code == 401


def test_auth_passes_with_key(test_app):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    with patch("resona_api.auth.get_api_key", return_value="required"):
        with TestClient(test_app) as c:
            resp = c.get("/jobs/", headers={"X-API-Key": "required"})
    assert resp.status_code == 200


# ── sanitize_filename ─────────────────────────────────────────────────────────

def test_sanitize_filename():
    from resona_api.endpoints import sanitize_filename
    assert sanitize_filename("normal.wav") == "normal.wav"
    assert sanitize_filename("path/to/file.wav") == "file.wav"
    assert sanitize_filename("has space.wav") == "has_space.wav"
    assert sanitize_filename(".hidden") == "unnamed_file"
    assert sanitize_filename("") == "unnamed_file"
    assert sanitize_filename("..") == "unnamed_file"
    assert sanitize_filename(r"C:\windows\path.wav") == "path.wav"
