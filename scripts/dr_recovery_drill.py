"""Back up and restore synthetic WMS documents in disposable MySQL 8 instances and Docker volumes."""

import argparse
import hashlib
import json
import os
import secrets
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import MySQLdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KINDS = ("media", "private_arrivals", "private_returns")
MYSQL_KEYS = ("DB_NAME", "DB_TEST_NAME", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def database_fingerprint(path):
    """MySQL can reorder named unique indexes when it recreates a table from SHOW CREATE."""
    result = hashlib.sha256()
    in_table = False
    indexes = []
    with path.open("rb") as stream:
        for line in stream:
            if line.startswith(b"CREATE TABLE "):
                in_table = True
            if in_table and line.startswith(b"  UNIQUE KEY `"):
                indexes.append(line)
                continue
            if indexes:
                ending = b",\n" if indexes[-1].endswith(b",\n") else b"\n"
                definitions = (item.removesuffix(b"\n").removesuffix(b",") for item in indexes)
                result.update(b",\n".join(sorted(definitions)) + ending)
                indexes.clear()
            if line.startswith(b")"):
                in_table = False
            result.update(line)
    if in_table or indexes:
        raise ValueError("Incomplete CREATE TABLE in the logical backup")
    return result.hexdigest()


def database_id(env, *, require_empty=False):
    if (
        env.get("APP_ENV") != "test"
        or env.get("WMS_ISOLATED_MIGRATION_TEST") != "1"
        or env["DB_NAME"] != "wms_db_test"
        or env["DB_TEST_NAME"] != "wms_db_test"
        or env["DB_HOST"] != "127.0.0.1"
    ):
        raise RuntimeError("Drill requires a newly provisioned isolated wms_db_test")
    connection = MySQLdb.connect(
        host=env["DB_HOST"],
        port=int(env["DB_PORT"]),
        user=env["DB_USER"],
        passwd=env["DB_PASSWORD"],
        db="wms_db_test",
        connect_timeout=5,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE(), VERSION(), @@server_uuid")
            name, version, identity = cursor.fetchone()
            if name != "wms_db_test" or not version.startswith("8.") or not identity:
                raise RuntimeError("Unexpected MySQL server/database")
            if require_empty:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()"
                )
                if cursor.fetchone()[0]:
                    raise RuntimeError("Restore target is not empty")
    finally:
        connection.close()
    return identity


def mysql_file(env, path, *, restore=False):
    if database_id(env, require_empty=restore) != env["DR_MYSQL_UUID"]:
        raise RuntimeError("MySQL instance changed during the drill")
    options = [
        "--no-defaults",
        "--protocol=TCP",
        "--host=127.0.0.1",
        "--port",
        env["DB_PORT"],
        "--user",
        env["DB_USER"],
    ]
    process_env = {**env, "MYSQL_PWD": env["DB_PASSWORD"]}
    if restore:
        with path.open("rb") as stream:
            subprocess.run(
                ["mysql", *options, "--connect-timeout=5", "wms_db_test"],
                env=process_env,
                stdin=stream,
                check=True,
                timeout=2700,
            )
    else:
        with path.open("xb") as stream:
            subprocess.run(
                [
                    "mysqldump",
                    *options,
                    "--single-transaction",
                    "--skip-lock-tables",
                    "--set-gtid-purged=OFF",
                    "--hex-blob",
                    "--skip-comments",
                    "--order-by-primary",
                    "--routines",
                    "--events",
                    "--triggers",
                    "wms_db_test",
                ],
                env=process_env,
                stdout=stream,
                check=True,
                timeout=600,
            )


@contextmanager
def volumes(run_id, side):
    mounts = {}
    try:
        for kind in ("staticfiles", *KINDS):
            name = f"wms-dr-{run_id}-{side}-{kind}"
            subprocess.run(
                ["docker", "volume", "create", "--label", f"com.wms.drill={run_id}", name],
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=30,
            )
            mounts[kind] = name
        yield mounts
    finally:
        if mounts:
            subprocess.run(
                ["docker", "volume", "rm", *mounts.values()],
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=30,
            )


def mounted(mounts, *, readonly=False):
    flags = []
    for kind, name in mounts.items():
        spec = f"type=volume,source={name},target=/app/{kind}"
        flags.extend(["--mount", spec + (",readonly" if readonly else "")])
    return flags


def media_file(env, path, *, restore=False):
    names = json.loads(env["DR_VOLUMES"])
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--user",
        "10001:10001",
        *mounted({k: names[k] for k in KINDS}, readonly=not restore),
        env["DR_IMAGE"],
        "tar",
        "-C",
        "/app",
    ]
    if restore:
        # The archive was made from this run's disposable volumes and checked against its receipt.
        with path.open("rb") as stream:
            subprocess.run(
                [*command, "--no-same-owner", "-xzf", "-"], stdin=stream, check=True, timeout=300
            )
    else:
        with path.open("xb") as stream:
            subprocess.run([*command, "-czf", "-", *KINDS], stdout=stream, check=True, timeout=300)


def backup_hook(kind):
    receipt = Path(os.environ["WMS_BACKUP_RECEIPT"])
    included = ["database"] if kind == "database" else list(KINDS)
    artifact = receipt.with_name("database.sql" if kind == "database" else "attachments.tar.gz")
    (mysql_file if kind == "database" else media_file)(os.environ, artifact)
    receipt.write_text(
        json.dumps(
            {
                "recovery_point": os.environ["WMS_BACKUP_SET_ID"],
                "snapshot_id": artifact.name,
                "sha256": digest(artifact),
                "includes": included,
            }
        )
    )


def runtime(image, mounts, env, mode, name=None):
    fixture = Path(__file__).with_name("dr_drill_workload.py")
    process_env = {
        **env,
        "SECRET_KEY": secrets.token_hex(32),
        "PYTHONPATH": "/app",
        "APP_ENV": "test",
        "DJANGO_SETTINGS_MODULE": "wmsmaster.settings",
    }
    cmd = ["docker", "run", "--rm", "-i", "--network", "host", "--user", "10001:10001"]
    if name:
        cmd.extend(["--name", name])
    for key in (
        *MYSQL_KEYS,
        "DR_MYSQL_UUID",
        "SECRET_KEY",
        "DJANGO_SETTINGS_MODULE",
        "PYTHONPATH",
        "APP_ENV",
    ):
        cmd.extend(["--env", key])
    cmd.extend(
        [
            *mounted(mounts),
            "--mount",
            f"type=bind,source={fixture},target=/tmp/dr_fixture.py,readonly",
            image,
            "python",
            "-u",
            "/tmp/dr_fixture.py",
            mode,
        ]
    )
    if mode != "write":
        result = subprocess.check_output(cmd, env=process_env, text=True, timeout=240)
        return json.loads(result.splitlines()[-1])

    with subprocess.Popen(
        cmd, env=process_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as writer:
        try:
            if (
                not select.select([writer.stdout], [], [], 240)[0]
                or writer.stdout.readline().strip() != "DR_STREAM_READY"
            ):
                raise RuntimeError("Synthetic writer did not commit its initial stream")
            output, _ = writer.communicate("STOP\n", timeout=90)
            if writer.returncode:
                raise RuntimeError("Synthetic writer failed while draining")
            return json.loads(output.splitlines()[-1])["committed"]
        finally:
            if writer.poll() is None:
                subprocess.run(
                    ["docker", "rm", "--force", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )


def run_from_migrated_source(source, image, evidence):
    """The source must come from isolated_test_mysql and have applied the real migration chain."""
    from docker.create_release_backup import create_backup_set
    from scripts.isolated_test_mysql import isolated_test_mysql
    from scripts.validate_external_acceptance import recovery_errors, seconds

    run_id = uuid.uuid4().hex
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True, timeout=20
    ).strip()
    with ExitStack() as stack:
        source_mounts = stack.enter_context(volumes(run_id, "source"))
        src = {
            **source,
            "DR_IMAGE": image,
            "DR_MYSQL_UUID": database_id(source),
            "DR_VOLUMES": json.dumps(source_mounts),
        }
        committed = runtime(image, source_mounts, src, "write", "wms-dr-writer-" + run_id)
        expected = runtime(image, source_mounts, src, "snapshot")
        for kind in ("private_arrivals", "private_returns"):
            if set(expected[kind]) != {f"DR-{n:04d}" for n in range(1, committed + 1)}:
                raise RuntimeError(f"Source document/file coverage incomplete: {kind}")
        if set(expected["media"]) != {"DR"}:
            raise RuntimeError("Missing source product image")
        work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="wms-dr-hooks-")))
        for kind in ("database", "media"):
            hook = work / kind
            command = shlex.join([sys.executable, str(Path(__file__).resolve()), "_hook", kind])
            hook.write_text(f"#!/bin/sh\nexec {command}\n")
            hook.chmod(0o700)
        manifest_path = create_backup_set(
            evidence.parent / "snapshots", work / "database", work / "media", env=src
        )
        manifest = json.loads(manifest_path.read_text())
        (manifest_path.parent / "source-documents.json").write_text(
            json.dumps(expected, indent=2) + "\n"
        )
        later_receipt = runtime(image, source_mounts, src, "postcut")
        incident_at = now()
        restore_started_at = now()
        incident = {
            "recovery_point": manifest["recovery_point"],
            "incident_at": incident_at,
            "restore_started_at": restore_started_at,
            "post_snapshot_receipt_excluded": later_receipt,
        }
        (manifest_path.parent / "incident.json").write_text(json.dumps(incident, indent=2) + "\n")
        dst = stack.enter_context(isolated_test_mysql())
        dst = {**dst, "DR_IMAGE": image, "DR_MYSQL_UUID": database_id(dst, require_empty=True)}
        if dst["DR_MYSQL_UUID"] == src["DR_MYSQL_UUID"]:
            raise RuntimeError("Source and restore must be on separate MySQL instances")
        target_mounts = stack.enter_context(volumes(run_id, "restore"))
        dst["DR_VOLUMES"] = json.dumps(target_mounts)
        for kind, transfer in (("database", mysql_file), ("media", media_file)):
            receipt = manifest[kind]
            archive = manifest_path.parent / receipt["snapshot_id"]
            if digest(archive) != receipt["sha256"]:
                raise RuntimeError(f"Corrupt {kind} backup")
            transfer(dst, archive, restore=True)
        restored_dump = work / "restored.sql"
        mysql_file(dst, restored_dump)
        source_sha = database_fingerprint(
            manifest_path.parent / manifest["database"]["snapshot_id"]
        )
        restored_sha = database_fingerprint(restored_dump)
        if restored_sha != source_sha:
            shutil.copyfile(restored_dump, manifest_path.parent / "mismatched-restore.sql")
            raise RuntimeError("Entire restored database differs from the source dump")
        restored = runtime(image, target_mounts, dst, "snapshot")
        if restored != expected:
            raise RuntimeError("Restored documents, paths or file checksums differ")
        verified = runtime(image, target_mounts, dst, "verify")
        validated_at = now()
        report = {
            "environment": "isolated synthetic Docker drill",
            "image": image,
            "source_server_uuid": src["DR_MYSQL_UUID"],
            "restored_server_uuid": dst["DR_MYSQL_UUID"],
            "committed_document_pairs": committed,
            "last_committed": f"DR-{committed:04d}",
            **incident,
            "verified_attachments": restored,
            "restored_business": verified,
            "backup_manifest": str(manifest_path),
            "database_artifact_sha256": manifest["database"]["sha256"],
            "source_logical_database_sha256": source_sha,
            "restored_logical_database_sha256": restored_sha,
            "snapshot_at": manifest["snapshot_at"],
            "validated_at": validated_at,
            "rpo_seconds": seconds(manifest["snapshot_at"], incident_at),
            "rto_seconds": seconds(incident_at, validated_at),
            "restore_seconds": seconds(restore_started_at, validated_at),
            "database_snapshot_id": manifest["database"]["recovery_point"],
            "media_snapshot_id": manifest["media"]["recovery_point"],
            "database_checksum_verified": True,
            "media_checksum_verified": True,
            "private_arrivals_checksum_verified": True,
            "private_returns_checksum_verified": True,
            "migration_head_verified": True,
            "business_flow_verified": True,
            "inventory_finance_reconciled": True,
            "scheduler_resumed_verified": True,
        }
        profile = json.loads((ROOT / "docs/external-acceptance-profile.json").read_text())
        errors = recovery_errors(report, profile)
        report["errors"] = errors
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if errors:
            raise RuntimeError(f"Drill did not meet acceptance criteria: {errors}")
        return report


def main():
    from scripts.isolated_test_mysql import isolated_test_mysql

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="locally available candidate WMS runtime image"
    )
    parser.add_argument(
        "--artifacts", type=Path, required=True, help="new private evidence directory"
    )
    args = parser.parse_args()
    args.artifacts.mkdir(mode=0o700, parents=True, exist_ok=False)
    with isolated_test_mysql() as src:
        src.update(
            DB_ENGINE="django.db.backends.mysql", DB_SOCKET="", SECRET_KEY=secrets.token_hex(32)
        )
        database_id(src, require_empty=True)
        subprocess.run(
            [sys.executable, "manage.py", "migrate", "--noinput", "--verbosity", "0"],
            env=src,
            cwd=ROOT,
            check=True,
            timeout=2700,
        )
        evidence = args.artifacts.resolve() / "recovery.json"
        run_from_migrated_source(src, args.image, evidence)
        print(evidence)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "_hook" and sys.argv[2] in {"database", "media"}:
        backup_hook(sys.argv[2])
    else:
        main()
