#!/usr/bin/env python3
"""Run serial WMS gates with durable evidence and canonical MySQL preflight."""

import argparse
import atexit
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def preflight(*, schema=True):
    import django
    import environ

    local = ROOT / ".env.test.local"
    if local.exists():
        environ.Env.read_env(local, overwrite=False)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings")
    django.setup()
    from django.conf import settings
    from django.db import connections

    from wmsmaster.test_coordination import DatabaseLease

    with DatabaseLease(settings.DATABASES["default"]):
        try:
            return _schema_identity(schema=schema)
        finally:
            connections.close_all()


def _schema_identity(*, schema):
    from django.apps import apps
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from wmsmaster.test_database import validate_test_process, verify_test_database

    validate_test_process(connection.settings_dict)
    identity = verify_test_database(connection.settings_dict)
    if schema:
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            raise RuntimeError("Pending migrations: a justified clean migration gate is required.")
        missing = []
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
            for model in apps.get_models(include_auto_created=True):
                opts = model._meta
                if not opts.managed or opts.proxy or opts.swapped:
                    continue
                if opts.db_table not in tables:
                    missing.append(opts.db_table)
                    continue
                columns = {
                    item.name
                    for item in connection.introspection.get_table_description(
                        cursor, opts.db_table
                    )
                }
                missing.extend(
                    f"{opts.db_table}.{field.column}"
                    for field in opts.local_fields
                    if field.column not in columns
                )
        if missing:
            raise RuntimeError(f"Missing model tables/columns: {missing}")
        identity["migration_heads"] = executor.loader.graph.leaf_nodes()
    connection.close()
    return identity


def main():
    from scripts.test_candidate import candidate_identity, verify_candidate
    from scripts.test_process import run_managed
    from wmsmaster.test_coordination import secure_directory, secure_file_lock
    from wmsmaster.test_evidence import atomic_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "preflight",
            "collect",
            "targeted",
            "functional",
            "migrations",
            "backend",
            "frontend",
            "browser",
            "release",
        ],
    )
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--fast-password-hashers", action="store_true")
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--clean-db", action="store_true")
    args = parser.parse_args()
    if args.clean_db and args.stage not in {"functional", "backend", "release"}:
        parser.error("--clean-db is restricted to the final backend verification")
    candidate = (
        verify_candidate(ROOT, json.loads(args.candidate_manifest.read_text()))
        if args.candidate_manifest
        else candidate_identity(ROOT)
    )
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    manifest_path = artifacts / f"{args.stage}-manifest.json"
    if manifest_path.exists():
        parser.error("Evidence already exists; select a fresh run directory.")
    manifest = {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "stage": args.stage,
        "revision": candidate.get("revision")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steps": [],
        "status": "RUNNING",
        "candidate": candidate,
        "password_hashing": (
            "fast test-only" if args.fast_password_hashers else "configured defaults"
        ),
        "database_mode": (
            "none"
            if args.stage == "frontend"
            else (
                "recreated for final verification"
                if args.stage == "release" or args.clean_db
                else "reuse"
            )
        ),
    }

    def persist():
        atomic_json(manifest_path, manifest)

    def interrupted_exit():
        if manifest["status"] == "RUNNING":
            manifest.update(status="ABORTED", reason="Process exited before completing this stage.")
            persist()

    atexit.register(interrupted_exit)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)

    def step(name, command, cwd=ROOT):
        log = artifacts / f"{name}.log"
        result = {
            "name": name,
            "command": command,
            "cwd": str(cwd),
            "log": str(log),
            "status": "RUNNING",
        }
        manifest["steps"].append(result)
        if any(row["status"] != "PASS" for row in manifest["steps"][:-1]):
            result.update(status="NOT RUN", reason="An earlier required step did not pass.")
            persist()
            return
        persist()
        print(f"RUN {name}", flush=True)
        started = time.monotonic()
        with log.open("w") as stream:
            try:
                completed = run_managed(
                    command,
                    cwd=cwd,
                    stdout=stream,
                    record_path=artifacts / f"{name}-process.json",
                    timeout=7200,
                    docker_cleanup=name in {"functional", "migrations"},
                    env={
                        **os.environ,
                        "WMS_TEST_MANIFEST": str(artifacts / f"{name}-tests.json"),
                        "WMS_BROWSER_ARTIFACTS": str(artifacts / "browser"),
                        "COVERAGE_FILE": str(artifacts / f".coverage.{name}"),
                    },
                )
                result.update(completed)
                if "pytest" in command and result["status"] == "PASS":
                    evidence = json.loads((artifacts / f"{name}-tests.json").read_text())
                    accepted = {"COLLECTED"} if "--collect-only" in command else {"PASS"}
                    if evidence["status"] not in accepted or evidence["exit_code"] != 0:
                        result.update(
                            status="FAIL", reason="Test evidence does not confirm success."
                        )
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                result.update(status="BLOCKED", error=type(exc).__name__)
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        persist()
        print(f"{result['status']} {name} ({result['duration_seconds']}s)", flush=True)

    persist()
    # Workspace exclusion also covers frontend builds and browser readers.
    workspace_key = hashlib.sha256(str(ROOT).encode()).hexdigest()[:24]
    locks = ExitStack()
    try:
        locks.enter_context(
            secure_file_lock(secure_directory() / f"workspace-{workspace_key}.lock")
        )
    except (OSError, RuntimeError) as exc:
        manifest.update(status="BLOCKED", reason=type(exc).__name__)
        persist()
        return 2
    if args.stage != "frontend":
        try:
            manifest["database"] = preflight(
                schema=args.stage not in ("release", "collect") and not args.clean_db
            )
        except Exception as exc:
            manifest.update(
                status="BLOCKED", reason=f"Database preflight: {type(exc).__name__}: {exc}"
            )
            persist()
            locks.close()
            return 2

    python = sys.executable
    pytest = [python, "-m", "pytest", "--reuse-db", "-q", "--durations=20"]
    if args.fast_password_hashers:
        pytest.append("--fast-password-hashers")
    if args.stage == "collect":
        step("collection", pytest + ["--collect-only", "allapp"])
    elif args.stage == "targeted":
        step(
            "critical-acceptance",
            pytest
            + [
                "allapp/core/test_database_safety.py",
                "allapp/core/test_security_acceptance.py",
                "allapp/pos/test_cross_flow_acceptance.py",
                "allapp/pos/test_repayment_concurrency.py",
                f"--junitxml={artifacts / 'critical-acceptance.xml'}",
            ],
        )
    elif args.stage in ("functional", "migrations", "backend", "release"):
        groups = (
            ("functional", "migrations") if args.stage in {"backend", "release"} else (args.stage,)
        )
        for group in groups:
            step(
                group,
                pytest
                + (
                    ["--create-db"]
                    if (args.stage == "release" or args.clean_db) and group == "functional"
                    else []
                )
                + [
                    "allapp",
                    f"--wms-group={group}",
                    "--cov=allapp",
                    "--cov-report=",
                    "--cov-fail-under=0",
                    f"--junitxml={artifacts / (group + '.xml')}",
                ],
            )
        if len(groups) == 2:
            step(
                "coverage-combine",
                [
                    python,
                    "-m",
                    "coverage",
                    "combine",
                    "--keep",
                    "--data-file",
                    str(artifacts / ".coverage"),
                    str(artifacts),
                ],
            )
            step(
                "coverage-gate",
                [
                    python,
                    "-m",
                    "coverage",
                    "report",
                    "--data-file",
                    str(artifacts / ".coverage"),
                    "--fail-under=72.38",
                ],
            )
            step(
                "coverage-json",
                [
                    python,
                    "-m",
                    "coverage",
                    "json",
                    "--data-file",
                    str(artifacts / ".coverage"),
                    "-o",
                    str(artifacts / "coverage.json"),
                ],
            )
            step(
                "business-coverage-gate",
                [
                    python,
                    str(ROOT / "scripts/check_coverage_gate.py"),
                    str(artifacts / "coverage.json"),
                ],
            )
    if args.stage in ("frontend", "release"):
        for app in (
            "wmspda",
            "wmsandroid",
            "wmsownersale",
            "wmsbossbilling",
            "sales-miniapp",
        ):
            scripts = json.loads((ROOT / app / "package.json").read_text())["scripts"]
            for name in (
                "test:contracts",
                "test:structure",
                "test:unit",
                "test:payment",
                "test:auth",
                "test:cart",
                "build:h5",
                "build:app",
                "test:e2e",
            ):
                if name in scripts:
                    step(
                        f"{app}-{name.replace(':', '-')}",
                        ["npm", "run", name],
                        cwd=ROOT / app,
                    )
    if args.stage in ("browser", "release"):
        step(
            "real-browser",
            pytest
            + [
                "scripts/test_real_browser_acceptance.py",
                f"--junitxml={artifacts / 'real-browser.xml'}",
            ],
        )
    failures = [row for row in manifest["steps"] if row["status"] != "PASS"]
    manifest["status"] = (
        "ABORTED"
        if any(row["status"] == "ABORTED" for row in failures)
        else "FAIL" if failures else "PASS"
    )
    manifest["scope_note"] = (
        "This stage is not a production-readiness certificate; "
        "external device/payment/recovery gates are separate."
    )
    persist()
    locks.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
