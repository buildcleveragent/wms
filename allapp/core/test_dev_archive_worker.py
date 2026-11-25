from contextlib import ExitStack, nullcontext
from unittest.mock import patch

import pytest
from django.core.management import get_commands

from .management.commands import runserver


@pytest.mark.parametrize("development", [True, False])
def test_only_bound_development_server_starts_worker(settings, development):
    settings.IS_DEVELOPMENT = development
    assert get_commands()["runserver"] == "allapp.core"
    server = runserver.Command()
    server.worker_pythonpath = "/custom-project"
    with ExitStack() as server.services, patch.object(runserver.StaticRunserver, "on_bind"):
        with patch.object(runserver, "archive_worker", return_value=nullcontext()) as worker:
            server.on_bind(8000)
            assert worker.call_count == int(development)
            if development:
                worker.assert_called_once_with("/custom-project")


def test_supervisor_restarts_crashed_worker_and_closes_lifetime_pipe(tmp_path, settings, caplog):
    from time import monotonic, sleep

    settings.BASE_DIR = tmp_path
    marker = tmp_path / "worker"
    (tmp_path / "manage.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:] == ['run_owner_archives', '--watch-runserver']\n"
        "p = Path(__file__).with_name('worker')\n"
        "if not p.exists():\n"
        "    p.write_text('first')\n"
        "    sys.exit(9)\n"
        "p.write_text('restarted')\n"
        "sys.stdin.buffer.read()\n"
        "p.write_text('stopped')\n"
    )
    with runserver.archive_worker():
        deadline = monotonic() + 12
        while not marker.exists() or marker.read_text() != "restarted":
            assert monotonic() < deadline, "the crashed worker was not restarted"
            sleep(0.05)
    assert "9" in caplog.text
    assert marker.read_text() == "stopped"
