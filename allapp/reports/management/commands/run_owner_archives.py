import argparse
import os
import sys
import time
from threading import Thread

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from allapp.reports.owner_archive_jobs import run_one


def exit_with_runserver():
    # The development server keeps stdin open for its entire serving lifetime.
    # Interrupted jobs are already recoverable under run_one's database lock.
    sys.stdin.buffer.read()
    os._exit(0)


class Command(BaseCommand):
    help = "生成货主完整 Excel，并清理已过期的私有导出。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--watch-runserver", action="store_true", help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        if options["watch_runserver"]:
            Thread(target=exit_with_runserver, name="runserver-lifetime", daemon=True).start()
        while True:
            close_old_connections()
            processed = run_one()
            if options["once"]:
                return
            if not processed:
                time.sleep(3)
