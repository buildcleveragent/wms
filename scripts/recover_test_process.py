#!/usr/bin/env python3
"""Recover only resources belonging to an uncatchably interrupted test run."""

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def recover(path, *, docker_cleanup=True):
    from scripts.test_process import cleanup_containers, group_members
    from wmsmaster.test_coordination import CoordinationBlocked, process_is_alive
    from wmsmaster.test_evidence import atomic_json

    path = Path(path)
    record = json.loads(path.read_text())
    if record["status"] != "RUNNING":
        return record
    if process_is_alive(record["owner"]):
        raise CoordinationBlocked("Execution owner is still alive; recovery refused.")
    record.update(status="ABORTED", reason="OWNER_EXITED_WITHOUT_FINAL_STATE")
    atomic_json(path, record)
    try:
        group = record.get("process_group")
        if not group or not record.get("child"):
            raise CoordinationBlocked(
                "Missing launched process identity; manual verification required."
            )
        for sig in (signal.SIGTERM, signal.SIGKILL):
            members = group_members(group)
            if not members:
                break
            marker = f"WMS_TEST_RUN_ID={record['run_id']}".encode()
            for member in members:
                try:
                    environment = Path(f"/proc/{member['pid']}/environ").read_bytes().split(b"\0")
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if marker not in environment:
                    raise CoordinationBlocked("Process group contains an unverified process.")
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + 3
            while group_members(group) and time.monotonic() < deadline:
                time.sleep(0.05)
        if group_members(group):
            raise CoordinationBlocked("Owned process group did not terminate.")
        record["cleaned_containers"] = (
            cleanup_containers(record["run_id"]) if docker_cleanup else []
        )
        record["cleanup_complete"] = True
        record["database_handover"] = "REQUIRES_DATABASE_LEASE_HEALTH_CHECK"
    except Exception as error:
        record.update(cleanup_complete=False, cleanup_error=type(error).__name__)
    atomic_json(path, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("process_record", type=Path)
    args = parser.parse_args()
    result = recover(args.process_record)
    print(
        json.dumps(
            {key: result.get(key) for key in ("status", "cleanup_complete", "database_handover")}
        )
    )
    return 0 if result.get("cleanup_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
