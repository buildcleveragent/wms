"""Run offline assessment checks using the repository's synthetic fixtures."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from allapp.core.test_external_acceptance import measured_capacity  # noqa: E402
from allapp.core.test_release_orchestration import release  # noqa: E402
from scripts.validate_external_acceptance import capacity_errors, recovery_errors  # noqa: E402


def main():
    profile = json.loads((ROOT / "docs/external-acceptance-profile.json").read_text())
    capacity = measured_capacity(profile)
    control = capacity_errors(capacity, profile)
    for run in capacity["runs"]:
        run["metrics"] = dict.fromkeys(profile["capacity"]["required_metrics"])
    null_metrics = capacity_errors(capacity, profile)
    capacity["runs"][0]["read_p95_ms"] = 801
    invalid_latency = capacity_errors(capacity, profile)

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    build = workflow["jobs"]["build"]
    actions = [step.get("uses") for step in build["steps"] if step.get("uses")]
    with TemporaryDirectory() as folder:
        status, events = release(Path(folder), scenario="observation")
    operations = [
        "backup database",
        "backup media",
        "migrate --noinput",
        "up -d web",
        "up -d scheduler boss-check-worker",
        "systemctl start wms-sale-mini-reconcile.service",
    ]
    recorded_order = [
        [name, next(i for i, item in enumerate(events) if name in item)] for name in operations
    ]
    stop_after_start = max(i for i, row in enumerate(events) if "stop web scheduler" in row)
    recovery = json.loads((Path(__file__).parent.parent / "isolated-recovery.json").read_text())
    print(
        json.dumps(
            {
                "capacity_validator": {
                    "type": "offline synthetic evidence validation, no load generation",
                    "positive_control_errors": control,
                    "null_telemetry_in_all_four_runs_errors": null_metrics,
                    "required_metrics_per_run": len(profile["capacity"]["required_metrics"]),
                    "over_limit_latency_control_errors": invalid_latency,
                },
                "release_observation": {
                    "type": "actual shell with mocked Docker, systemd and backup hooks",
                    "event_indexes": dict(recorded_order),
                    "refreeze_index": stop_after_start,
                    "exit_code": status.returncode,
                    "restore_pre_migration_backup_requested": (
                        "restore the verified database/media backup" in status.stderr
                    ),
                },
                "github_build": {
                    "source": "current workflow static inspection",
                    "explicit_token_permissions": build["permissions"],
                    "provenance_action": next(a for a in actions if "attest-build" in a),
                    "sarif_action": next(a for a in actions if "upload-sarif" in a),
                    "staging_requires": workflow["jobs"]["deploy-staging"]["needs"],
                },
                "previous_recovery_record": {
                    "type": recovery["environment"],
                    "current_validator_errors": recovery_errors(recovery, profile),
                    "rpo_seconds": recovery["rpo_seconds"],
                    "rto_seconds": recovery["rto_seconds"],
                    "committed_document_pairs": recovery["committed_document_pairs"],
                    "source_differs_from_destination": (
                        recovery["source_server_uuid"] != recovery["restored_server_uuid"]
                    ),
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
