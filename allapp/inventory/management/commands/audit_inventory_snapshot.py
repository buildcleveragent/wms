"""Compare historical snapshot stock to current stock less subsequent posted movements."""

import csv
import datetime
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from allapp.inventory.snapshot_audit import DIMENSIONS, audit_day as _audit_day

def _review_rows(audit, source, as_of):
    findings = audit["issues"].copy()
    if not audit["snapshot_present"]:
        findings.insert(0, {"issue": "no_snapshot_rows"})
    if not findings:
        findings.append({"issue": "comparable_onhand_matches"})
    for finding in findings:
        yield (
            source,
            as_of.isoformat(),
            audit["owner_id"],
            audit["warehouse_id"],
            audit["date"],
            finding["issue"],
            *(finding.get(field) for field in DIMENSIONS),
            finding.get("snapshot_onhand"),
            finding.get("reconstructed_onhand"),
            "待复核",
            "",
            "",
            "",
            "",
        )


class Command(BaseCommand):
    help = "只读核查历史在库快照。差异需结合日结时间和流水完整性复核，结果不用于推定历史预留量。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="dates",
            type=datetime.date.fromisoformat,
            action="append",
            required=True,
            help="历史业务日期，可重复指定。",
        )
        parser.add_argument("--owner", type=int, required=True)
        parser.add_argument("--warehouse", type=int, required=True)
        parser.add_argument("--format", choices=["jsonl", "review-csv"], default="jsonl")
        parser.add_argument("--source-reference", help="核查副本或备份编号。")
        parser.add_argument(
            "--as-of",
            type=datetime.datetime.fromisoformat,
            help="副本数据时间，含时区，例如 2026-11-25T06:00:00+08:00。",
        )

    def handle(self, *args, **options):
        zone = timezone.get_default_timezone()
        as_of = options["as_of"]
        source = (options["source_reference"] or "").strip()
        review = options["format"] == "review-csv"
        if review and (not source or not as_of):
            raise CommandError("复核清单需要副本编号 --source-reference 和数据时间 --as-of。")
        if as_of and timezone.is_naive(as_of):
            raise CommandError("--as-of 需要明确时区。")
        now = datetime.datetime.now(zone)
        cutoff = min(now, as_of.astimezone(zone)) if as_of else now
        if any(day >= cutoff.date() for day in options["dates"]):
            raise CommandError("请选择在副本数据时间之前已结束的业务日期。")
        if review:
            writer = csv.writer(self.stdout, lineterminator="\n")
            writer.writerow(
                (
                    "副本编号",
                    "副本数据时间",
                    "货主ID",
                    "仓库ID",
                    "业务日期",
                    "在库核查结果",
                    "商品ID",
                    "库位ID",
                    "批次",
                    "生产日期",
                    "到期日期",
                    "序列号",
                    "快照在库",
                    "倒推在库",
                    "复核状态",
                    "原始凭据",
                    "仓管复核",
                    "财务影响及复核",
                    "更正记录",
                )
            )
        scope = {"owner_id": options["owner"], "warehouse_id": options["warehouse"]}
        for day in sorted(set(options["dates"])):
            audit = _audit_day(day, scope)
            if review:
                for row in _review_rows(audit, source, as_of):
                    writer.writerow(
                        (
                            "'" + value
                            if isinstance(value, str)
                            and value.lstrip().startswith(("=", "+", "-", "@"))
                            else value
                        )
                        for value in row
                    )
            else:
                self.stdout.write(json.dumps(audit, default=str, ensure_ascii=False))
