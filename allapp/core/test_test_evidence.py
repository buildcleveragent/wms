"""Fault injection for the report used to decide whether gates passed."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from wmsmaster.test_evidence import TestEvidence

ROOT = Path(__file__).resolve().parents[2]


def report(recorder, phase, outcome, *, subtest=False, node="case"):
    recorder.report(
        SimpleNamespace(
            nodeid=node,
            when=phase,
            outcome=outcome,
            context=SimpleNamespace(kwargs={"password": "never-export-this"}) if subtest else None,
        )
    )


@pytest.mark.unit
def test_failed_subtest_survives_all_later_success_events(tmp_path):
    evidence = TestEvidence(tmp_path / "run.json")
    evidence.collection(["case"])
    report(evidence, "setup", "passed")
    report(evidence, "call", "failed", subtest=True)
    report(evidence, "call", "passed", subtest=True)
    report(evidence, "call", "passed")
    report(evidence, "teardown", "passed")
    result = evidence.finish(0)
    assert result["status"] == "FAIL"
    assert result["tests"]["case"]["status"] == "FAIL"
    assert result["subtest_counts"] == {"failed": 1, "passed": 1}
    events = [json.loads(line) for line in evidence.events_path.read_text().splitlines()]
    assert len({event["sequence"] for event in events}) == len(events)
    assert "never-export-this" not in evidence.events_path.read_text()
    assert {e["subtest_id"] for e in events if e.get("subtest_id")} == {"1:1", "1:2"}


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_failed_phase_cannot_be_overwritten_or_hidden_by_exit_zero(phase):
    evidence = TestEvidence()
    evidence.collection(["case"])
    report(evidence, phase, "failed")
    for stage in ("setup", "call", "teardown"):
        report(evidence, stage, "passed")
    assert evidence.finish(0)["status"] == "FAIL"


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 2, 3, 4, 5])
def test_successful_events_do_not_override_nonzero_exit(exit_code):
    evidence = TestEvidence()
    evidence.collection(["case"])
    for phase in ("setup", "call", "teardown"):
        report(evidence, phase, "passed")
    assert evidence.finish(exit_code)["status"] != "PASS"


@pytest.mark.unit
def test_zero_incomplete_and_unexpected_execution_are_not_success():
    evidence = TestEvidence()
    assert evidence.finish(0)["status"] == "FAIL"
    evidence.collection(["case", "not_executed"])
    for phase in ("setup", "call", "teardown"):
        report(evidence, phase, "passed")
    assert evidence.finish(0)["status"] == "FAIL"
    evidence.collection(["case"])
    report(evidence, "call", "passed", node="unexpected")
    assert evidence.finish(0)["unexpected_nodes"] == ["unexpected"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "nodes,exit_code,status",
    [(["case", "second"], 0, "COLLECTED"), ([], 0, "FAIL"), (["case"], 2, "ABORTED")],
)
def test_collection_only_is_distinct_from_execution(nodes, exit_code, status):
    evidence = TestEvidence()
    evidence.collection(nodes)
    result = evidence.finish(exit_code, collect_only=True)
    assert result["status"] == status
    assert all(row["status"] == "NOT RUN" for row in result["tests"].values())
    assert not any(event["kind"] == "report" for event in evidence.events)


@pytest.mark.unit
def test_new_run_can_pass_without_overwriting_previous_failure(tmp_path):
    old = TestEvidence(tmp_path / "old.json")
    old.collection(["case"])
    report(old, "call", "failed")
    old.finish(1)
    original = old.destination.read_bytes()
    new = TestEvidence(tmp_path / "new.json")
    new.collection(["case"])
    for phase in ("setup", "call", "teardown"):
        report(new, phase, "passed")
    assert new.finish(0)["status"] == "PASS"
    assert old.run_id != new.run_id
    assert old.destination.read_bytes() == original
    with pytest.raises(RuntimeError, match="exists"):
        TestEvidence(old.destination)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "expected_status", "expected_exit"),
    [
        (
            "def test_example(subtests):\n"
            "    for value in (False, True):\n"
            "        with subtests.test(value=value):\n"
            "            assert value\n",
            "FAIL",
            1,
        ),
        (
            "import pytest\n@pytest.fixture\ndef broken():\n"
            "    yield\n    assert False\ndef test_example(broken):\n    pass\n",
            "FAIL",
            1,
        ),
        ("def test_example():\n    raise KeyboardInterrupt\n", "ABORTED", 2),
        ("def test_example():\n    pass\n", "PASS", 0),
        ("# no tests\n", "FAIL", 5),
    ],
)
def test_actual_pytest_hooks_preserve_failure_and_subtest_evidence(
    tmp_path, body, expected_status, expected_exit
):
    # Use the real repository hooks, in a fresh process; no database fixture is requested.
    (tmp_path / "conftest.py").write_bytes((ROOT / "conftest.py").read_bytes())
    source = tmp_path / "test_fault.py"
    source.write_text(body)
    destination = tmp_path / "result.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "WMS_TEST_MANIFEST": str(destination),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pytest.ini"),
            "--confcutdir",
            str(tmp_path),
            str(source),
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert completed.returncode == expected_exit, completed.stdout + completed.stderr
    summary = json.loads(destination.read_text())
    assert summary["status"] == expected_status
    if "subtests" in body:
        assert summary["subtest_counts"] == {"failed": 1, "passed": 1}
        assert summary["tests"][next(iter(summary["tests"]))]["status"] == "FAIL"
