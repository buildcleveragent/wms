import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from allapp.core.data_accuracy import reconcile_data_accuracy
from allapp.core.data_accuracy_cleanup import apply_safe_data_accuracy_fixes


def _failed_checks(section):
    if not section:
        return []
    return [
        {
            "name": check["name"],
            "issue_count": check["issue_count"],
        }
        for check in section["checks"]
        if not check["ok"]
    ]


def _render_failed_check_line(title, section):
    failed = _failed_checks(section)
    if not failed:
        return f"{title}: PASS"
    details = ", ".join(
        f"{item['name']}({item['issue_count']})"
        for item in failed
    )
    return f"{title}: {details}"


class Command(BaseCommand):
    help = (
        "Run a full-scope data accuracy cleanup workflow: reconcile current data, "
        "optionally apply safe fixes, then reconcile again."
    )

    def add_arguments(self, parser):
        parser.add_argument("--owner", type=int, help="Optional owner_id scope.")
        parser.add_argument("--warehouse", type=int, help="Optional warehouse_id scope.")
        parser.add_argument("--limit", type=int, default=20, help="Max sample rows per failed check.")
        parser.add_argument(
            "--apply-safe-fixes",
            action="store_true",
            help=(
                "Apply safe automatic fixes before re-checking: "
                "recalculate InventoryDetail.available_qty, rebuild InventorySummary "
                "(owner scope only), and recalculate Bill header totals from BillLine."
            ),
        )
        parser.add_argument("--json", action="store_true", dest="as_json", help="Render JSON output.")
        parser.add_argument(
            "--output",
            type=str,
            help="Optional path to write the full JSON cleanup report.",
        )
        parser.add_argument(
            "--fail-on-issues",
            action="store_true",
            help="Raise CommandError when issues remain after cleanup.",
        )

    def handle(self, *args, **options):
        owner_id = options.get("owner")
        warehouse_id = options.get("warehouse")
        limit = options["limit"]

        before = reconcile_data_accuracy(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            limit=limit,
        )
        fixes = None
        after = before
        if options["apply_safe_fixes"]:
            fixes = apply_safe_data_accuracy_fixes(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
            )
            after = reconcile_data_accuracy(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                limit=limit,
            )

        payload = {
            "scope": {
                "owner_id": owner_id,
                "warehouse_id": warehouse_id,
            },
            "before": before,
            "fixes": fixes,
            "after": after,
        }

        if options.get("output"):
            output_path = Path(options["output"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            lines = [
                "Data Accuracy Cleanup",
                f"Scope: owner={owner_id or '-'} warehouse={warehouse_id or '-'}",
                f"Before: {'PASS' if before['ok'] else 'FAIL'} issues={before['issue_count']}",
                _render_failed_check_line("  Inventory", before["inventory"]),
                _render_failed_check_line("  Billing", before["billing"]),
            ]
            if fixes is not None:
                lines.extend(
                    [
                        "Safe fixes applied:",
                        (
                            "  InventoryDetail.available_qty "
                            f"processed={fixes['inventory_details']['processed']} "
                            f"updated={fixes['inventory_details']['updated']}"
                        ),
                        (
                            "  InventorySummary "
                            f"processed={fixes['inventory_summaries']['processed']} "
                            f"created={fixes['inventory_summaries']['created']} "
                            f"updated={fixes['inventory_summaries']['updated']} "
                            f"skipped={fixes['inventory_summaries']['skipped']}"
                        ),
                        (
                            "  Bill headers "
                            f"processed={fixes['bill_headers']['processed']} "
                            f"updated={fixes['bill_headers']['updated']}"
                        ),
                        f"After: {'PASS' if after['ok'] else 'FAIL'} issues={after['issue_count']}",
                        _render_failed_check_line("  Inventory", after["inventory"]),
                        _render_failed_check_line("  Billing", after["billing"]),
                    ]
                )
                if fixes["inventory_summaries"]["reason"]:
                    lines.append(f"  Note: {fixes['inventory_summaries']['reason']}")
            self.stdout.write("\n".join(lines))

        if options["fail_on_issues"] and after["issue_count"] > 0:
            raise CommandError(
                f"Data accuracy cleanup finished with {after['issue_count']} remaining issue(s)."
            )
