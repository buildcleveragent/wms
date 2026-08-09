"""Environment file loading helpers for Django settings."""

import os
from collections.abc import Sequence
from pathlib import Path

import environ


def is_test_command(argv: Sequence[str]) -> bool:
    """Return whether *argv* represents a Django or pytest test command."""

    command = " ".join(argv).lower()
    return "pytest" in command or "py.test" in command or "manage.py test" in command


def load_environment(
    base_env: Path,
    local_test_env: Path,
    argv: Sequence[str],
) -> None:
    """Load environment files without overriding explicit process variables.

    Test commands load the ignored local test file first, then use the base
    ``.env`` file only to fill missing values.  ``django-environ`` preserves
    variables already present in ``os.environ``, so exported shell and CI
    values retain the highest priority.
    """

    explicit_test_environment = os.environ.get("APP_ENV", "").strip().lower() == "test"
    if local_test_env.exists() and (
        explicit_test_environment or is_test_command(argv)
    ):
        environ.Env.read_env(local_test_env, overwrite=False)
    environ.Env.read_env(base_env, overwrite=False)
