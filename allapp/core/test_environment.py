import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from wmsmaster.environment import is_test_command, load_environment


class EnvironmentLoadingTests(SimpleTestCase):
    def _env_files(self, root: Path) -> tuple[Path, Path]:
        base_env = root / ".env"
        local_test_env = root / ".env.test.local"
        base_env.write_text(
            "WMS_ENV_PRIORITY=base\nWMS_BASE_ONLY=base-value\n",
            encoding="utf-8",
        )
        local_test_env.write_text(
            "WMS_ENV_PRIORITY=test\nWMS_TEST_ONLY=test-value\n",
            encoding="utf-8",
        )
        return base_env, local_test_env

    def test_explicit_process_environment_has_highest_priority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_env, local_test_env = self._env_files(Path(tmpdir))
            with patch.dict(
                os.environ,
                {"WMS_ENV_PRIORITY": "explicit"},
                clear=True,
            ):
                load_environment(base_env, local_test_env, ["pytest"])

                self.assertEqual(os.environ["WMS_ENV_PRIORITY"], "explicit")
                self.assertEqual(os.environ["WMS_TEST_ONLY"], "test-value")
                self.assertEqual(os.environ["WMS_BASE_ONLY"], "base-value")

    def test_local_test_environment_overrides_base_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_env, local_test_env = self._env_files(Path(tmpdir))
            with patch.dict(os.environ, {}, clear=True):
                load_environment(base_env, local_test_env, ["python", "-m", "pytest"])

                self.assertEqual(os.environ["WMS_ENV_PRIORITY"], "test")
                self.assertEqual(os.environ["WMS_TEST_ONLY"], "test-value")
                self.assertEqual(os.environ["WMS_BASE_ONLY"], "base-value")

    def test_non_test_command_does_not_load_local_test_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_env, local_test_env = self._env_files(Path(tmpdir))
            with patch.dict(os.environ, {}, clear=True):
                load_environment(
                    base_env,
                    local_test_env,
                    ["python", "manage.py", "runserver"],
                )

                self.assertEqual(os.environ["WMS_ENV_PRIORITY"], "base")
                self.assertNotIn("WMS_TEST_ONLY", os.environ)

    def test_explicit_test_app_environment_loads_local_test_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_env, local_test_env = self._env_files(Path(tmpdir))
            with patch.dict(os.environ, {"APP_ENV": "test"}, clear=True):
                load_environment(
                    base_env,
                    local_test_env,
                    ["python", "manage.py", "validate_sale_mini_data_accuracy"],
                )

                self.assertEqual(os.environ["WMS_ENV_PRIORITY"], "test")
                self.assertEqual(os.environ["WMS_TEST_ONLY"], "test-value")
                self.assertEqual(os.environ["WMS_BASE_ONLY"], "base-value")

    def test_django_test_command_is_detected(self):
        self.assertTrue(is_test_command(["python", "manage.py", "test"]))
        self.assertFalse(is_test_command(["python", "manage.py", "check"]))
