"""Exercise cancellation and cleanup with real, disposable child processes."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.test_process import group_members, run_managed


@pytest.mark.unit
@pytest.mark.parametrize(
    ("code", "timeout", "status", "reason"),
    [
        ("print('done')", 5, "PASS", None),
        ("raise SystemExit(7)", 5, "FAIL", None),
        ("import time; time.sleep(60)", 0.2, "ABORTED", "TIMEOUT"),
        (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
            5,
            "FAIL",
            "LEAKED_RESOURCES",
        ),
        (
            "import os,signal,time; os.kill(os.getppid(),signal.SIGTERM); time.sleep(60)",
            5,
            "ABORTED",
            "INTERRUPTED",
        ),
    ],
)
def test_process_exit_cannot_hide_failure_or_leaked_children(
    tmp_path, code, timeout, status, reason
):
    path = tmp_path / "process.json"
    with (tmp_path / "output.log").open("w") as stream:
        result = run_managed(
            [sys.executable, "-c", code],
            cwd=Path.cwd(),
            env=os.environ,
            stdout=stream,
            record_path=path,
            timeout=timeout,
            docker_cleanup=False,
        )
    assert result["status"] == status
    assert result.get("reason") == reason
    assert result["cleanup_complete"]
    assert not group_members(result["process_group"])
    assert json.loads(path.read_text()) == result


@pytest.mark.unit
def test_cleanup_failure_blocks_an_otherwise_successful_command(tmp_path, monkeypatch):
    def failed_cleanup(run_id):
        raise RuntimeError("Docker unavailable")

    monkeypatch.setattr("scripts.test_process.cleanup_containers", failed_cleanup)
    with (tmp_path / "output.log").open("w") as stream:
        result = run_managed(
            [sys.executable, "-c", "pass"],
            cwd=Path.cwd(),
            env=os.environ,
            stdout=stream,
            record_path=tmp_path / "process.json",
            timeout=5,
        )
    assert result["status"] == "BLOCKED"
    assert not result["cleanup_complete"]


@pytest.mark.unit
def test_existing_evidence_is_not_overwritten(tmp_path):
    path = tmp_path / "process.json"
    path.write_text("old evidence")
    with (tmp_path / "output.log").open("w") as stream:
        with pytest.raises(RuntimeError, match="already exists"):
            run_managed(
                [sys.executable, "-c", "pass"],
                cwd=Path.cwd(),
                env=os.environ,
                stdout=stream,
                record_path=path,
                timeout=5,
                docker_cleanup=False,
            )
    assert path.read_text() == "old evidence"


@pytest.mark.unit
def test_sigkill_is_recovered_as_aborted_not_passed(tmp_path):
    from scripts.recover_test_process import recover

    source = tmp_path / "supervisor.py"
    source.write_text(
        "import os,sys\nfrom pathlib import Path\n"
        "from scripts.test_process import run_managed\n"
        "with open(sys.argv[2], 'w') as stream:\n"
        "    run_managed([sys.executable,'-c','import time; time.sleep(60)'],"
        "cwd=Path.cwd(),env=os.environ,stdout=stream,record_path=Path(sys.argv[1]),"
        "docker_cleanup=False)\n"
    )
    record = tmp_path / "killed.json"
    owner = subprocess.Popen(
        [sys.executable, str(source), str(record), str(tmp_path / "child.log")],
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if record.exists() and json.loads(record.read_text()).get("child"):
                break
            time.sleep(0.05)
        assert record.exists() and json.loads(record.read_text())["child"]
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=3)
        result = recover(record, docker_cleanup=False)
        assert result["status"] == "ABORTED"
        assert result["cleanup_complete"]
        assert not group_members(result["process_group"])
        assert result["database_handover"] == "REQUIRES_DATABASE_LEASE_HEALTH_CHECK"
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)
        if record.exists():
            recover(record, docker_cleanup=False)
