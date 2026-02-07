# allapp/billing/management/commands/billing_accrue_storage.py
import datetime
from django.core.management.base import BaseCommand, CommandError
from allapp.billing.services import accrue_storage_for_date
from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse

class Command(BaseCommand):
    help = "Accrue storage charge for a given date (defaults to yesterday)."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, help="YYYY-MM-DD, default yesterday")
        parser.add_argument("--owner", type=int, help="owner_id (optional)")
        parser.add_argument("--warehouse", type=int, help="warehouse_id (optional)")

    def handle(self, *args, **opts):
        d = opts.get("date")
        if d:
            service_date = datetime.date.fromisoformat(d)
        else:
            service_date = datetime.date.today() - datetime.timedelta(days=1)

        owners = Owner.objects.all()
        whs    = Warehouse.objects.all()
        if opts.get("owner"):
            owners = owners.filter(id=opts["owner"])
        if opts.get("warehouse"):
            whs = whs.filter(id=opts["warehouse"])

        total_ev = total_acc = 0
        for o in owners:
            for w in whs:
                ev, acc = accrue_storage_for_date(o.id, w.id, service_date)
                total_ev += ev; total_acc += acc
        self.stdout.write(self.style.SUCCESS(f"Storage accrued {total_ev} events, {total_acc} accruals for {service_date}"))
