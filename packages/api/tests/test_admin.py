"""Hub-local admin commands issue, store, and revoke credentials."""

import hashlib

import pytest

from sqlmodel import Session, select

from resona_api.admin import approve_worker, enroll_worker, issue_credential, main, revoke_credential
from resona_api.db.engine import engine
from resona_api.auth import resolve_token
from resona_api.db.models import Credential, Worker


def test_issued_token_authenticates(anon_client, fresh_org):
    with Session(engine) as session:
        _, token = issue_credential(session, fresh_org, "client")
        session.commit()

    resp = anon_client.get("/v1/settings", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200


def test_database_stores_only_the_token_hash(fresh_org):
    with Session(engine) as session:
        cred_id, token = issue_credential(session, fresh_org, "client")
        session.commit()

    with Session(engine) as session:
        row = session.get(Credential, cred_id)
        stored = " ".join(str(value) for value in row.model_dump().values())

    assert row.secret_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token not in stored


def test_revoked_token_is_rejected(anon_client, fresh_org):
    with Session(engine) as session:
        cred_id, token = issue_credential(session, fresh_org, "client")
        session.commit()
    headers = {"Authorization": f"Bearer {token}"}
    assert anon_client.get("/v1/settings", headers=headers).status_code == 200

    with Session(engine) as session:
        revoke_credential(session, cred_id)

    assert anon_client.get("/v1/settings", headers=headers).status_code == 401


def test_cli_issue_prints_token_once_and_it_authenticates(capsys, anon_client, fresh_org):
    assert main(["credential", "issue", "--organization", fresh_org, "--role", "org_admin"]) == 0

    out = capsys.readouterr().out
    token_line = next(line for line in out.splitlines() if line.startswith("token: "))
    token = token_line.removeprefix("token: ")
    assert token and token not in out.replace(token_line, "")

    resp = anon_client.put(
        "/v1/settings",
        json={"language": "en"},
        headers={"Authorization": f"Bearer {token}", "If-Match": '"1"'},
    )
    assert resp.status_code == 200


def test_cli_issues_hub_operator_token_for_the_admin_api_only(capsys, anon_client, fresh_org):
    assert main(["credential", "issue", "--organization", fresh_org, "--role", "hub_operator"]) == 0

    out = capsys.readouterr().out
    token = next(line for line in out.splitlines() if line.startswith("token: ")).removeprefix("token: ")
    headers = {"Authorization": f"Bearer {token}"}

    me = anon_client.get("/v1/admin/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "hub_operator"
    assert anon_client.get("/v1/settings", headers=headers).status_code == 403


def test_cli_rejects_unknown_organization(capsys):
    assert main(["credential", "issue", "--organization", "no-such-org", "--role", "client"]) == 1
    assert "unknown organization" in capsys.readouterr().err


def test_cli_revoke_credential_by_id(capsys, anon_client, fresh_org):
    with Session(engine) as session:
        cred_id, token = issue_credential(session, fresh_org, "client")
        session.commit()

    assert main(["credential", "revoke", cred_id]) == 0

    assert anon_client.get(
        "/v1/settings", headers={"Authorization": f"Bearer {token}"},
    ).status_code == 401
    with Session(engine) as session:
        assert session.exec(
            select(Credential).where(Credential.id == cred_id)
        ).one().revoked_at is not None


def test_enrolled_worker_is_unapproved_and_its_token_resolves_to_worker_role(fresh_org):
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "pc-one", fresh_org)
        session.commit()

    with Session(engine) as session:
        worker = session.get(Worker, worker_id)
        assert worker.approved is False
        assert worker.approved_private is False
        principal = resolve_token(session, token)

    assert principal is not None
    assert principal.role == "worker"
    assert principal.worker_id == worker_id
    assert principal.organization_id == fresh_org


def test_cli_enroll_prints_worker_id_and_token(capsys, fresh_org):
    assert main(["worker", "enroll", "pc-cli", "--organization", fresh_org]) == 0
    out = capsys.readouterr().out.splitlines()
    worker_id = out[0].split(": ", 1)[1]
    token = out[1].split(": ", 1)[1]

    with Session(engine) as session:
        assert session.get(Worker, worker_id) is not None
        assert resolve_token(session, token).worker_id == worker_id


def _write_capabilities(tmp_path, payload: str):
    path = tmp_path / "caps.json"
    path.write_text(payload, encoding="utf-8")
    return str(path)


def test_cli_approve_private_stores_validated_capabilities(capsys, tmp_path, fresh_org):
    with Session(engine) as session:
        worker_id, _ = enroll_worker(session, "pc-approve", fresh_org)
        session.commit()
    caps = _write_capabilities(tmp_path, (
        '[{"engine": "mlx-whisper", "model": "large-v3", "accelerator": "metal"}]'
    ))

    assert main(["worker", "approve", worker_id, "--private",
                 "--capabilities-file", caps]) == 0

    with Session(engine) as session:
        worker = session.get(Worker, worker_id)
        assert worker.approved is True
        assert worker.approved_private is True
        assert "mlx-whisper" in worker.approved_capabilities


@pytest.mark.parametrize("payload", [
    '[{"engine": "x", "model": "m", "accelerator": "tpu"}]',
    '[{"engine": "x", "model": "m", "accelerator": "cpu", "surprise": 1}]',
    '{"engine": "x"}',
])
def test_cli_approve_rejects_invalid_candidates_and_leaves_worker_unapproved(
        capsys, tmp_path, fresh_org, payload):
    with Session(engine) as session:
        worker_id, _ = enroll_worker(session, "pc-bad", fresh_org)
        session.commit()
    caps = _write_capabilities(tmp_path, payload)

    assert main(["worker", "approve", worker_id, "--private",
                 "--capabilities-file", caps]) == 1
    assert capsys.readouterr().err.startswith("error: ")

    with Session(engine) as session:
        worker = session.get(Worker, worker_id)
        assert worker.approved is False
        assert worker.approved_capabilities == "[]"


def test_cli_registers_tokens_by_hash_without_printing_them(capsys, anon_client, fresh_org):
    import secrets

    client_token, worker_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    client_hash = hashlib.sha256(client_token.encode()).hexdigest()
    worker_hash = hashlib.sha256(worker_token.encode()).hexdigest()

    assert main(["credential", "issue", "--organization", fresh_org, "--role", "client",
                 "--token-sha256", client_hash]) == 0
    assert main(["worker", "enroll", "pc-hash", "--organization", fresh_org,
                 "--token-sha256", worker_hash]) == 0
    captured = capsys.readouterr()
    assert "registered by hash" in captured.out
    assert client_token not in captured.out + captured.err

    assert anon_client.get(
        "/v1/settings", headers={"Authorization": f"Bearer {client_token}"},
    ).status_code == 200
    with Session(engine) as session:
        assert resolve_token(session, worker_token).role == "worker"


@pytest.mark.parametrize("value", ["abc", "g" * 64])
def test_cli_rejects_malformed_token_hash(capsys, fresh_org, value):
    assert main(["credential", "issue", "--organization", fresh_org, "--role", "client",
                 "--token-sha256", value]) == 1
    assert "--token-sha256" in capsys.readouterr().err


def test_cli_rejects_a_duplicate_token_hash(capsys, fresh_org):
    digest = hashlib.sha256(b"same-token").hexdigest()
    args = ["credential", "issue", "--organization", fresh_org, "--role", "client", "--token-sha256", digest]
    assert main(args) == 0
    assert main(args) == 1
    assert "already exists" in capsys.readouterr().err
