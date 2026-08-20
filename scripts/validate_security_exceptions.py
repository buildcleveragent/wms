#!/usr/bin/env python3
"""Validate that vulnerability exceptions are complete and time bounded."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import yaml

REQUIRED_TEXT_FIELDS = ("id", "package", "owner", "rationale")


def _parse_expiry(raw_value: object, exception_id: str) -> date:
    if isinstance(raw_value, date):
        return raw_value
    if isinstance(raw_value, str):
        try:
            return date.fromisoformat(raw_value)
        except ValueError as exc:
            raise ValueError(f"{exception_id}: expires must use YYYY-MM-DD") from exc
    raise ValueError(f"{exception_id}: expires is required")


def validate(path: Path, *, today: date, max_days: int) -> list[str]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    exceptions = document.get("exceptions")
    if not isinstance(exceptions, list):
        return ["top-level exceptions must be a list"]

    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(exceptions, start=1):
        if not isinstance(entry, dict):
            errors.append(f"entry {index}: must be a mapping")
            continue
        exception_id = str(entry.get("id") or f"entry {index}").strip()
        for field in REQUIRED_TEXT_FIELDS:
            if not str(entry.get(field) or "").strip():
                errors.append(f"{exception_id}: {field} is required")
        if exception_id in seen_ids:
            errors.append(f"{exception_id}: duplicate exception id")
        seen_ids.add(exception_id)

        controls = entry.get("compensating_controls")
        if not isinstance(controls, list) or not any(str(item).strip() for item in controls):
            errors.append(f"{exception_id}: compensating_controls must not be empty")

        try:
            expiry = _parse_expiry(entry.get("expires"), exception_id)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        remaining_days = (expiry - today).days
        if remaining_days < 0:
            errors.append(f"{exception_id}: expired on {expiry.isoformat()}")
        elif remaining_days > max_days:
            errors.append(
                f"{exception_id}: expires in {remaining_days} days, exceeding {max_days}-day limit"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-days", type=int, default=30)
    args = parser.parse_args()
    if args.max_days < 1:
        parser.error("--max-days must be positive")

    errors = validate(args.path, today=date.today(), max_days=args.max_days)
    if errors:
        for error in errors:
            print(f"security exception error: {error}")
        return 1
    print(f"Security exceptions valid: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
