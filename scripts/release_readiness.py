#!/usr/bin/env python3
"""Evaluate local completion, release permission and post-release acceptance separately."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("evidence timestamps require a timezone")
    return result


def validate_record(record, *, candidate, policy, root, now):
    """A summary without candidate-matched, intact raw evidence cannot pass."""
    if record.get("candidate_id") != candidate["candidate_id"]:
        return "candidate mismatch"
    if record.get("status") not in {"PASS", "EXPLICIT_TIME_LIMITED_ACCEPTANCE", "CONTROL_VERIFIED"}:
        return record.get("status", "NOT_RUN")
    if record.get("gate_id") != "pdf-resource-release-decision" and record["status"] != "PASS":
        return "this gate requires a passed verification"
    try:
        completed = timestamp(record["finished_at"])
        if completed > now:
            return "future completion timestamp"
        if record.get("expires_at") and timestamp(record["expires_at"]) <= now:
            return "expired evidence"
        if record["gate_id"] in {"image-security", "dependency-audit"}:
            if now - completed > timedelta(hours=policy["security_max_age_hours"]):
                return "security evidence requires a fresh scan"
        if record["gate_id"] in {"image-security", "runtime-smoke", "deployed-artifact-identity"}:
            if (
                not candidate.get("runtime_image_id")
                or record.get("runtime_image_id") != candidate["runtime_image_id"]
            ):
                return "runtime image identity mismatch"
        if record["gate_id"] in {"staging-rehearsal-60m", "production-observation-60m"}:
            if (completed - timestamp(record["started_at"])).total_seconds() < 3600:
                return "observation shorter than 60 minutes"
        artifacts = record.get("artifacts", [])
        if not artifacts:
            return "raw evidence is required"
        for artifact in artifacts:
            path = (root / artifact["path"]).resolve()
            path.relative_to(root.resolve())
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]
            ):
                return "missing or changed raw evidence"
        if record["gate_id"] in {"capacity", "disaster-recovery", "manual-196"}:
            from scripts.validate_external_acceptance import (
                capacity_errors,
                manual_errors,
                recovery_errors,
            )

            repository = Path(__file__).resolve().parents[1]
            profile = json.loads((repository / "docs/external-acceptance-profile.json").read_text())
            measured = json.loads((root / artifacts[0]["path"]).read_text())
            if record["gate_id"] == "manual-196":
                errors = manual_errors(measured, repository / profile["manual_case_source"])
            else:
                validator = capacity_errors if record["gate_id"] == "capacity" else recovery_errors
                errors = validator(measured, profile)
            if errors:
                return "; ".join(errors)
        if record["gate_id"] == "pdf-resource-release-decision":
            if (
                record.get("assessment_role") != "release-security"
                or record.get("decision_role") != "release-manager"
            ):
                return "PDF risk requires security assessment and release-manager decision"
            if not record.get("assessor") or not record.get("decision_by"):
                return "identified PDF risk reviewers required"
            if record["status"] == "EXPLICIT_TIME_LIMITED_ACCEPTANCE":
                if any(not record.get(field) for field in policy["exception_required_fields"]):
                    return "incomplete PDF risk exception"
                if record["risk_id"] != "R-PDF-RESOURCE" or timestamp(record["expires_at"]) <= now:
                    return "invalid or expired PDF resource exception"
            elif record["status"] != "CONTROL_VERIFIED":
                return "PDF resource risk has no explicit disposition"
    except (KeyError, TypeError, ValueError, OSError):
        return "malformed or inaccessible evidence"
    return None


def evaluate(candidate, records, policy, root, *, now=None):
    now = now or datetime.now(timezone.utc)
    grouped = {}
    for record in records:
        grouped.setdefault(record.get("gate_id"), []).append(record)
    result = {"candidate_id": candidate["candidate_id"], "evaluated_at": now.isoformat()}
    phases = {
        "local": policy["local"],
        "pre_release": policy["local"] + policy["pre_release_additional"],
        "post_release": policy["local"]
        + policy["pre_release_additional"]
        + policy["post_release_additional"],
    }
    for phase, required in phases.items():
        gates = []
        for gate_id in required:
            entries = grouped.get(gate_id, [])
            if len(entries) != 1:
                reason = (
                    "duplicate gate evidence: select one current result" if entries else "NOT_RUN"
                )
            else:
                reason = validate_record(
                    entries[0], candidate=candidate, policy=policy, root=root, now=now
                )
            gates.append(
                {"gate_id": gate_id, "status": "BLOCKED" if reason else "PASS", "reason": reason}
            )
        result[phase] = {
            "status": "PASS" if all(row["status"] == "PASS" for row in gates) else "BLOCKED",
            "gates": gates,
        }
    result["local_complete"] = result["local"]["status"] == "PASS"
    result["release_permitted"] = result["pre_release"]["status"] == "PASS"
    result["release_accepted"] = result["post_release"]["status"] == "PASS"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("docs/release-gates.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("use a new output path to preserve earlier readiness evidence")
    report = evaluate(
        json.loads(args.candidate.read_text()),
        json.loads(args.evidence.read_text()),
        json.loads(args.policy.read_text()),
        args.evidence.parent,
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("local_complete", "release_permitted", "release_accepted")
            }
        )
    )
    return 0 if report["local_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
