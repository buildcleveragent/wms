import copy
import json
from pathlib import Path

import pytest

from scripts.validate_external_acceptance import capacity_errors, manual_errors, recovery_errors

pytestmark = pytest.mark.unit


@pytest.fixture
def profile():
    return json.loads(Path("docs/external-acceptance-profile.json").read_text())


def measured_capacity(profile):
    """Synthetic validator input, never written as real capacity evidence."""
    rules = profile["capacity"]
    return {
        "dataset": profile["dataset"].copy(),
        "warmup_seconds": 300,
        "deployment_identity": "synthetic-test",
        "resource_limits": {"cpu": 4},
        "runs": [
            {
                "scenario": scenario,
                "users": phase["users"],
                "measured_seconds": phase["seconds"],
                "active_roles": {
                    key: value * phase["users"] // 50
                    for key, value in rules["roles_per_50"].items()
                },
                "planned_requests": 1000,
                "started_requests": 1000,
                "completed_requests": 1000,
                "unsent_requests": 0,
                "timeout_requests": 0,
                "unexpected_failures": 0,
                "read_samples": 500,
                "write_samples": 500,
                "read_p95_ms": 500,
                "write_p95_ms": 1000,
                "inventory_errors": 0,
                "financial_errors": 0,
                "metrics": {name: 0 for name in rules["required_metrics"]},
            }
            for scenario in rules["scenarios"]
            for phase in rules["phases"]
        ],
    }


def test_complete_measured_capacity_contract(profile):
    assert capacity_errors(measured_capacity(profile), profile) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("started_requests", 0),
        ("unsent_requests", 1),
        ("timeout_requests", 20),
        ("measured_seconds", 10),
        ("read_samples", 0),
        ("read_p95_ms", 801),
        ("write_p95_ms", 1501),
        ("inventory_errors", 1),
        ("financial_errors", 1),
        ("active_roles", {}),
        ("metrics", {}),
    ],
)
def test_partial_or_incorrect_capacity_never_passes(profile, field, value):
    data = measured_capacity(profile)
    data["runs"][0][field] = value
    assert capacity_errors(data, profile)


def test_empty_capacity_cannot_be_reported_as_zero_error_success(profile):
    assert capacity_errors({}, profile)


def test_recovery_requires_measured_consistent_database_media_and_business_checks(profile):
    data = {
        "snapshot_at": "2026-09-06T00:00:00Z",
        "incident_at": "2026-09-06T08:00:00Z",
        "restore_started_at": "2026-09-06T08:00:00Z",
        "validated_at": "2026-09-06T08:30:00Z",
        "database_snapshot_id": "synthetic-point",
        "media_snapshot_id": "synthetic-point",
    }
    for key in (
        "database_checksum_verified",
        "media_checksum_verified",
        "migration_head_verified",
        "business_flow_verified",
        "inventory_finance_reconciled",
        "scheduler_resumed_verified",
    ):
        data[key] = True
    assert recovery_errors(data, profile) == []
    for changes in (
        {"media_snapshot_id": "other"},
        {"validated_at": "2026-09-06T10:00:00Z"},
        {"snapshot_at": "2026-09-01T00:00:00Z"},
        {"business_flow_verified": False},
    ):
        invalid = copy.deepcopy(data)
        invalid.update(changes)
        assert recovery_errors(invalid, profile)


def test_manual_summary_count_cannot_replace_individual_signed_results(tmp_path):
    source = tmp_path / "cases.csv"
    source.write_text("id,description\nA,first\nB,second\n")
    assert manual_errors({"passed": 2}, source)
    cases = [
        {"id": key, "status": "PASS", "reviewer": "synthetic", "evidence": "synthetic-log"}
        for key in ("A", "B")
    ]
    assert manual_errors({"cases": cases}, source) == []
    assert manual_errors({"cases": [cases[0], cases[0]]}, source)
    cases[1]["status"] = "NOT_RUN"
    assert manual_errors({"cases": cases}, source)
