"""Bounded process-group and run-owned container cleanup for test commands."""

import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

from wmsmaster.test_coordination import process_identity
from wmsmaster.test_evidence import atomic_json


def group_members(group):
    """Return live Linux process identities belonging to the launched group."""
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            tail = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(tail[2]) == group and tail[0] != "Z":
                identity = process_identity(int(entry.name))
                if identity:
                    members.append(identity)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return members


def stop_group(process):
    """Stop only the new session created for this command and reap its leader."""
    for sig, seconds in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        if not group_members(process.pid):
            break
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + seconds
        while group_members(process.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    process.wait(timeout=3)
    return not group_members(process.pid)


def cleanup_containers(run_id):
    """Remove only disposable resources explicitly labeled with this run UUID."""
    if uuid.UUID(run_id).hex != run_id:
        raise ValueError("Invalid test resource run identity.")
    command = [
        "docker",
        "ps",
        "-aq",
        "--no-trunc",
        "--filter",
        f"label=com.wms.test-run={run_id}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    containers = result.stdout.split()
    for container in containers:
        if len(container) != 64 or any(c not in "0123456789abcdef" for c in container):
            raise RuntimeError("Invalid owned container identity.")
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", container],
            capture_output=True,
            timeout=30,
            check=True,
        )
    remaining = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    if remaining.stdout.strip():
        raise RuntimeError("Run-owned containers still exist after cleanup.")
    return containers


def run_managed(command, *, cwd, env, stdout, record_path, timeout=7200, docker_cleanup=True):
    """Persist a run identity, execute a command, and reject incomplete cleanup."""
    record_path = Path(record_path)
    if record_path.exists():
        raise RuntimeError("Process evidence already exists; use a new execution directory.")
    run_id = uuid.uuid4().hex
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "RUNNING",
        "owner": process_identity(),
        "command": list(command),
        "cwd": str(cwd),
        "child": None,
        "cleanup_complete": False,
    }
    atomic_json(record_path, record)
    process = None
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env={**env, "WMS_TEST_RUN_ID": run_id},
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        record["child"] = process_identity(process.pid)
        record["process_group"] = process.pid
        atomic_json(record_path, record)
        code = process.wait(timeout=timeout)
        record.update(exit_code=code, status="PASS" if code == 0 else "FAIL")
    except subprocess.TimeoutExpired:
        record.update(status="ABORTED", reason="TIMEOUT")
    except KeyboardInterrupt:
        record.update(status="ABORTED", reason="INTERRUPTED")
    except OSError as error:
        record.update(status="BLOCKED", reason=type(error).__name__)
    finally:
        # Ignore further catchable interrupts only during bounded cleanup.
        old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            residual = bool(process and group_members(process.pid))
            group_clean = process is None or stop_group(process)
            containers = cleanup_containers(run_id) if docker_cleanup else []
            record["cleaned_containers"] = containers
            record["cleanup_complete"] = group_clean
            if record["status"] == "PASS" and (residual or containers or not group_clean):
                record.update(status="FAIL", reason="LEAKED_RESOURCES")
        except Exception as error:
            record.update(status="BLOCKED", cleanup_error=type(error).__name__)
        finally:
            record["duration_seconds"] = round(time.monotonic() - started, 3)
            atomic_json(record_path, record)
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, previous_handler)
    return record
