import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from allapp.reports.boss_automation import heartbeat, run_scheduled_check

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Keep Boss check scheduling alive; read global configuration every 30 seconds."

    def handle(self, *args, **options):
        self.stdout.write("Boss check scheduler started (configuration poll: 30 seconds).")
        try:
            while True:
                close_old_connections()
                try:
                    heartbeat()
                    result = run_scheduled_check()
                    if "skipped" not in result:
                        heartbeat()
                        self.stdout.write(str(result))
                except Exception:
                    logger.exception("Boss check scheduler cycle failed")
                finally:
                    close_old_connections()
                time.sleep(30)
        except KeyboardInterrupt:
            self.stdout.write("Boss check scheduler stopped.")
