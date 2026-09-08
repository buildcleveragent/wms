import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

from scripts.isolated_test_mysql import isolated_test_mysql

pytestmark = pytest.mark.migrations


class ProductIdentifierMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.environ.get("APP_ENV") != "test":
            raise AssertionError("Identifier migration scenarios require APP_ENV=test.")
        if os.environ.get("DB_NAME") != "wms_db_test":
            raise AssertionError("Identifier migration scenarios require the isolated test DB.")

        repository_root = Path(__file__).resolve().parents[2]
        runner = repository_root / "scripts" / "identifier_migration_scenario.py"
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(repository_root), existing_pythonpath) if part
        )
        with isolated_test_mysql() as isolated_env:
            env.update(isolated_env)
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

    def test_0008_to_0011_backfills_soft_deleted_identifier_history(self):
        self.assertTrue(self.result["backfill_verified"])
        self.assertGreaterEqual(self.result["registry_count"], 7)

    def test_preflight_conflict_fails_before_schema_changes(self):
        self.assertTrue(self.result["preflight_verified"])
        self.assertTrue(self.result["retry_verified"])
