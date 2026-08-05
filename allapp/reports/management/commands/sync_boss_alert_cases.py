from django.core.management.base import BaseCommand

from allapp.reports.services_alert_cases import sync_alert_cases


class Command(BaseCommand):
    help = (
        "Materialize current boss data-quality alerts into AlertCase workflow records."
    )

    def handle(self, *args, **options):
        result = sync_alert_cases()
        self.stdout.write(self.style.SUCCESS(str(result)))
