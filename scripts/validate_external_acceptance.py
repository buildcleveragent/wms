#!/usr/bin/env python3
"""Check supplied capacity/recovery/manual evidence; never fabricate external execution."""

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path


def seconds(start, finish):
    left = datetime.fromisoformat(start.replace("Z", "+00:00"))
    right = datetime.fromisoformat(finish.replace("Z", "+00:00"))
    if left.tzinfo is None or right.tzinfo is None:
        raise ValueError("timezone required")
    return (right - left).total_seconds()


def nonnegative_measurement(value):
    return (
        type(value) in (int, float) and value >= 0 and (type(value) is int or math.isfinite(value))
    )


def capacity_errors(data, profile):
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("dataset", {}), dict)
        or not isinstance(data.get("runs", []), list)
        or any(not isinstance(row, dict) for row in data.get("runs", []))
    ):
        return ["invalid capacity evidence structure"]
    errors = []
    for name, minimum in profile["dataset"].items():
        value = data.get("dataset", {}).get(name)
        if type(value) is not int or value < minimum:
            errors.append(f"dataset below required {name}")
    rules = profile["capacity"]
    if (
        not nonnegative_measurement(data.get("warmup_seconds"))
        or data["warmup_seconds"] < rules["warmup_seconds"]
    ):
        errors.append("warmup is incomplete")
    if not data.get("deployment_identity") or not data.get("resource_limits"):
        errors.append("deployment and resource identity required")
    for scenario in rules["scenarios"]:
        for phase in rules["phases"]:
            matches = [
                row
                for row in data.get("runs", [])
                if row.get("scenario") == scenario and row.get("users") == phase["users"]
            ]
            if len(matches) != 1:
                errors.append(f"missing/duplicate run: {scenario}/{phase['users']}")
                continue
            row = matches[0]
            invalid_numeric = False
            for name in (
                "planned_requests",
                "started_requests",
                "completed_requests",
                "unsent_requests",
                "timeout_requests",
                "unexpected_failures",
                "read_samples",
                "write_samples",
                "read_p95_ms",
                "write_p95_ms",
                "measured_seconds",
            ):
                value = row.get(name)
                if not nonnegative_measurement(value):
                    invalid_numeric = True
                    errors.append(f"invalid numeric measurement: {name}")
            if invalid_numeric:
                continue
            if row.get("measured_seconds", 0) < phase["seconds"]:
                errors.append("measurement window too short")
            expected_roles = {
                key: value * phase["users"] // 50 for key, value in rules["roles_per_50"].items()
            }
            if row.get("active_roles") != expected_roles:
                errors.append("required concurrent role mix was not observed")
            planned, started, completed = (
                row.get(key, 0)
                for key in ("planned_requests", "started_requests", "completed_requests")
            )
            unsent, timeout, failed = (
                row.get(key, -1)
                for key in ("unsent_requests", "timeout_requests", "unexpected_failures")
            )
            if planned <= 0 or started <= 0 or completed <= 0 or unsent != 0:
                errors.append("empty or unsent workload")
            if started + max(unsent, 0) != planned or completed + max(timeout, 0) != started:
                errors.append("request accounting does not reconcile")
            if (
                failed < 0
                or timeout < 0
                or (failed + timeout) / max(started, 1) >= rules["unexpected_error_rate_less_than"]
            ):
                errors.append("unexpected failure/timeout threshold exceeded")
            if row.get("read_samples", 0) <= 0 or row.get("write_samples", 0) <= 0:
                errors.append("read and write samples are both required")
            if (
                row.get("read_p95_ms", float("inf")) > rules["read_p95_ms"]
                or row.get("write_p95_ms", float("inf")) > rules["write_p95_ms"]
            ):
                errors.append("latency threshold exceeded")
            metrics = row.get("metrics")
            for name in rules["required_metrics"]:
                value = metrics.get(name) if isinstance(metrics, dict) else None
                if not nonnegative_measurement(value):
                    errors.append(f"{scenario}/{phase['users']}: invalid telemetry: {name}")
            if any(
                type(row.get(k)) is not int or row[k] != 0
                for k in ("inventory_errors", "financial_errors")
            ):
                errors.append("business invariants failed or were not checked")
    return errors


def recovery_errors(data, profile):
    errors = []
    try:
        rpo = seconds(data["snapshot_at"], data["incident_at"])
        rto = seconds(data["incident_at"], data["validated_at"])
        if seconds(data["incident_at"], data["restore_started_at"]) < 0:
            errors.append("restore cannot start before the incident")
        if seconds(data["restore_started_at"], data["validated_at"]) <= 0:
            errors.append("validation must follow restore start")
        if not 0 <= rpo <= profile["disaster_recovery"]["rpo_seconds"]:
            errors.append("RPO exceeds 24 hours")
        if not 0 < rto <= profile["disaster_recovery"]["rto_seconds"]:
            errors.append("RTO exceeds 60 minutes or no measured recovery")
    except (AttributeError, KeyError, TypeError, ValueError):
        errors.append("measured recovery timestamps required")
    for key in (
        "database_checksum_verified",
        "media_checksum_verified",
        "private_arrivals_checksum_verified",
        "private_returns_checksum_verified",
        "private_close_checksum_verified",
        "migration_head_verified",
        "business_flow_verified",
        "inventory_finance_reconciled",
        "scheduler_resumed_verified",
    ):
        if data.get(key) is not True:
            errors.append(f"not verified: {key}")
    if not data.get("database_snapshot_id") or data.get("database_snapshot_id") != data.get(
        "media_snapshot_id"
    ):
        errors.append("database/media consistency point mismatch")
    return errors


def manual_errors(data, source):
    with Path(source).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        first = reader.fieldnames[0]
        expected = {row[first] for row in reader}
    rows = data.get("cases", [])
    identities = [row.get("id") for row in rows]
    if len(identities) != len(set(identities)) or set(identities) != expected:
        return ["manual case identities must match the full source list exactly once"]
    return [
        f"manual case incomplete: {row['id']}"
        for row in rows
        if row.get("status") != "PASS" or not row.get("reviewer") or not row.get("evidence")
    ]


def validate_archive(args):
    data, profile = json.loads(args.evidence.read_text()), json.loads(args.profile.read_text())
    errors = (
        manual_errors(data, profile["manual_case_source"])
        if args.kind == "manual"
        else (capacity_errors if args.kind == "capacity" else recovery_errors)(data, profile)
    )
    if args.kind == "capacity" and not errors:
        # Formal CLI acceptance replays the archive, including request ledgers and native checks.
        if __package__:
            from .wms_capacity import build_report, local_artifact
        else:
            from wms_capacity import build_report, local_artifact
        sources = data["sources"]
        rebuilt = build_report(
            local_artifact(args.evidence.parent, sources["observations"]),
            [local_artifact(args.evidence.parent, p) for p in sources["client_dirs"]],
            profile,
        )
        if data != dict(rebuilt, sources=sources):
            errors.append("capacity summary differs from the archived evidence")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("capacity", "recovery", "manual"))
    parser.add_argument("evidence", type=Path)
    parser.add_argument(
        "--profile", type=Path, default=Path("docs/external-acceptance-profile.json")
    )
    args = parser.parse_args()
    try:
        errors = validate_archive(args)
    except (AttributeError, KeyError, TypeError, ValueError, OSError, OverflowError) as exc:
        errors = [f"missing or invalid acceptance evidence: {exc}"]
    print(
        json.dumps(
            {"status": "BLOCKED" if errors else "PASS", "errors": errors}, ensure_ascii=False
        )
    )
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
