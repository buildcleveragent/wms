#!/usr/bin/env python3
"""Enforce aggregate and business-entry coverage together, without rounding up."""

import argparse
import json
from pathlib import Path


def check(report, policy):
    limits = policy["minimum_coverage_percent"]
    results = {"allapp": report["totals"]["percent_covered"]}
    for gate, filename in (
        ("relocation_api", "views_relocation.py"),
        ("replenishment_api", "views_replenishment.py"),
    ):
        matches = [
            row
            for name, row in report["files"].items()
            if name.replace("\\", "/").endswith("allapp/tasking/" + filename)
        ]
        if len(matches) != 1:
            raise ValueError(f"coverage is missing or ambiguous for {filename}")
        results[gate] = matches[0]["summary"]["percent_covered"]
    return {
        "status": (
            "PASS" if all(results[name] >= value for name, value in limits.items()) else "FAIL"
        ),
        "measured": results,
        "minimum": limits,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage", type=Path)
    parser.add_argument("--policy", type=Path, default=Path("docs/release-gates.json"))
    args = parser.parse_args()
    result = check(json.loads(args.coverage.read_text()), json.loads(args.policy.read_text()))
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
