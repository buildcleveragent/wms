"""Collect both external backups while the production release holds writers stopped."""

import argparse
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


def create_backup_set(evidence_root, db_script, media_script, *, env=None):
    """Run synchronous hooks and publish a manifest only for a complete recovery point."""
    recovery_point = uuid.uuid4().hex
    directory = Path(evidence_root).resolve() / recovery_point
    directory.mkdir(mode=0o700, parents=True)
    manifest = {
        "recovery_point": recovery_point,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }
    hooks = (
        ("database", db_script, {"database"}),
        ("media", media_script, {"media", "private_arrivals", "private_returns"}),
    )
    print(f"Recording backups for the stopped writers in {directory}", flush=True)
    for kind, hook, required in hooks:
        receipt_path = directory / f"{kind}.json"
        subprocess.run(
            [str(Path(hook).resolve())],
            env={
                **(os.environ if env is None else env),
                "WMS_BACKUP_SET_ID": recovery_point,
                "WMS_BACKUP_RECEIPT": str(receipt_path),
            },
            check=True,
        )
        receipt = json.loads(receipt_path.read_text())
        if (
            not isinstance(receipt, dict)
            or receipt.get("recovery_point") != recovery_point
            or not isinstance(receipt.get("snapshot_id"), str)
            or not receipt["snapshot_id"].strip()
            or not isinstance(receipt.get("includes"), list)
            or not required.issubset(receipt["includes"])
        ):
            raise ValueError(f"Incomplete or mismatched {kind} backup receipt: {receipt_path}")
        manifest[kind] = receipt
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    target = directory / "manifest.json"
    temporary = directory / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("db_script", type=Path)
    parser.add_argument("media_script", type=Path)
    options = parser.parse_args()
    print(create_backup_set(options.evidence_root, options.db_script, options.media_script))
