"""Run the owner archive worker alongside the bound Django development server."""

import logging
import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from threading import Event, Thread

from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import Command as StaticRunserver

logger = logging.getLogger(__name__)


def supervise_archives(command, parent_pipe, stopping):
    while not stopping.is_set():
        try:
            result = subprocess.run(command, stdin=parent_pipe, start_new_session=True, check=False)
            if not stopping.is_set():
                logger.warning("货主导出进程退出（%s），3 秒后重启。", result.returncode)
        except OSError:
            logger.exception("货主导出进程启动失败，3 秒后重试。")
        stopping.wait(3)


@contextmanager
def archive_worker(pythonpath=None):
    command = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "run_owner_archives",
        "--watch-runserver",
    ]
    if pythonpath:
        command.extend(["--pythonpath", pythonpath])
    # Only this server owns the writing end. EOF also reaches the child when
    # the OS kills the server without running Python's shutdown handlers.
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb") as lifetime:
        stopping = Event()
        monitor = Thread(
            target=supervise_archives,
            args=(command, reader, stopping),
            name="owner-archive-supervisor",
            daemon=True,
        )
        monitor.start()
        try:
            yield
        finally:
            stopping.set()
            lifetime.close()
            monitor.join(timeout=10)


class Command(StaticRunserver):
    def run(self, **options):
        self.worker_pythonpath = options.get("pythonpath")
        with ExitStack() as self.services:
            return super().run(**options)

    def on_bind(self, server_port):
        super().on_bind(server_port)
        # The autoreloader parent and a server that couldn't bind never get here.
        if settings.IS_DEVELOPMENT:
            self.services.enter_context(archive_worker(self.worker_pythonpath))
