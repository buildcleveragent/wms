#!/usr/bin/env python3
"""Cross-check immutable historical evidence without inventing missing events."""

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class JUnitTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("JUnit evidence must not contain a document type or entity declarations")


def parse_junit(path):
    # The parser rejects every DTD, including internal/external entity declarations.
    return ET.parse(path, parser=ET.XMLParser(target=JUnitTreeBuilder()))  # nosec B314


def review(directory):
    directory = Path(directory)
    xml = parse_junit(directory / "backend.xml")
    cases = list(xml.iter("testcase"))
    tests = json.loads((directory / "backend-tests.json").read_text())
    run = json.loads((directory / "backend-manifest.json").read_text())
    expected = set()
    for node in tests["tests"]:
        parts = node.split("::")
        classname = parts[0].removesuffix(".py").replace("/", ".")
        if len(parts) > 2:
            classname += "." + ".".join(parts[1:-1])
        expected.add((classname, parts[-1]))
    observed = {(item.get("classname"), item.get("name")) for item in cases}
    counts = {kind: len(list(xml.iter(kind))) for kind in ("failure", "error", "skipped")}
    log = directory / "backend.log"
    log_summary = None
    if log.exists():
        matches = re.findall(
            r"(\d+) passed, (\d+) subtests passed", log.read_text(errors="replace")
        )
        if matches:
            log_summary = {
                "main_passed": int(matches[-1][0]),
                "subtests_passed": int(matches[-1][1]),
            }
    complete = (
        len(cases) == len(observed) == len(expected)
        and observed == expected
        and not any(counts.values())
        and tests["exit_code"] == 0
        and all(item["status"] == "PASS" for item in tests["tests"].values())
        and all(step.get("exit_code") == 0 for step in run["steps"])
    )
    return {
        "status": "CONSISTENT_WITH_REPORTED_MAIN_PASS" if complete else "REVIEW_REQUIRED",
        "testcase_elements": len(cases),
        "suite_reported_tests": [int(suite.get("tests", 0)) for suite in xml.iter("testsuite")],
        "xml_failures": counts,
        "missing_cases": sorted(expected - observed),
        "extra_cases": sorted(observed - expected),
        "log_summary": log_summary,
        "subtest_identity_recoverable": False,
        "limitation": (
            "Historical subtests have aggregate counts only. "
            "A new run cannot recover their original identities."
        ),
        "source_files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                directory / "backend.xml",
                directory / "backend-tests.json",
                directory / "backend-manifest.json",
            )
        },
    }


def main():
    from wmsmaster.test_evidence import atomic_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.previous.resolve()):
        parser.error("Write the review to a new evidence location, never the historical directory.")
    result = review(args.previous)
    atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "CONSISTENT_WITH_REPORTED_MAIN_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
