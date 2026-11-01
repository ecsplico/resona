"""Hub-local administration: ``resona-admin``.

Runs directly against the hub database. Every command migrates the schema first,
except ``restore``, which runs only while the hub is stopped (see ``restore.py``).
Issued tokens are printed once; only their SHA-256 hashes are stored. With
``--token-sha256`` a token generated elsewhere is registered by its hash alone, so
the token itself never passes through the hub host or its logs.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from sqlmodel import Session, select

from resona_job_core import Candidate, default_settings

from . import releases
from .auth import ROLE_CLIENT, ROLE_ORG_ADMIN, ROLE_WORKER, hash_token
from .db.engine import create_db_and_tables, engine
from .db.models import ApprovedReleaseTuple, Credential, Organization, Release, SettingsRevision, Worker

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class AdminError(RuntimeError):
    """A requested admin operation is invalid."""


def _now() -> datetime:
    return datetime.now(UTC)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _secret_hash(token_sha256: str | None) -> tuple[str, str | None]:
    """``(stored hash, token to show once)``: a fresh token, or a caller-supplied hash and no token."""
    if token_sha256 is None:
        token = _new_token()
        return hash_token(token), token
    value = token_sha256.strip().lower()
    if not _SHA256_HEX.fullmatch(value):
        raise AdminError("--token-sha256 must be 64 lowercase hex characters (SHA-256 of the token)")
    return value, None


def create_organization(session: Session, org_id: str) -> Organization:
    """Create an organization with settings revision 1 (the defaults)."""
    if not org_id.strip():
        raise AdminError("organization id must not be empty")
    if session.get(Organization, org_id) is not None:
        raise AdminError(f"organization {org_id!r} already exists")
    org = Organization(id=org_id, storage_id=secrets.token_hex(16), created_at=_now())
    session.add(org)
    session.add(
        SettingsRevision(
            organization_id=org_id,
            revision=1,
            settings_json=default_settings().model_dump_json(),
            created_at=_now(),
        )
    )
    session.commit()
    return org


def _require_organization(session: Session, org_id: str) -> None:
    if session.get(Organization, org_id) is None:
        raise AdminError(f"unknown organization {org_id!r}")


def issue_credential(
    session: Session, organization_id: str, role: str, *, token_sha256: str | None = None
) -> tuple[str, str | None]:
    """Issue a client or org_admin credential. Returns ``(credential_id, token)``.

    With ``token_sha256`` the credential is registered by that hash and no token is returned.
    """
    if role not in (ROLE_CLIENT, ROLE_ORG_ADMIN):
        raise AdminError("role must be 'client' or 'org_admin'")
    _require_organization(session, organization_id)
    secret_hash, token = _secret_hash(token_sha256)
    if session.exec(select(Credential).where(Credential.secret_hash == secret_hash)).first() is not None:
        raise AdminError("a credential with this token already exists")
    cred = Credential(
        id=f"cred_{secrets.token_hex(8)}",
        secret_hash=secret_hash,
        organization_id=organization_id,
        role=role,
        created_at=_now(),
    )
    session.add(cred)
    session.commit()
    return cred.id, token


def enroll_worker(
    session: Session, name: str, organization_id: str, *, token_sha256: str | None = None
) -> tuple[str, str | None]:
    """Enroll an unapproved worker with its single worker credential.

    Returns ``(worker_id, token)`` (no token with ``token_sha256``). The worker cannot
    claim work until approved.
    """
    if not name.strip():
        raise AdminError("worker name must not be empty")
    _require_organization(session, organization_id)
    secret_hash, token = _secret_hash(token_sha256)
    if session.exec(select(Credential).where(Credential.secret_hash == secret_hash)).first() is not None:
        raise AdminError("a credential with this token already exists")
    worker = Worker(
        id=f"wrk_{secrets.token_hex(8)}",
        organization_id=organization_id,
        name=name,
        approved=False,
        approved_private=False,
        last_seen=None,
    )
    session.add(worker)
    session.flush()  # the credential's worker_id FK needs the worker row first
    session.add(
        Credential(
            id=f"cred_{secrets.token_hex(8)}",
            secret_hash=secret_hash,
            organization_id=organization_id,
            role=ROLE_WORKER,
            worker_id=worker.id,
            created_at=_now(),
        )
    )
    session.commit()
    return worker.id, token


def approve_worker(
    session: Session,
    worker_id: str,
    *,
    private: bool,
    capabilities: Sequence[dict],
) -> Worker:
    """Approve a worker for private or non-private work with validated capability tuples."""
    worker = session.get(Worker, worker_id)
    if worker is None or worker.revoked_at is not None:
        raise AdminError(f"unknown or revoked worker {worker_id!r}")
    validated = [Candidate.model_validate(item).model_dump(mode="json") for item in capabilities]
    worker.approved = True
    worker.approved_private = private
    worker.approved_capabilities = json.dumps(validated, sort_keys=True)
    session.add(worker)
    session.commit()
    return worker


def revoke_credential(session: Session, credential_id: str) -> None:
    cred = session.get(Credential, credential_id)
    if cred is None:
        raise AdminError(f"unknown credential {credential_id!r}")
    if cred.revoked_at is None:
        cred.revoked_at = _now()
        session.add(cred)
        session.commit()


def revoke_worker(session: Session, worker_id: str) -> None:
    """Revoke a worker and every credential it holds."""
    worker = session.get(Worker, worker_id)
    if worker is None:
        raise AdminError(f"unknown worker {worker_id!r}")
    now = _now()
    worker.revoked_at = worker.revoked_at or now
    session.add(worker)
    for cred in session.exec(select(Credential).where(Credential.worker_id == worker_id)):
        cred.revoked_at = cred.revoked_at or now
        session.add(cred)
    session.commit()


def _read_capabilities(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise AdminError("capabilities file must contain a JSON list")
    return data


def list_releases(session: Session) -> list[str]:
    """One line per release, then one indented line per approved tuple."""
    lines = []
    for row in session.exec(select(Release).order_by(Release.staged_at)).all():
        lines.append(f"{row.release_id}\t{row.status}\t{row.manifest_sha256}")
        approvals = session.exec(
            select(ApprovedReleaseTuple).where(ApprovedReleaseTuple.release_id == row.release_id)
        ).all()
        for item in sorted(approvals, key=lambda a: (a.organization_id, a.execution_profile, a.platform)):
            lines.append(
                f"  approved\t{item.organization_id}\t{item.execution_profile}\t{item.platform}"
                f"\tevaluation {item.evaluation_id}"
            )
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resona-admin", description="Hub administration")
    top = parser.add_subparsers(dest="group", required=True)

    org = top.add_parser("organization").add_subparsers(dest="action", required=True)
    org_create = org.add_parser("create")
    org_create.add_argument("id")

    cred = top.add_parser("credential").add_subparsers(dest="action", required=True)
    cred_issue = cred.add_parser("issue")
    cred_issue.add_argument("--organization", required=True)
    cred_issue.add_argument("--role", required=True, choices=[ROLE_CLIENT, ROLE_ORG_ADMIN])
    cred_issue.add_argument(
        "--token-sha256", help="register a token generated elsewhere by its SHA-256 hex digest (no token printed)"
    )
    cred_revoke = cred.add_parser("revoke")
    cred_revoke.add_argument("credential_id")

    worker = top.add_parser("worker").add_subparsers(dest="action", required=True)
    w_enroll = worker.add_parser("enroll")
    w_enroll.add_argument("name")
    w_enroll.add_argument("--organization", required=True)
    w_enroll.add_argument(
        "--token-sha256", help="register a token generated elsewhere by its SHA-256 hex digest (no token printed)"
    )
    w_approve = worker.add_parser("approve")
    w_approve.add_argument("worker_id")
    scope = w_approve.add_mutually_exclusive_group(required=True)
    scope.add_argument("--private", action="store_true")
    scope.add_argument("--non-private", action="store_true")
    w_approve.add_argument("--capabilities-file", required=True)
    w_revoke = worker.add_parser("revoke")
    w_revoke.add_argument("worker_id")

    release = top.add_parser("release").add_subparsers(dest="action", required=True)
    r_stage = release.add_parser("stage", help="admit a release manifest for evaluation jobs")
    r_stage.add_argument("--manifest", required=True)
    release.add_parser("list", help="releases and their approved (organization, profile, platform) tuples")

    restore = top.add_parser(
        "restore",
        help="restore a database backup while the hub is stopped; revokes every restored credential",
    )
    restore.add_argument("backup", help="SQLite backup file of a hub database")
    return parser


def _print_token(token: str | None) -> None:
    if token is None:
        print("token: registered by hash (not shown)")
        return
    print(f"token: {token}")
    print("Store this token now; it is shown only once.", file=sys.stderr)


def _restore(backup: str) -> int:
    from .hublock import HubRunning
    from .restore import RestoreError, restore

    try:
        report = restore(Path(backup))
    except (HubRunning, RestoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("restore complete")
    for name in ("credentials_revoked", "workers_revoked", "leases_invalidated",
                 "inputs_unavailable", "results_unavailable", "uploads_dropped"):
        print(f"  {name}: {getattr(report, name)}")
    for name, count in report.maintenance.items():
        print(f"  maintenance.{name}: {count}")
    print("Every restored credential is revoked; issue fresh credentials and re-enroll workers.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.group == "restore":
        return _restore(args.backup)
    create_db_and_tables()
    try:
        with Session(engine) as session:
            if args.group == "organization":
                create_organization(session, args.id)
                print(f"organization {args.id} created (settings revision 1)")
            elif args.group == "credential" and args.action == "issue":
                cred_id, token = issue_credential(
                    session, args.organization, args.role, token_sha256=args.token_sha256
                )
                print(f"credential_id: {cred_id}")
                _print_token(token)
            elif args.group == "credential" and args.action == "revoke":
                revoke_credential(session, args.credential_id)
                print(f"credential {args.credential_id} revoked")
            elif args.group == "worker" and args.action == "enroll":
                worker_id, token = enroll_worker(
                    session, args.name, args.organization, token_sha256=args.token_sha256
                )
                print(f"worker_id: {worker_id}")
                _print_token(token)
            elif args.group == "worker" and args.action == "approve":
                approve_worker(
                    session,
                    args.worker_id,
                    private=args.private,
                    capabilities=_read_capabilities(args.capabilities_file),
                )
                print(f"worker {args.worker_id} approved (private={args.private})")
            elif args.group == "worker" and args.action == "revoke":
                revoke_worker(session, args.worker_id)
                print(f"worker {args.worker_id} revoked")
            elif args.group == "release" and args.action == "stage":
                try:
                    raw = Path(args.manifest).read_bytes()
                except OSError as exc:
                    raise AdminError(f"cannot read manifest: {exc.strerror}") from None
                release = releases.stage_release(session, raw)
                print(f"release {release.release_id} {release.status} (manifest sha256 {release.manifest_sha256})")
            elif args.group == "release" and args.action == "list":
                for line in list_releases(session):
                    print(line)
    except (AdminError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
