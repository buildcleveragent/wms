#!/usr/bin/env python3
"""Enforce a no-new-debt baseline for the repository's four Python linters."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path

import black

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / ".ci" / "lint-baseline.json"
LINT_CONFIG_PATHS = ("pyproject.toml", ".flake8", "setup.cfg", "tox.ini")
FLAKE8_PATTERN = re.compile(
    r"^(?P<path>.*?):(?P<line>\d+):(?P<column>\d+): "
    r"(?P<code>[A-Z]\d+) (?P<message>.*)$"
)
ISORT_PATTERN = re.compile(
    r"ERROR: (?P<path>.+?) Imports are incorrectly sorted and/or formatted\."
)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def tracked_python_files() -> list[str]:
    result = _run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"]
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return sorted(line for line in result.stdout.splitlines() if line)


def sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def toolchain_fingerprint() -> dict[str, object]:
    """Identify lint engines and checked-in configuration that define the rules."""

    configs = {
        path: sha256(path) for path in LINT_CONFIG_PATHS if (ROOT / path).is_file()
    }
    return {
        tool: importlib.metadata.version(tool)
        for tool in ("black", "isort", "ruff", "flake8")
    } | {
        "configs": configs,
    }


def _normalize_path(value: str) -> str:
    path = Path(value.strip())
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix().removeprefix("./")


def collect_findings(files: list[str]) -> dict[str, dict[str, list[str]]]:
    findings = {
        path: {"black": [], "isort": [], "ruff": [], "flake8": []} for path in files
    }

    # Black's CLI always creates a process pool for multiple files, even with
    # ``--workers 1``.  That pool can hang in constrained CI/container
    # environments, so run the same formatter one file at a time in-process.
    for file_path in files:
        path = ROOT / file_path
        try:
            would_reformat = black.format_file_in_place(
                path,
                fast=False,
                mode=black.Mode(is_pyi=path.suffix == ".pyi"),
                write_back=black.WriteBack.CHECK,
            )
        except Exception as exc:
            raise RuntimeError(f"Black failed for {file_path}: {exc}") from exc
        if would_reformat:
            findings[file_path]["black"] = ["would-reformat"]

    isort = _run([sys.executable, "-m", "isort", "--check-only", *files])
    for line in isort.stderr.splitlines():
        match = ISORT_PATTERN.search(line)
        if match:
            path = _normalize_path(match.group("path"))
            if path in findings:
                findings[path]["isort"] = ["incorrect-import-order"]

    ruff = _run([sys.executable, "-m", "ruff", "check", "--output-format=json", *files])
    try:
        ruff_rows = json.loads(ruff.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse Ruff output: {exc}") from exc
    for row in ruff_rows:
        path = _normalize_path(row["filename"])
        if path not in findings:
            continue
        location = row.get("location") or {}
        findings[path]["ruff"].append(
            f"{location.get('row', 0)}:{location.get('column', 0)}:"
            f"{row.get('code') or 'UNKNOWN'}:{row.get('message') or ''}"
        )

    flake8 = _run(
        [
            sys.executable,
            "-m",
            "flake8",
            "--max-line-length=100",
            "--extend-ignore=E203,W503",
            *files,
        ]
    )
    for line in flake8.stdout.splitlines():
        match = FLAKE8_PATTERN.match(line)
        if not match:
            continue
        path = _normalize_path(match.group("path"))
        if path in findings:
            findings[path]["flake8"].append(
                f"{match.group('line')}:{match.group('column')}:"
                f"{match.group('code')}:{match.group('message')}"
            )

    for tool_findings in findings.values():
        for values in tool_findings.values():
            values.sort()
    return findings


def load_hash_manifest(path: Path | None, files: list[str]) -> dict[str, str]:
    if path is None:
        return {file_path: sha256(file_path) for file_path in files}
    hashes: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, file_path = line.split(maxsplit=1)
        hashes[file_path.strip()] = digest
    return hashes


def write_baseline(path: Path, hash_manifest: Path | None) -> int:
    files = tracked_python_files()
    findings = collect_findings(files)
    hashes = load_hash_manifest(hash_manifest, files)
    payload = {
        "version": 1,
        "policy": "Changed and new files must be clean; unchanged debt may only decrease.",
        "toolchain": toolchain_fingerprint(),
        "files": {
            file_path: {
                "sha256": hashes.get(file_path, ""),
                "findings": findings[file_path],
            }
            for file_path in files
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote lint baseline for {len(files)} tracked Python files to {path}")
    return 0


def check_baseline(path: Path) -> int:
    baseline = json.loads(path.read_text(encoding="utf-8"))
    baseline_files = baseline.get("files", {})
    files = tracked_python_files()
    failures: list[str] = []
    expected_toolchain = baseline.get("toolchain")
    current_toolchain = toolchain_fingerprint()
    if expected_toolchain != current_toolchain:
        failures.append(
            "lint tool versions or configuration changed; regenerate and review the baseline"
        )

    current_hashes = {file_path: sha256(file_path) for file_path in files}
    changed_files = [
        file_path
        for file_path in files
        if baseline_files.get(file_path, {}).get("sha256") != current_hashes[file_path]
    ]
    current = collect_findings(changed_files) if changed_files else {}

    for file_path in changed_files:
        for tool, values in current[file_path].items():
            if values:
                failures.append(
                    f"{file_path}: changed/new file has {len(values)} {tool} finding(s)"
                )

    removed = sorted(set(baseline_files) - set(files))
    if removed:
        print(f"Lint debt reduced: {len(removed)} baseline file(s) removed.")
    if failures:
        print("Lint ratchet failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(
        "Lint ratchet passed: "
        f"{len(changed_files)} changed/new Python file(s) are clean; "
        "unchanged debt is hash-pinned."
    )
    return 0


def check_baseline_did_not_grow(path: Path, previous_ref: str) -> int:
    """Reject additions to the versioned debt allowance itself."""

    relative_path = path.resolve().relative_to(ROOT).as_posix()
    previous = _run(["git", "show", f"{previous_ref}:{relative_path}"])
    if previous.returncode:
        print(
            f"No lint baseline exists at {previous_ref}; baseline growth check skipped."
        )
        return 0

    current_payload = json.loads(path.read_text(encoding="utf-8"))
    previous_payload = json.loads(previous.stdout)
    current_files = current_payload.get("files", {})
    previous_files = previous_payload.get("files", {})
    additions: list[str] = []
    for file_path, current in current_files.items():
        previous_findings = previous_files.get(file_path, {}).get("findings", {})
        for tool, values in current.get("findings", {}).items():
            added = set(values) - set(previous_findings.get(tool, []))
            if added:
                additions.append(
                    f"{file_path}: baseline adds {len(added)} {tool} finding(s)"
                )

    if additions:
        print("Lint baseline debt increased:", file=sys.stderr)
        for addition in additions:
            print(f"- {addition}", file=sys.stderr)
        return 1
    print(f"Lint baseline did not grow relative to {previous_ref}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--hash-manifest", type=Path)
    parser.add_argument("--previous-baseline-ref")
    args = parser.parse_args()
    if args.write_baseline:
        return write_baseline(args.baseline, args.hash_manifest)
    result = check_baseline(args.baseline)
    if result or not args.previous_baseline_ref:
        return result
    return check_baseline_did_not_grow(args.baseline, args.previous_baseline_ref)


if __name__ == "__main__":
    raise SystemExit(main())
