#!/usr/bin/env python3
"""Run real test-schema migrations while holding the resource execution lease."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import django
    import environ

    from scripts.test_process import run_managed
    from wmsmaster.test_coordination import DatabaseLease
    from wmsmaster.test_database import verify_test_database

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    environ.Env.read_env(ROOT / ".env.test.local", overwrite=False)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings")
    django.setup()
    from django.conf import settings

    args.artifacts.mkdir(parents=True, exist_ok=True)
    with DatabaseLease(settings.DATABASES["default"]):
        verify_test_database(settings.DATABASES["default"])
        with (args.artifacts / "migrate.log").open("x") as stream:
            result = run_managed(
                [
                    sys.executable,
                    str(ROOT / "manage.py"),
                    "migrate",
                    "--noinput",
                    "--verbosity",
                    "2",
                ],
                cwd=ROOT,
                env=os.environ,
                stdout=stream,
                record_path=args.artifacts / "migrate-process.json",
                docker_cleanup=False,
            )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
