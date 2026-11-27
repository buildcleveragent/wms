#!/usr/bin/env python3
"""Verify a non-root candidate using fresh disposable release volumes."""

import argparse
import subprocess
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    identity = uuid.uuid4().hex
    directories = ("staticfiles", "media", "private_returns", "private_arrivals", "private_close", "private_exports")
    names = {kind: f"wms-release-{kind}-{identity}" for kind in directories}
    container = f"wms-release-volume-check-{identity}"
    created = []
    try:
        mounts = []
        for kind, name in names.items():
            subprocess.run(
                ["docker", "volume", "create", "--label", "com.wms.purpose=release-test", name],
                check=True,
                capture_output=True,
                timeout=20,
            )
            created.append(name)
            mounts.extend(["--mount", f"type=volume,source={name},target=/app/{kind}"])
        code = """import os, secrets, stat, subprocess, sys
os.environ['SECRET_KEY'] = secrets.token_hex(32)
os.environ['DJANGO_SETTINGS_MODULE'] = 'wmsmaster.settings'
assert os.getuid() == 10001 and os.getgid() == 10001
for kind in sys.argv[1:]:
    directory = os.stat('/app/' + kind)
    assert (directory.st_uid, directory.st_gid) == (10001, 10001)
    if kind.startswith('private_'):
        assert stat.S_IMODE(directory.st_mode) == 0o700
subprocess.run([sys.executable, '/app/docker/check_volume_access.py'], check=True)
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', 'collectstatic', '--noinput'])
print('PASS: collectstatic and IO on fresh volumes as UID/GID 10001')
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
                *mounts,
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
                *directories,
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
