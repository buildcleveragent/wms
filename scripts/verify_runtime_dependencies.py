#!/usr/bin/env python3
"""Ensure adding test tools preserves every installed runtime distribution."""

import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path


def installed():
    result = {}
    for row in importlib.metadata.distributions():
        files = {}
        for relative in row.files or []:
            path = Path(row.locate_file(relative))
            if path.suffix != ".pyc" and path.is_file():
                files[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        result[row.metadata["Name"].lower().replace("_", "-")] = {
            "version": row.version,
            "files": files,
        }
    system = dict(
        line.split("\t", 1)
        for line in subprocess.check_output(
            ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"], text=True
        ).splitlines()
    )
    return {"python": result, "system": system}


def main():
    mode, filename = sys.argv[1:]
    path = Path(filename)
    if mode == "snapshot":
        path.write_text(json.dumps(installed(), sort_keys=True, indent=2))
    elif mode == "verify":
        before, after = json.loads(path.read_text()), installed()
        changed = [
            f"{kind}:{name}"
            for kind, packages in before.items()
            for name, details in packages.items()
            if after[kind].get(name) != details
        ]
        print(
            json.dumps(
                {
                    "status": "FAIL" if changed else "PASS",
                    "runtime_distributions": len(before["python"]),
                    "runtime_system_packages": len(before["system"]),
                    "changed": changed,
                }
            )
        )
        return 1 if changed else 0
    else:
        raise ValueError("expected snapshot or verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
