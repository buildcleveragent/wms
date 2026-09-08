import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.release_readiness import evaluate

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)


@pytest.fixture
def evidence(tmp_path):
    policy = json.loads(Path("docs/release-gates.json").read_text())
    # External measurement contracts are independently exercised in their tests.
    policy["pre_release_additional"] = [
        gate
        for gate in policy["pre_release_additional"]
        if gate not in {"capacity", "disaster-recovery", "manual-196"}
    ]
    candidate = {"candidate_id": "candidate-1", "runtime_image_id": "sha256:runtime"}
    raw = tmp_path / "raw.txt"
    raw.write_text("synthetic raw evidence for validator tests")
    records = []
    for gate_id in (
        policy["local"] + policy["pre_release_additional"] + policy["post_release_additional"]
    ):
        records.append(
            {
                "gate_id": gate_id,
                "status": "PASS",
                **candidate,
                "started_at": "2026-09-06T08:00:00Z",
                "finished_at": "2026-09-06T09:00:00Z",
                "artifacts": [
                    {"path": raw.name, "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}
                ],
            }
        )
    pdf = next(row for row in records if row["gate_id"] == "pdf-resource-release-decision")
    pdf.update(
        status="CONTROL_VERIFIED",
        assessment_role="release-security",
        decision_role="release-manager",
        assessor="synthetic assessor",
        decision_by="synthetic release manager",
    )
    return candidate, records, policy, tmp_path


def verdict(evidence):
    return evaluate(*evidence, now=NOW)


def test_pre_release_can_pass_before_any_production_observation(evidence):
    candidate, records, policy, root = evidence
    records[:] = [row for row in records if row["gate_id"] not in policy["post_release_additional"]]
    result = verdict(evidence)
    assert result["local_complete"]
    assert result["release_permitted"]
    assert not result["release_accepted"]


@pytest.mark.parametrize("status", ["FAIL", "RUNNING", "ABORTED", "SKIPPED", "BLOCKED", "NOT_RUN"])
def test_security_gate_never_passes_via_later_observation(evidence, status):
    next(row for row in evidence[1] if row["gate_id"] == "image-security")["status"] = status
    result = verdict(evidence)
    assert not result["local_complete"]
    assert not result["release_permitted"]
    assert not result["release_accepted"]


def test_open_pdf_resource_risk_blocks_release_but_not_completed_local_work(evidence):
    row = next(row for row in evidence[1] if row["gate_id"] == "pdf-resource-release-decision")
    row["status"] = "REVIEW_REQUIRED"
    result = verdict(evidence)
    assert result["local_complete"] and not result["release_permitted"]


@pytest.mark.parametrize(
    "mutation",
    ["candidate", "digest", "missing", "stale", "duplicate", "short-observation", "image"],
)
def test_incomplete_or_mismatched_evidence_is_not_accepted(evidence, mutation):
    records = evidence[1]
    row = next(item for item in records if item["gate_id"] == "image-security")
    if mutation == "candidate":
        row["candidate_id"] = "another-candidate"
    elif mutation == "digest":
        row["artifacts"][0]["sha256"] = "invalid"
    elif mutation == "missing":
        row["artifacts"] = []
    elif mutation == "stale":
        row["finished_at"] = "2026-09-01T09:00:00Z"
    elif mutation == "duplicate":
        records.append(row.copy())
    elif mutation == "image":
        row["runtime_image_id"] = "sha256:other"
    else:
        row = next(item for item in records if item["gate_id"] == "staging-rehearsal-60m")
        row["started_at"] = row["finished_at"]
    assert not verdict(evidence)["release_permitted"]


def test_same_image_fresh_scan_updates_conclusion_without_rebuild(evidence):
    assert verdict(evidence)["release_permitted"]
    row = next(item for item in evidence[1] if item["gate_id"] == "image-security")
    row.update(status="FAIL", finished_at="2026-09-06T09:30:00Z")
    assert not verdict(evidence)["release_permitted"]
    row.update(status="PASS", finished_at="2026-09-06T09:45:00Z")
    assert verdict(evidence)["release_permitted"]
