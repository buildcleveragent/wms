import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.isolated_test_mysql import isolated_test_mysql

pytestmark = pytest.mark.migrations


def test_nonempty_0009_to_0010_preserves_history_and_enforces_new_request_keys():
    root = Path(__file__).resolve().parents[2]
    with isolated_test_mysql() as env:
        result = subprocess.run(
            [sys.executable, str(root / "scripts/pos_repayment_migration_scenario.py")],
            cwd=root,
            env={**env, "PYTHONPATH": str(root)},
            capture_output=True,
            text=True,
            timeout=1800,
        )
        artifacts = os.environ.get("WMS_TEST_MANIFEST")
        if artifacts:
            Path(artifacts).with_suffix(".pos-migration.log").write_text(
                result.stdout + result.stderr
            )
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        assert evidence["history_rows"] == 4
        assert all(value is True for key, value in evidence.items() if key != "history_rows")
