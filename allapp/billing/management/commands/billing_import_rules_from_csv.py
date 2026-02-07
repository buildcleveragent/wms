# allapp/billing/management/commands/billing_import_rules_from_csv.py
#useage python manage.py billing_import_rules_from_csv /path/to/billing_rules_template.csv
import csv, decimal
from django.core.management.base import BaseCommand
from allapp.billing.models import BillingRule

class Command(BaseCommand):
    help = "Import BillingRule from a CSV file (utf-8-sig)."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str)

    def handle(self, *args, **opts):
        path = opts["csv_file"]
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            created = updated = 0
            for r in reader:
                defaults = dict(
                    warehouse_id = (int(r["warehouse_id"]) if r.get("warehouse_id") else None),
                    unit_price   = decimal.Decimal(r["unit_price"] or "0"),
                    currency     = r.get("currency") or "CNY",
                    taxable      = bool(int(r.get("taxable") or "1")),
                    tax_rate     = decimal.Decimal(r.get("tax_rate") or "0"),
                    min_charge   = decimal.Decimal(r.get("min_charge") or "0"),
                    active       = True,
                    priority     = int(r.get("priority") or 100),
                    effective_from = (r.get("effective_from") or None),
                    effective_to   = (r.get("effective_to") or None),
                    note          = r.get("note") or "",
                )
                obj, is_new = BillingRule.objects.update_or_create(
                    owner_id = (int(r["owner_id"]) if r.get("owner_id") else None),
                    charge_type = r["charge_type"],
                    calc_method = r["calc_method"],
                    defaults = defaults
                )
                created += int(is_new); updated += int(not is_new)
        self.stdout.write(self.style.SUCCESS(f"Imported rules: created={created}, updated={updated}"))
