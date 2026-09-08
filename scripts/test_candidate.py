"""Identify acceptance inputs without treating new evidence as a new candidate."""

import hashlib
import json
import subprocess
from pathlib import Path


def verify_candidate(root, manifest):
    """Verify a frozen source manifest in a runtime image without Git metadata."""
    root = Path(root).resolve()
    expected = manifest["files"]
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    if not expected or digest != manifest["source_digest"]:
        raise ValueError("Invalid candidate manifest digest")
    for name, sha in expected.items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Unsafe candidate input path")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError(f"Candidate source mismatch: {name}")
    return manifest


def candidate_identity(root):
    """Fingerprint tracked and new source inputs, excluding secrets and evidence."""
    root = Path(root)
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root
        )
        .decode()
        .split("\0")
    )
    files = {}
    for name in sorted(set(paths)):
        if name == "docs/remediation-execution-2026-09-06.md":
            continue
        if not name or name.startswith(("var/", "backups/", "media/", ".venv", ".git/")):
            continue
        path = root / name
        if not path.is_file():
            continue
        if path.name.startswith(".env") and not path.name.endswith(("example", "sample")):
            continue
        if path.suffix in {".pem", ".key", ".p12", ".jks", ".keystore", ".log"}:
            continue
        if path.is_symlink():
            raise RuntimeError(f"Candidate input symlink requires explicit handling: {name}")
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"schema_version": 1, "source_digest": digest, "files": files}
