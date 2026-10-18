#!/usr/bin/env python3
"""Reproduce locks in isolation with a bounded, pinned resolver invocation."""

import argparse
import importlib.metadata
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.test_process import run_managed  # noqa: E402

LOCKS = ("requirements", "prod", "dev", "test")
NETWORK_ERRORS = ("connectionerror", "connection reset", "read timed out", "connecttimeout")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("Lock reproduction requires Python 3.12")
    for package, version in (("pip", "26.2.1"), ("pip-tools", "7.6.1")):
        if importlib.metadata.version(package) != version:
            parser.error(f"Lock reproduction requires {package}=={version}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = {
        **os.environ,
        "PIP_INDEX_URL": "https://pypi.org/simple",
        "PIP_EXTRA_INDEX_URL": "",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_TOOLS_CACHE_DIR": os.environ.get("PIP_TOOLS_CACHE_DIR", str(output / "cache")),
        "CUSTOM_COMPILE_COMMAND": "python3.12 -m piptools compile --generate-hashes --reuse-hashes",
    }
    results = []
    with tempfile.TemporaryDirectory(prefix="wms-locks-") as temporary:
        workspace = Path(temporary)
        shutil.copytree(ROOT / "requirements", workspace / "requirements")
        for name in LOCKS:
            lock = workspace / "requirements" / f"{name}.txt"
            original = (ROOT / "requirements" / f"{name}.txt").read_bytes()
            for attempt in (1, 2):
                lock.write_bytes(original)
                log = output / f"{name}-{attempt}.log"
                with log.open("w") as stream:
                    result = run_managed(
                        [
                            sys.executable,
                            "-m",
                            "piptools",
                            "compile",
                            "--generate-hashes",
                            "--reuse-hashes",
                            "--pip-args=--timeout=30 --retries=1",
                            f"requirements/{name}.in",
                        ],
                        cwd=workspace,
                        env=env,
                        stdout=stream,
                        record_path=output / f"{name}-{attempt}.json",
                        timeout=900,
                        docker_cleanup=False,
                    )
                network_error = any(
                    error in log.read_text(errors="replace").lower() for error in NETWORK_ERRORS
                )
                if result["status"] == "PASS" or not network_error:
                    break
            identical = result["status"] == "PASS" and lock.read_bytes() == original
            row = {
                "lock": name,
                "attempts": attempt,
                "status": "PASS" if identical else result["status"],
                "identical": identical,
            }
            if result["status"] == "PASS" and not identical:
                row["status"] = "FAIL_LOCK_DRIFT"
                shutil.copy2(lock, output / f"{name}.actual.txt")
            results.append(row)
            (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(json.dumps(row), flush=True)
    return 0 if all(row["identical"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
