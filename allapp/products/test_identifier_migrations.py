import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

import MySQLdb

SCHEMA_PATTERN = re.compile(r"^wms_identifier_migration_[a-f0-9_]+$")


def _drop_scenario_schema(schema):
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise AssertionError("Refusing to drop an unexpected migration-test schema.")
    connection = MySQLdb.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"],
        passwd=os.environ["DB_PASSWORD"],
        charset="utf8mb4",
    )
    try:
        connection.autocommit(True)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")  # nosec B608
    finally:
        connection.close()


class ProductIdentifierMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.environ.get("APP_ENV") != "test":
            raise AssertionError("Identifier migration scenarios require APP_ENV=test.")
        if os.environ.get("DB_NAME") != "wms_db_test":
            raise AssertionError("Identifier migration scenarios require the isolated test DB.")

        schema = f"wms_identifier_migration_{uuid.uuid4().hex}"
        repository_root = Path(__file__).resolve().parents[2]
        runner = repository_root / "scripts" / "identifier_migration_scenario.py"
        env = os.environ.copy()
        env["WMS_IDENTIFIER_MIGRATION_SCHEMA"] = schema
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(repository_root), existing_pythonpath) if part
        )
        try:
            completed = subprocess.run(
                [sys.executable, str(runner)],
                cwd=repository_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            if completed.returncode:
                raise AssertionError(
                    "Identifier migration scenario failed.\n"
                    f"stdout:\n{completed.stdout[-4000:]}\n"
                    f"stderr:\n{completed.stderr[-4000:]}"
                )
            cls.result = json.loads(completed.stdout.strip().splitlines()[-1])
        finally:
            _drop_scenario_schema(schema)

    def test_0008_to_0011_backfills_soft_deleted_identifier_history(self):
        self.assertTrue(self.result["backfill_verified"])
        self.assertGreaterEqual(self.result["registry_count"], 7)

    def test_preflight_conflict_fails_before_schema_changes(self):
        self.assertTrue(self.result["preflight_verified"])
        self.assertTrue(self.result["retry_verified"])
