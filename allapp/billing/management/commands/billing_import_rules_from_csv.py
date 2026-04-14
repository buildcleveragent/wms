# allapp/billing/management/commands/billing_import_rules_from_csv.py
# usage: python manage.py billing_import_rules_from_csv /path/to/billing_rules_template.csv
import csv
import decimal

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from allapp.billing.models import BillingRule


def _decimal_or_none(value):
    """CSV 字段转 Decimal，空值返回 None 而非 0。"""
    if not value or not str(value).strip():
        return None
    return decimal.Decimal(value)


def _str_or_none(value):
    """CSV 字段转字符串，空值返回 None。"""
    if not value or not str(value).strip():
        return None
    return str(value).strip()


class Command(BaseCommand):
    help = "Import BillingRule from a CSV file (utf-8-sig). Runs full_clean on each row."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str)
        parser.add_argument("--dry-run", action="store_true", help="Validate only, do not save")

    def handle(self, *args, **opts):
        path = opts["csv_file"]
        dry_run = opts["dry_run"]
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            created = updated = errors = 0
            for row_num, r in enumerate(reader, start=2):
                lookup = dict(
                    owner_id=(int(r["owner_id"]) if r.get("owner_id") else None),
                    warehouse_id=(int(r["warehouse_id"]) if r.get("warehouse_id") else None),
                    charge_type=r["charge_type"],
                    calc_method=r["calc_method"],
                )
                defaults = dict(
                    unit_price=_decimal_or_none(r.get("unit_price")),
                    currency=r.get("currency") or "CNY",
                    taxable=bool(int(r.get("taxable") or "1")),
                    tax_rate=decimal.Decimal(r.get("tax_rate") or "0"),
                    min_charge=_decimal_or_none(r.get("min_charge")),
                    active=True,
                    priority=int(r.get("priority") or 100),
                    effective_from=_str_or_none(r.get("effective_from")),
                    effective_to=_str_or_none(r.get("effective_to")),
                    note=r.get("note") or "",
                    # cap/bundle 字段
                    cap_mode=r.get("cap_mode") or None,
                    cap_amount=_decimal_or_none(r.get("cap_amount")),
                    bundle_scope=r.get("bundle_scope") or None,
                    bundle_type=r.get("bundle_type") or None,
                    bundle_key=r.get("bundle_key") or "",
                    bundle_price=_decimal_or_none(r.get("bundle_price")),
                    ladder_mode=_str_or_none(r.get("ladder_mode")),
                )

                try:
                    obj, is_new = BillingRule.objects.get_or_create(
                        defaults=defaults, **lookup,
                    )
                    if not is_new:
                        for k, v in defaults.items():
                            setattr(obj, k, v)
                    # 显式调用 full_clean 确保模型校验通过
                    obj.full_clean()
                    if not dry_run:
                        obj.save()
                    created += int(is_new)
                    updated += int(not is_new)
                except (ValidationError, ValueError, decimal.InvalidOperation) as e:
                    errors += 1
                    self.stderr.write(f"  Row {row_num}: {e}")

        status = "DRY-RUN " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{status}Imported rules: created={created}, updated={updated}, errors={errors}"
        ))
