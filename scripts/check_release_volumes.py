#!/usr/bin/env python3
"""Verify a non-root candidate using fresh disposable static/media volumes."""

import argparse
import subprocess
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    identity = uuid.uuid4().hex
    names = [f"wms-release-{kind}-{identity}" for kind in ("static", "media")]
    container = f"wms-release-volume-check-{identity}"
    created = []
    try:
        for name in names:
            subprocess.run(
                ["docker", "volume", "create", "--label", "com.wms.purpose=release-test", name],
                check=True,
                capture_output=True,
                timeout=20,
            )
            created.append(name)
        code = """import os, runpy, secrets
os.environ['SECRET_KEY'] = secrets.token_hex(32)
os.environ['DJANGO_SETTINGS_MODULE'] = 'wmsmaster.settings'
assert os.getuid() == 10001 and os.getgid() == 10001
for path in ('/app/staticfiles', '/app/media'):
    assert os.stat(path).st_uid == 10001
runpy.run_path('/app/docker/check_volume_access.py', run_name='__main__')
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', 'collectstatic', '--noinput'])
print('PASS: collectstatic and media IO with fresh volumes as UID/GID 10001')
"""
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--name",
                container,
                "--user",
                "10001:10001",
                "--mount",
                f"type=volume,source={names[0]},target=/app/staticfiles",
                "--mount",
                f"type=volume,source={names[1]},target=/app/media",
                "-e",
                "APP_ENV=test",
                "-e",
                "DB_NAME=wms_db_test",
                "-e",
                "DB_TEST_NAME=wms_db_test",
                args.image,
                "python",
                "-c",
                code,
            ],
            check=False,
            timeout=120,
        ).returncode
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True, timeout=20, check=False
        )
        for name in created:
            subprocess.run(
                ["docker", "volume", "rm", name], check=True, capture_output=True, timeout=20
            )


if __name__ == "__main__":
    raise SystemExit(main())
