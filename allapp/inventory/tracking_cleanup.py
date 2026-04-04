from __future__ import annotations

import csv
import datetime
from collections import OrderedDict
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction

from allapp.inventory.models import InventoryDetail, InventoryTransaction


TRACKING_REPAIR_COLUMNS = [
    "source",
    "id",
    "owner_id",
    "owner_code",
    "owner_name",
    "warehouse_id",
    "warehouse_code",
    "product_id",
    "product_code",
    "product_name",
    "location_id",
    "location_code",
    "current_batch_no",
    "current_production_date",
    "current_expiry_date",
    "current_serial_no",
    "problems",
    "new_batch_no",
    "new_production_date",
    "new_expiry_date",
    "new_serial_no",
    "note",
]

BUSINESS_REPLY_COLUMNS = [
    "owner_code",
    "owner_name",
    "product_code",
    "product_name",
    "warehouse_code",
    "location_code",
    "inventory_detail_ids",
    "inventory_transaction_ids",
    "need_batch_no",
    "need_production_date",
    "need_expiry_date",
    "business_confirmed_batch_no",
    "business_confirmed_production_date",
    "business_confirmed_expiry_date",
    "evidence_source",
    "confirmed_by",
    "confirmed_at",
    "remarks",
]

BUSINESS_REPLY_KEY_FIELDS = (
    "owner_code",
    "product_code",
    "warehouse_code",
    "location_code",
)


def _date_text(value):
    return value.isoformat() if value else ""


def _csv_text(value):
    return (value or "").strip()


def _build_issue_row(*, source: str, obj, problems: Iterable[str]):
    return {
        "source": source,
        "id": obj.id,
        "owner_id": obj.owner_id,
        "owner_code": getattr(obj.owner, "code", ""),
        "owner_name": getattr(obj.owner, "name", ""),
        "warehouse_id": obj.warehouse_id,
        "warehouse_code": getattr(obj.warehouse, "code", ""),
        "product_id": obj.product_id,
        "product_code": getattr(obj.product, "code", ""),
        "product_name": getattr(obj.product, "name", ""),
        "location_id": obj.location_id,
        "location_code": getattr(obj.location, "code", ""),
        "current_batch_no": obj.batch_no or "",
        "current_production_date": _date_text(getattr(obj, "production_date", None)),
        "current_expiry_date": _date_text(getattr(obj, "expiry_date", None)),
        "current_serial_no": getattr(obj, "serial_no", "") or "",
        "problems": ",".join(problems),
        "new_batch_no": "",
        "new_production_date": "",
        "new_expiry_date": "",
        "new_serial_no": "",
        "note": "",
    }


def collect_inventory_tracking_gap_rows(*, owner_id=None, warehouse_id=None):
    rows = []

    detail_queryset = InventoryDetail.objects.filter(is_active=True).select_related(
        "owner",
        "warehouse",
        "product",
        "location",
    )
    tx_queryset = InventoryTransaction.objects.filter(
        is_active=True,
        posted_at__isnull=False,
    ).select_related(
        "owner",
        "warehouse",
        "product",
        "location",
    )
    if owner_id:
        detail_queryset = detail_queryset.filter(owner_id=owner_id)
        tx_queryset = tx_queryset.filter(owner_id=owner_id)
    if warehouse_id:
        detail_queryset = detail_queryset.filter(warehouse_id=warehouse_id)
        tx_queryset = tx_queryset.filter(warehouse_id=warehouse_id)

    for detail in detail_queryset.order_by("owner_id", "product_id", "location_id", "id"):
        problems = []
        product = detail.product
        if getattr(product, "batch_control", False) and not (detail.batch_no or "").strip():
            problems.append("missing_batch_no")
        if getattr(product, "expiry_control", False):
            if not detail.expiry_date:
                problems.append("missing_expiry_date")
            if getattr(product, "expiry_basis", None) == "MFG" and not detail.production_date:
                problems.append("missing_production_date")
        if problems:
            rows.append(_build_issue_row(source="detail", obj=detail, problems=problems))

    for tx in tx_queryset.order_by("owner_id", "product_id", "location_id", "id"):
        problems = []
        product = tx.product
        if getattr(product, "batch_control", False) and not (tx.batch_no or "").strip():
            problems.append("missing_batch_no")
        if getattr(product, "expiry_control", False):
            if not tx.expiry_date:
                problems.append("missing_expiry_date")
            if getattr(product, "expiry_basis", None) == "MFG" and not tx.production_date:
                problems.append("missing_production_date")
        if problems:
            rows.append(_build_issue_row(source="transaction", obj=tx, problems=problems))

    return rows


def export_inventory_tracking_gap_rows(rows, output_file):
    with open(output_file, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=TRACKING_REPAIR_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv_rows(csv_file):
    with open(csv_file, "r", newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _write_csv_rows(output_file, fieldnames, rows):
    with open(output_file, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _reply_group_key(row):
    return tuple(_csv_text(row.get(field_name)) for field_name in BUSINESS_REPLY_KEY_FIELDS)


def _collect_problem_flags(problem_text):
    flags = {
        "need_batch_no": False,
        "need_production_date": False,
        "need_expiry_date": False,
    }
    problems = {part.strip() for part in _csv_text(problem_text).split(",") if part.strip()}
    if "missing_batch_no" in problems:
        flags["need_batch_no"] = True
    if "missing_production_date" in problems:
        flags["need_production_date"] = True
    if "missing_expiry_date" in problems:
        flags["need_expiry_date"] = True
    return flags


def build_inventory_tracking_business_reply_rows(template_rows):
    grouped_rows = OrderedDict()

    for row in template_rows:
        key = _reply_group_key(row)
        if not any(key):
            raise ValidationError("Template row is missing business reply grouping fields.")

        bucket = grouped_rows.setdefault(
            key,
            {
                "owner_code": _csv_text(row.get("owner_code")),
                "owner_name": _csv_text(row.get("owner_name")),
                "product_code": _csv_text(row.get("product_code")),
                "product_name": _csv_text(row.get("product_name")),
                "warehouse_code": _csv_text(row.get("warehouse_code")),
                "location_code": _csv_text(row.get("location_code")),
                "inventory_detail_ids": [],
                "inventory_transaction_ids": [],
                "need_batch_no": "N",
                "need_production_date": "N",
                "need_expiry_date": "N",
                "business_confirmed_batch_no": "",
                "business_confirmed_production_date": "",
                "business_confirmed_expiry_date": "",
                "evidence_source": "",
                "confirmed_by": "",
                "confirmed_at": "",
                "remarks": "",
            },
        )

        if _csv_text(row.get("source")).lower() == "detail":
            bucket["inventory_detail_ids"].append(_csv_text(row.get("id")))
        elif _csv_text(row.get("source")).lower() == "transaction":
            bucket["inventory_transaction_ids"].append(_csv_text(row.get("id")))
        else:
            raise ValidationError(f"Unsupported template source: {row.get('source')!r}")

        flags = _collect_problem_flags(row.get("problems"))
        if flags["need_batch_no"]:
            bucket["need_batch_no"] = "Y"
        if flags["need_production_date"]:
            bucket["need_production_date"] = "Y"
        if flags["need_expiry_date"]:
            bucket["need_expiry_date"] = "Y"

    reply_rows = []
    for bucket in grouped_rows.values():
        bucket["inventory_detail_ids"] = ",".join(filter(None, bucket["inventory_detail_ids"]))
        bucket["inventory_transaction_ids"] = ",".join(
            filter(None, bucket["inventory_transaction_ids"])
        )
        reply_rows.append(bucket)
    return reply_rows


def export_inventory_tracking_business_reply_sheet(template_csv, output_file):
    template_rows = _read_csv_rows(template_csv)
    reply_rows = build_inventory_tracking_business_reply_rows(template_rows)
    _write_csv_rows(output_file, BUSINESS_REPLY_COLUMNS, reply_rows)
    return {
        "template_rows": len(template_rows),
        "reply_rows": len(reply_rows),
    }


def _parse_optional_date(raw_value: str):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.date.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValidationError(f"Invalid date value: {raw_value}") from exc


def _snapshot_tracking_values(obj):
    return {
        "current_batch_no": (obj.batch_no or "").strip(),
        "current_production_date": _date_text(getattr(obj, "production_date", None)),
        "current_expiry_date": _date_text(getattr(obj, "expiry_date", None)),
        "current_serial_no": (getattr(obj, "serial_no", "") or "").strip(),
    }


def _validate_row_matches_current_tracking_state(row, obj):
    errors = {}
    current_values = _snapshot_tracking_values(obj)
    for field_name, current_value in current_values.items():
        csv_value = (row.get(field_name) or "").strip()
        if csv_value != current_value:
            errors[field_name] = (
                "CSV current value does not match database; "
                "regenerate the template before applying repairs."
            )
    if errors:
        raise ValidationError(errors)


def _validate_required_repair_values(row, obj):
    product = obj.product
    errors = {}
    new_batch_no = (row.get("new_batch_no") or "").strip()
    new_production_date = (row.get("new_production_date") or "").strip()
    new_expiry_date = (row.get("new_expiry_date") or "").strip()
    new_serial_no = (row.get("new_serial_no") or "").strip()

    if (
        getattr(product, "batch_control", False)
        and not (obj.batch_no or "").strip()
        and not new_batch_no
    ):
        errors["new_batch_no"] = "Batch-controlled inventory rows require new_batch_no."
    if getattr(product, "expiry_control", False):
        if not getattr(obj, "expiry_date", None) and not new_expiry_date:
            errors["new_expiry_date"] = "Expiry-controlled inventory rows require new_expiry_date."
        if (
            getattr(product, "expiry_basis", None) == "MFG"
            and not getattr(obj, "production_date", None)
            and not new_production_date
        ):
            errors["new_production_date"] = (
                "MFG-based expiry-controlled inventory rows require new_production_date."
            )
    if (
        getattr(product, "serial_control", False)
        and not (obj.serial_no or "").strip()
        and not new_serial_no
    ):
        errors["new_serial_no"] = "Serial-controlled inventory rows require new_serial_no."

    if errors:
        raise ValidationError(errors)


def apply_inventory_tracking_repairs_from_csv(csv_file):
    summary = {
        "rows": 0,
        "updated": 0,
        "skipped": 0,
    }

    with transaction.atomic():
        with open(csv_file, "r", newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                summary["rows"] += 1
                source = (row.get("source") or "").strip().lower()
                try:
                    object_id = int((row.get("id") or "").strip())
                except (TypeError, ValueError) as exc:
                    raise ValidationError({"id": "Row id must be an integer."}) from exc

                if source == "detail":
                    obj = InventoryDetail.objects.select_related("product").get(pk=object_id)
                elif source == "transaction":
                    obj = InventoryTransaction.objects.select_related("product").get(pk=object_id)
                else:
                    raise ValidationError(f"Unsupported source: {row.get('source')!r}")

                _validate_row_matches_current_tracking_state(row, obj)
                _validate_required_repair_values(row, obj)

                changed = False
                if (row.get("new_batch_no") or "").strip():
                    obj.batch_no = row["new_batch_no"].strip()
                    changed = True
                if (row.get("new_production_date") or "").strip():
                    obj.production_date = _parse_optional_date(row["new_production_date"])
                    changed = True
                if (row.get("new_expiry_date") or "").strip():
                    obj.expiry_date = _parse_optional_date(row["new_expiry_date"])
                    changed = True
                if (row.get("new_serial_no") or "").strip():
                    obj.serial_no = row["new_serial_no"].strip()
                    changed = True

                if not changed:
                    summary["skipped"] += 1
                    continue

                obj.save()
                summary["updated"] += 1

    return summary


def _validate_business_reply_columns(reply_rows):
    if not reply_rows:
        return
    missing_columns = [
        field_name
        for field_name in BUSINESS_REPLY_COLUMNS
        if field_name not in reply_rows[0]
    ]
    if missing_columns:
        raise ValidationError(
            {
                "__all__": (
                    "Business reply CSV is missing required columns: "
                    + ", ".join(missing_columns)
                )
            }
        )


def _load_business_reply_map(reply_csv):
    reply_rows = _read_csv_rows(reply_csv)
    _validate_business_reply_columns(reply_rows)

    reply_map = OrderedDict()
    for index, row in enumerate(reply_rows, start=2):
        if not any(_csv_text(value) for value in row.values()):
            continue
        key = _reply_group_key(row)
        if not any(key):
            raise ValidationError(
                {
                    "__all__": (
                        f"Business reply row {index} is missing "
                        "owner/product/warehouse/location key fields."
                    )
                }
            )
        if key in reply_map:
            raise ValidationError(
                {
                    "__all__": (
                        "Business reply CSV contains duplicate group rows for "
                        f"owner={key[0]!r}, product={key[1]!r}, "
                        f"warehouse={key[2]!r}, location={key[3]!r}."
                    )
                }
            )
        reply_map[key] = row
    return reply_map


def _build_business_reply_note(reply_row):
    parts = []
    evidence_source = _csv_text(reply_row.get("evidence_source"))
    confirmed_by = _csv_text(reply_row.get("confirmed_by"))
    confirmed_at = _csv_text(reply_row.get("confirmed_at"))
    remarks = _csv_text(reply_row.get("remarks"))
    if evidence_source:
        parts.append(f"evidence={evidence_source}")
    if confirmed_by:
        parts.append(f"confirmed_by={confirmed_by}")
    if confirmed_at:
        parts.append(f"confirmed_at={confirmed_at}")
    if remarks:
        parts.append(f"remarks={remarks}")
    return "; ".join(parts)


def merge_inventory_tracking_business_reply_into_template(template_csv, reply_csv, output_file):
    template_rows = _read_csv_rows(template_csv)
    reply_map = _load_business_reply_map(reply_csv)

    merged_rows = []
    matched_reply_keys = set()
    matched_template_keys = set()
    summary = {
        "template_rows": len(template_rows),
        "reply_groups": len(reply_map),
        "matched_rows": 0,
        "updated_rows": 0,
        "rows_without_reply": 0,
        "matched_groups": 0,
        "groups_without_reply": 0,
        "output_file": output_file,
    }

    for row in template_rows:
        key = _reply_group_key(row)
        reply_row = reply_map.get(key)
        if not reply_row:
            merged_rows.append(row)
            summary["rows_without_reply"] += 1
            continue

        matched_reply_keys.add(key)
        matched_template_keys.add(key)
        summary["matched_rows"] += 1

        changed = False
        value_mapping = (
            ("business_confirmed_batch_no", "new_batch_no"),
            ("business_confirmed_production_date", "new_production_date"),
            ("business_confirmed_expiry_date", "new_expiry_date"),
        )
        for reply_field, template_field in value_mapping:
            reply_value = _csv_text(reply_row.get(reply_field))
            if reply_value and _csv_text(row.get(template_field)) != reply_value:
                row[template_field] = reply_value
                changed = True

        merged_note = _build_business_reply_note(reply_row)
        if merged_note and merged_note not in _csv_text(row.get("note")):
            current_note = _csv_text(row.get("note"))
            row["note"] = f"{current_note} | {merged_note}" if current_note else merged_note
            changed = True

        if changed:
            summary["updated_rows"] += 1
        merged_rows.append(row)

    unmatched_reply_keys = [key for key in reply_map.keys() if key not in matched_reply_keys]
    if unmatched_reply_keys:
        first = unmatched_reply_keys[0]
        raise ValidationError(
            {
                "__all__": (
                    "Business reply CSV contains rows that do not match the template: "
                    f"owner={first[0]!r}, product={first[1]!r}, "
                    f"warehouse={first[2]!r}, location={first[3]!r}."
                )
            }
        )

    summary["matched_groups"] = len(matched_template_keys)
    template_group_count = len({_reply_group_key(row) for row in template_rows})
    summary["groups_without_reply"] = max(
        template_group_count - len(matched_template_keys),
        0,
    )
    _write_csv_rows(output_file, TRACKING_REPAIR_COLUMNS, merged_rows)
    return summary
