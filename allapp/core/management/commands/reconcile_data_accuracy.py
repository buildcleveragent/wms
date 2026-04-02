import datetime
import json

from django.core.management.base import BaseCommand, CommandError

from allapp.core.data_accuracy import reconcile_data_accuracy


def _render_section_lines(title, section):
    lines = [f"{title}:"]
    if section is None:
        lines.append("  skipped")
        return lines

    for check in section["checks"]:
        status = "SKIP" if check["skipped"] else ("OK" if check["ok"] else "FAIL")
        line = f"  [{status}] {check['name']}"
        if not check["skipped"]:
            line += f" issues={check['issue_count']}"
        lines.append(line)
        if check["note"]:
            lines.append(f"    note: {check['note']}")
        for sample in check["samples"]:
            detail = ", ".join(f"{key}={value}" for key, value in sample.items())
            lines.append(f"    - {detail}")
    return lines


class Command(BaseCommand):
    help = "Reconcile core inventory and billing data accuracy without mutating business data."

    def add_arguments(self, parser):
        parser.add_argument("--owner", type=int, help="Optional owner_id scope.")
        parser.add_argument("--warehouse", type=int, help="Optional warehouse_id scope.")
        parser.add_argument("--date", type=str, help="Optional service date (YYYY-MM-DD) for billing checks.")
        parser.add_argument("--period", type=int, help="Optional BillingPeriod id for billing checks.")
        parser.add_argument("--limit", type=int, default=20, help="Max sample rows per failed check.")
        parser.add_argument("--inventory-only", action="store_true", help="Run inventory checks only.")
        parser.add_argument("--billing-only", action="store_true", help="Run billing checks only.")
        parser.add_argument("--json", action="store_true", dest="as_json", help="Render JSON output.")
        parser.add_argument(
            "--fail-on-issues",
            action="store_true",
            help="Raise CommandError when any reconciliation issue is found.",
        )

    def handle(self, *args, **options):
        if options["inventory_only"] and options["billing_only"]:
            raise CommandError("--inventory-only and --billing-only cannot be used together.")

        service_date = None
        if options.get("date"):
            try:
                service_date = datetime.date.fromisoformat(options["date"])
            except ValueError as exc:
                raise CommandError(f"Invalid --date value: {options['date']}") from exc

        include_inventory = not options["billing_only"]
        include_billing = not options["inventory_only"]
        summary = reconcile_data_accuracy(
            owner_id=options.get("owner"),
            warehouse_id=options.get("warehouse"),
            service_date=service_date,
            period_id=options.get("period"),
            include_inventory=include_inventory,
            include_billing=include_billing,
            limit=options["limit"],
        )

        if options["as_json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            lines = [
                "Data Accuracy Reconciliation",
                (
                    "Scope: "
                    f"owner={summary['scope']['owner_id'] or '-'} "
                    f"warehouse={summary['scope']['warehouse_id'] or '-'} "
                    f"service_date={summary['scope']['service_date'] or '-'} "
                    f"period={summary['scope']['period_id'] or '-'}"
                ),
            ]
            if include_inventory:
                lines.extend(_render_section_lines("Inventory", summary["inventory"]))
            if include_billing:
                lines.extend(_render_section_lines("Billing", summary["billing"]))
            lines.append(
                f"Overall: {'PASS' if summary['ok'] else 'FAIL'} issues={summary['issue_count']}"
            )
            self.stdout.write("\n".join(lines))

        if options["fail_on_issues"] and summary["issue_count"] > 0:
            raise CommandError(
                f"Data accuracy reconciliation failed with {summary['issue_count']} issue(s)."
            )
