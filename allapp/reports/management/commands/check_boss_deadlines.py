from django.core.management.base import BaseCommand

from allapp.reports.boss_automation import run_scheduled_check


class Command(BaseCommand):
    help = "Run due Boss checks respecting global settings; --force runs one manual check."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="Run once even if disabled/not due."
        )

    def handle(self, *args, **options):
        self.stdout.write(str(run_scheduled_check(force=options["force"])))
