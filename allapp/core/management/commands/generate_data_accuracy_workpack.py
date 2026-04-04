import csv
import datetime
import json
import shlex
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.billing.models import BillingPeriod
from allapp.locations.models import Warehouse


FALLBACK_DAILY_RECORD_HEADER = [
    "date",
    "executor",
    "tech_owner",
    "business_owner",
    "warehouse_owner",
    "owner_id",
    "warehouse_id",
    "period_id",
    "reconcile_result",
    "issue_count",
    "safe_fix_applied",
    "tracking_repair_applied",
    "smoke_done",
    "spot_check_done",
    "billing_only_check_done",
    "duplicate_job_run_found",
    "duplicate_bill_found",
    "snapshot_gap_found",
    "abnormal_accrual_found",
    "allow_lock_or_invoice",
    "allow_external_commitment",
    "notes",
]


def _parse_date(raw_value, *, option_name):
    try:
        return datetime.date.fromisoformat(raw_value)
    except ValueError as exc:
        raise CommandError(f"Invalid {option_name} value: {raw_value}") from exc


def _current_local_now():
    now = timezone.now()
    return timezone.localtime(now) if timezone.is_aware(now) else now


def _load_daily_record_header():
    template_path = Path(settings.BASE_DIR) / "docs" / "data-accuracy-daily-record-template.csv"
    try:
        with template_path.open("r", encoding="utf-8", newline="") as csv_file:
            reader = csv.reader(csv_file)
            return next(reader)
    except (OSError, StopIteration):
        return FALLBACK_DAILY_RECORD_HEADER


def _workpack_phase_note(day_offset, shadow_days):
    if day_offset == 0:
        return "Day 0 baseline"
    if day_offset == 1:
        return "Day 1 safe cleanup"
    if day_offset == 2:
        return "Day 2 tracking repair"
    if day_offset == 3:
        return "Day 3 billing rehearsal"
    shadow_index = day_offset - 3
    return f"Day {day_offset} shadow run {shadow_index}/{shadow_days}"


def _build_daily_record_rows(*, start_date, owner_id, warehouse_id, period_id, shadow_days):
    total_days = 4 + shadow_days
    rows = []
    for offset in range(total_days):
        record_date = start_date + datetime.timedelta(days=offset)
        rows.append(
            {
                "date": record_date.isoformat(),
                "executor": "",
                "tech_owner": "",
                "business_owner": "",
                "warehouse_owner": "",
                "owner_id": owner_id,
                "warehouse_id": warehouse_id,
                "period_id": period_id or "",
                "reconcile_result": "",
                "issue_count": "",
                "safe_fix_applied": "N",
                "tracking_repair_applied": "N",
                "smoke_done": "N",
                "spot_check_done": "N",
                "billing_only_check_done": "N",
                "duplicate_job_run_found": "N",
                "duplicate_bill_found": "N",
                "snapshot_gap_found": "N",
                "abnormal_accrual_found": "N",
                "allow_lock_or_invoice": "N",
                "allow_external_commitment": "N",
                "notes": _workpack_phase_note(offset, shadow_days),
            }
        )
    return rows


def _write_csv(path, *, header, rows):
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _quoted(path_or_value):
    return shlex.quote(str(path_or_value))


def _scope_arg_line(*, owner_id, warehouse_id, period_id=None):
    scope = f"--owner {owner_id} --warehouse {warehouse_id}"
    if period_id:
        scope += f" --period {period_id}"
    return scope


def _build_command_plan(*, owner_id, warehouse_id, period_id, service_date, files):
    scoped_args = _scope_arg_line(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        period_id=period_id,
    )
    billing_check_args = f"{scoped_args} --billing-only --date {service_date.isoformat()}"
    day0_commands = [
        f"python manage.py reconcile_data_accuracy --json > {_quoted(files['day0_global_baseline'])}",
        f"python manage.py reconcile_data_accuracy {scoped_args} --json > {_quoted(files['day0_scope_baseline'])}",
    ]
    day1_commands = [
        f"python manage.py reconcile_data_accuracy {scoped_args} --json > {_quoted(files['day1_before'])}",
        f"python manage.py reconcile_data_accuracy_cleanup --owner {owner_id} --warehouse {warehouse_id} --output {_quoted(files['day1_cleanup_preview'])}",
        (
            "# REVIEW BEFORE RUNNING: safe fixes mutate business tables.\n"
            f"python manage.py reconcile_data_accuracy_cleanup --owner {owner_id} --warehouse {warehouse_id} "
            f"--apply-safe-fixes --output {_quoted(files['day1_cleanup_fixed'])}"
        ),
        f"python manage.py reconcile_data_accuracy {scoped_args} --json > {_quoted(files['day1_after'])}",
    ]
    day2_commands = [
        (
            f"python manage.py export_inventory_tracking_repair_template "
            f"{_quoted(files['day2_tracking_template'])} --owner {owner_id} --warehouse {warehouse_id}"
        ),
        (
            f"python manage.py export_inventory_tracking_business_reply_sheet "
            f"{_quoted(files['day2_tracking_template'])} {_quoted(files['day2_business_reply'])}"
        ),
        (
            "# Fill the business reply CSV, then merge and apply.\n"
            f"python manage.py merge_inventory_tracking_business_reply "
            f"{_quoted(files['day2_tracking_template'])} {_quoted(files['day2_business_reply'])} "
            f"--output {_quoted(files['day2_tracking_ready'])}"
        ),
        (
            "# REVIEW BEFORE RUNNING: applies confirmed business tracking repairs.\n"
            f"python manage.py apply_inventory_tracking_repairs {_quoted(files['day2_tracking_ready'])}"
        ),
        f"python manage.py reconcile_data_accuracy {scoped_args} --json > {_quoted(files['day2_after'])}",
    ]
    day3_commands = [
        (
            f"python manage.py inventory_generate_snapshot --date {service_date.isoformat()} "
            f"--owner {owner_id} --warehouse {warehouse_id}"
        ),
        (
            f"python manage.py billing_run_scheduler --once --date {service_date.isoformat()} "
            f"--owner {owner_id} --warehouse {warehouse_id}"
        ),
        (
            f"python manage.py reconcile_data_accuracy {billing_check_args} --json "
            f"> {_quoted(files['day3_billing_before_lock'])}"
        ),
        "# Lock period via Billing admin/API, then re-run the billing-only reconciliation below.",
        (
            f"python manage.py reconcile_data_accuracy {billing_check_args} --json "
            f"> {_quoted(files['day3_billing_after_lock'])}"
        ),
        "# Generate invoice via Billing admin/API, then re-run the billing-only reconciliation below.",
        (
            f"python manage.py reconcile_data_accuracy {billing_check_args} --json "
            f"> {_quoted(files['day3_billing_after_invoice'])}"
        ),
        f"./.venv/bin/python -m pytest -q allapp/test_business_flows.py > {_quoted(files['day3_business_flows'])}",
    ]
    shadow_commands = [
        (
            f"python manage.py reconcile_data_accuracy --owner {owner_id} --warehouse {warehouse_id} "
            "--fail-on-issues"
        ),
        "# Run the business smoke checklist and spot-check SKU counts each day.",
    ]
    return [
        ("Day 0", day0_commands),
        ("Day 1", day1_commands),
        ("Day 2", day2_commands),
        ("Day 3", day3_commands),
        ("Day 4+", shadow_commands),
    ]


def _render_runbook(*, generated_at, execution_start_date, shadow_days, service_date, owner, warehouse, period, files, command_plan):
    shadow_start = execution_start_date + datetime.timedelta(days=4)
    shadow_end = shadow_start + datetime.timedelta(days=shadow_days - 1)
    lines = [
        "# Data Accuracy Workpack",
        "",
        "## Scope",
        f"- Generated at: `{generated_at.isoformat()}`",
        f"- Owner: `{owner.id}` / `{owner.code}` / `{owner.name}`",
        f"- Warehouse: `{warehouse.id}` / `{warehouse.code}` / `{warehouse.name}`",
        (
            f"- Period: `{period.id}` / `{period.label}` / "
            f"`{period.start_date.isoformat()} ~ {period.end_date.isoformat()}`"
            if period
            else "- Period: `-`"
        ),
        f"- Billing rehearsal service date: `{service_date.isoformat()}`",
        f"- Execution start date: `{execution_start_date.isoformat()}`",
        f"- Shadow run window: `{shadow_start.isoformat()} ~ {shadow_end.isoformat()}`",
        "",
        "## Generated Files",
        f"- Scope metadata: `{files['scope_json']}`",
        f"- Command script: `{files['commands']}`",
        f"- Daily record CSV: `{files['daily_record_csv']}`",
        "",
        "## Concrete Commands",
    ]
    for title, commands in command_plan:
        lines.append(f"### {title}")
        lines.append("```bash")
        lines.extend(commands)
        lines.append("```")
    lines.extend(
        [
            "",
            "## Stop Criteria",
            "- Stop lock/invoice when any reconciliation returns FAIL.",
            "- Stop external commitment when spot checks or billing-only checks disagree with the system.",
            "",
            "## Notes",
            "- Day 1 and Day 2 include mutating commands. Review outputs before running them in production.",
            "- Day 3 lock/invoice remains an admin/API action; the workpack already includes pre/post verification commands.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_command_script(*, owner_id, warehouse_id, period_id, service_date, workpack_dir, command_plan):
    period_line = f"PERIOD_ID={period_id}" if period_id else "PERIOD_ID="
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"WORKPACK_DIR={_quoted(workpack_dir)}",
        f"OWNER_ID={owner_id}",
        f"WAREHOUSE_ID={warehouse_id}",
        period_line,
        f"SERVICE_DATE={service_date.isoformat()}",
        "",
    ]
    for title, commands in command_plan:
        lines.append(f"# {title}")
        lines.extend(commands)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class Command(BaseCommand):
    help = "Generate a concrete data-accuracy execution workpack for one billing scope."

    def add_arguments(self, parser):
        parser.add_argument("--owner", type=int, required=True, help="Required owner_id scope.")
        parser.add_argument("--warehouse", type=int, required=True, help="Required warehouse_id scope.")
        parser.add_argument("--period", type=int, help="Optional BillingPeriod id. Must match owner + warehouse.")
        parser.add_argument("--service-date", type=str, help="Optional billing rehearsal date (YYYY-MM-DD).")
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Number of shadow-run days after Day 3. Default: 7.",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            help="Optional target directory for generated files.",
        )

    def handle(self, *args, **options):
        owner = Owner.objects.filter(pk=options["owner"]).only("id", "code", "name").first()
        if not owner:
            raise CommandError(f"Owner not found: {options['owner']}")

        warehouse = Warehouse.objects.filter(pk=options["warehouse"]).only("id", "code", "name").first()
        if not warehouse:
            raise CommandError(f"Warehouse not found: {options['warehouse']}")

        period = None
        if options.get("period") is not None:
            period = (
                BillingPeriod.objects
                .filter(pk=options["period"])
                .only("id", "owner_id", "warehouse_id", "label", "start_date", "end_date")
                .first()
            )
            if not period:
                raise CommandError(f"BillingPeriod not found: {options['period']}")
            if period.owner_id != owner.id or period.warehouse_id != warehouse.id:
                raise CommandError(
                    "BillingPeriod does not belong to the provided owner + warehouse scope."
                )

        shadow_days = options["days"]
        if shadow_days < 1:
            raise CommandError("--days must be greater than or equal to 1.")

        generated_at = _current_local_now()
        execution_start_date = generated_at.date()
        service_date = (
            _parse_date(options["service_date"], option_name="--service-date")
            if options.get("service_date")
            else (period.end_date if period else execution_start_date)
        )

        if options.get("output_dir"):
            workpack_dir = Path(options["output_dir"]).expanduser().resolve()
        else:
            folder_name = (
                f"owner-{owner.id}-warehouse-{warehouse.id}"
                f"{f'-period-{period.id}' if period else ''}"
                f"-{generated_at.strftime('%Y%m%d-%H%M%S')}"
            )
            workpack_dir = (
                Path(settings.BASE_DIR)
                / "tmp"
                / "data-accuracy-workpacks"
                / folder_name
            ).resolve()

        workpack_dir.mkdir(parents=True, exist_ok=True)

        files = {
            "scope_json": workpack_dir / "scope.json",
            "runbook": workpack_dir / "RUNBOOK.md",
            "commands": workpack_dir / "commands.sh",
            "daily_record_csv": workpack_dir / "daily-record.csv",
            "day0_global_baseline": workpack_dir / "day0-global-baseline.json",
            "day0_scope_baseline": workpack_dir / "day0-scope-baseline.json",
            "day1_before": workpack_dir / "day1-before.json",
            "day1_cleanup_preview": workpack_dir / "day1-cleanup-preview.json",
            "day1_cleanup_fixed": workpack_dir / "day1-cleanup-fixed.json",
            "day1_after": workpack_dir / "day1-after.json",
            "day2_tracking_template": workpack_dir / "day2-inventory-tracking-template.csv",
            "day2_business_reply": workpack_dir / "day2-business-reply.csv",
            "day2_tracking_ready": workpack_dir / "day2-tracking-ready.csv",
            "day2_after": workpack_dir / "day2-after.json",
            "day3_billing_before_lock": workpack_dir / "day3-billing-before-lock.json",
            "day3_billing_after_lock": workpack_dir / "day3-billing-after-lock.json",
            "day3_billing_after_invoice": workpack_dir / "day3-billing-after-invoice.json",
            "day3_business_flows": workpack_dir / "day3-business-flows.txt",
        }

        command_plan = _build_command_plan(
            owner_id=owner.id,
            warehouse_id=warehouse.id,
            period_id=period.id if period else None,
            service_date=service_date,
            files=files,
        )
        scope_payload = {
            "generated_at": generated_at.isoformat(),
            "execution_start_date": execution_start_date.isoformat(),
            "shadow_run_days": shadow_days,
            "shadow_run_start_date": (execution_start_date + datetime.timedelta(days=4)).isoformat(),
            "shadow_run_end_date": (
                execution_start_date + datetime.timedelta(days=3 + shadow_days)
            ).isoformat(),
            "service_date": service_date.isoformat(),
            "owner": {
                "id": owner.id,
                "code": owner.code,
                "name": owner.name,
            },
            "warehouse": {
                "id": warehouse.id,
                "code": warehouse.code,
                "name": warehouse.name,
            },
            "period": (
                {
                    "id": period.id,
                    "label": period.label,
                    "start_date": period.start_date.isoformat(),
                    "end_date": period.end_date.isoformat(),
                }
                if period
                else None
            ),
        }
        files["scope_json"].write_text(
            json.dumps(scope_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        files["runbook"].write_text(
            _render_runbook(
                generated_at=generated_at,
                execution_start_date=execution_start_date,
                shadow_days=shadow_days,
                service_date=service_date,
                owner=owner,
                warehouse=warehouse,
                period=period,
                files=files,
                command_plan=command_plan,
            ),
            encoding="utf-8",
        )
        files["commands"].write_text(
            _render_command_script(
                owner_id=owner.id,
                warehouse_id=warehouse.id,
                period_id=period.id if period else None,
                service_date=service_date,
                workpack_dir=workpack_dir,
                command_plan=command_plan,
            ),
            encoding="utf-8",
        )
        files["commands"].chmod(0o750)
        _write_csv(
            files["daily_record_csv"],
            header=_load_daily_record_header(),
            rows=_build_daily_record_rows(
                start_date=execution_start_date,
                owner_id=owner.id,
                warehouse_id=warehouse.id,
                period_id=period.id if period else None,
                shadow_days=shadow_days,
            ),
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Generated data accuracy workpack: path={workpack_dir}"
            )
        )
