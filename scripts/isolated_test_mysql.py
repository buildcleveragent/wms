"""Disposable MySQL instance for migration tests, always named wms_db_test."""

import os
import secrets
import subprocess
import time
import uuid
from contextlib import contextmanager

import MySQLdb

MYSQL_IMAGE = (
    "mysql:8.0.46@sha256:" "7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b"
)


@contextmanager
def isolated_test_mysql():
    """Yield credentials for a newly created container; never reuse an existing one."""
    name = f"wms-test-migration-{uuid.uuid4().hex}"
    env = {
        **os.environ,
        "MYSQL_ROOT_PASSWORD": secrets.token_hex(24),
        "MYSQL_DATABASE": "wms_db_test",
        "MYSQL_ROOT_HOST": "%",
    }
    created = False
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--label",
                "com.wms.purpose=isolated-migration-test",
                "--label",
                f"com.wms.test-run={os.environ.get('WMS_TEST_RUN_ID', uuid.uuid4().hex)}",
                "--publish",
                "127.0.0.1::3306",
                "--env",
                "MYSQL_ROOT_PASSWORD",
                "--env",
                "MYSQL_DATABASE",
                "--env",
                "MYSQL_ROOT_HOST",
                MYSQL_IMAGE,
                "--skip-log-bin",
                "--sync-binlog=0",
                "--innodb-flush-log-at-trx-commit=2",
                "--innodb-doublewrite=OFF",
            ],
            env=env,
            check=True,
            capture_output=True,
            timeout=180,
        )
        created = True
        port = (
            subprocess.check_output(
                ["docker", "port", name, "3306/tcp"],
                text=True,
                timeout=10,
            )
            .strip()
            .rsplit(":", 1)[1]
        )
        child_env = {
            **os.environ,
            "APP_ENV": "test",
            "DB_NAME": "wms_db_test",
            "DB_TEST_NAME": "wms_db_test",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": port,
            "DB_USER": "root",
            "DB_PASSWORD": env["MYSQL_ROOT_PASSWORD"],
            "WMS_ISOLATED_MIGRATION_TEST": "1",
        }
        deadline = time.monotonic() + 180
        while True:
            try:
                connection = MySQLdb.connect(
                    host="127.0.0.1",
                    port=int(port),
                    user="root",
                    passwd=child_env["DB_PASSWORD"],
                    db="wms_db_test",
                    connect_timeout=3,
                )
                with connection.cursor() as cursor:
                    cursor.execute("SELECT DATABASE(), VERSION()")
                    database, version = cursor.fetchone()
                    if database != "wms_db_test" or not version.startswith("8."):
                        raise RuntimeError("Unexpected isolated MySQL identity.")
                connection.close()
                break
            except MySQLdb.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Isolated migration MySQL was not ready within 180s."
                    ) from None
                time.sleep(1)
        yield child_env
    finally:
        if created:
            subprocess.run(
                ["docker", "rm", "--force", "--volumes", name],
                check=True,
                capture_output=True,
                timeout=30,
            )
