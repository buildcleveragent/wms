"""Seal and independently reconcile one owner's inventory at a Shanghai business-day boundary."""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone

from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.inventory.daily_close_transaction import close_transaction
from allapp.inventory.models import (
    InventoryCloseReview,
    InventoryCloseScope,
    InventoryDailyClose,
    InventoryOpeningVersion,
    InventorySnapshotDaily,
    InventoryTransaction,
)
from allapp.inventory.snapshot_audit import DIMENSIONS, audit_day, balance_evidence
from allapp.inventory.snapshot_services import (
    _load_hold_balances,
    _load_previous_snapshot_payloads,
    _load_tx_aggregates,
    _lock_snapshot_scope,
    _q4,
    _snapshot_dim_key_from_row,
    generate_inventory_snapshot_for_date,
    trusted_inventory_baseline,
)
from allapp.products.models import Product

DAY = timedelta(days=1)
SHANGHAI = ZoneInfo("Asia/Shanghai")
QUANTITIES = ("onhand_qty", "available_qty", "allocated_qty", "locked_qty", "damaged_qty")
HELD = QUANTITIES[2:]
Quality = InventoryDailyClose.Quality
MESSAGES = {
    "undated_movements": "流水缺少过账日期，无法确认归属业务日。",
    "onhand_difference": "日终在库与当前库存扣除后续流水的结果不一致。",
    "snapshot_missing": "缺少可信前一日期初或该日库存快照。",
    "approximate_source": "快照包含近似或来源未确认的库存行。",
    "hold_history_missing": "该日早于库存占用历史覆盖范围。",
    "hold_difference": "日终占用与该日最后一次记录的分配、锁定或破损量不一致。",
    "quantity_difference": "日终可用量与在库扣除分配、锁定及破损量的结果不一致。",
    "rollforward_difference": "期初在库加当日净变动与日终在库不一致。",
    "opening_basis_changed": "封存时引用的前一日基线已失效或已由新的签核版本替代。",
    "opening_changed": "新过账或日终依据变化，原已签期初需要重新核对。",
    "sealed_rows_changed": "原日结封存行数与现场快照行数不一致。",
    "posting_evidence_changed": "封存当日的过账流水清单、维度或数量已发生变化。",
    "late_posting": "封存后新增或更改了归属该业务日的库存流水。",
    "duplicate_dimension": "多条快照记录对应同一个库存维度。",
}


def shanghai_now():
    now = timezone.now()
    return now.astimezone(SHANGHAI) if timezone.is_aware(now) else now


def effective_bindings():
    return OwnerWarehouseBinding.objects.filter(
        is_active=True,
        owner__is_active=True,
        owner__is_deleted=False,
        warehouse__is_active=True,
        warehouse__is_deleted=False,
        warehouse__inventoryclosepilot__enabled=True,
    )


def binding_start_date(binding):
    pilot = binding.warehouse.inventoryclosepilot
    if binding.created_at <= pilot.created_at:
        return pilot.start_date
    created = binding.created_at
    if timezone.is_aware(created):
        created = created.astimezone(SHANGHAI)
    return max(pilot.start_date, created.date())


def _active_binding(owner_id, warehouse_id):
    return (
        effective_bindings()
        .select_related("warehouse__inventoryclosepilot")
        .filter(owner_id=owner_id, warehouse_id=warehouse_id)
        .first()
    )


def pilot_covers_date(owner_id, warehouse_id, day):
    """Check enrollment without creating queue state during a billing read."""
    binding = _active_binding(owner_id, warehouse_id)
    return bool(binding and day >= binding_start_date(binding))


def _scope_for(owner_id, warehouse_id):
    binding = _active_binding(owner_id, warehouse_id)
    if binding is None:
        raise ValueError("这个货主与仓库没有已启用的日结绑定。")
    pilot = binding.warehouse.inventoryclosepilot
    start = binding_start_date(binding)
    scope, _ = InventoryCloseScope.objects.get_or_create(
        pilot=pilot, owner_id=owner_id, defaults={"start_date": start}
    )
    if scope.start_date != start and scope.through_date is None:
        if not InventoryDailyClose.objects.filter(
            owner_id=owner_id, warehouse_id=warehouse_id
        ).exists():
            scope.start_date = start
            scope.save(update_fields=["start_date"])
    return scope


def _json_fields(values):
    return {
        name: (
            value.isoformat()
            if isinstance(value, date)
            else str(value) if isinstance(value, Decimal) else value
        )
        for name, value in values.items()
    }


def _dim(key):
    return _json_fields(dict(zip(DIMENSIONS, (key[3], key[2], key[4], key[5], key[6], key[7]))))


def _issue(code, **fields):
    return {"issue": code, "message": MESSAGES[code], **_json_fields(fields)}


def opening_candidate(close, *, allow_unknown_holds=False):
    """Reconstruct proposed opening rows without writing or substituting today's reservations."""
    scope = dict(owner_id=close.owner_id, warehouse_id=close.warehouse_id)
    balances, _ = balance_evidence(close.business_date, scope)
    holds = _load_hold_balances(close.business_date, **scope)
    if holds is None and not allow_unknown_holds:
        raise ValueError(MESSAGES["hold_history_missing"])
    quantities = {}
    for key, values in balances.items():
        if "undated" in values:
            raise ValueError(MESSAGES["undated_movements"])
        product, location, batch, produced, expires, serial = key
        quantities[
            (
                close.owner_id,
                close.warehouse_id,
                location,
                product,
                batch,
                produced,
                expires,
                serial,
            )
        ] = (
            values["current"] - values["later"]
        )
    keys = set(quantities) | set(holds or {})
    products = {
        p.pk: p
        for p in Product.all_objects.select_related("base_uom").filter(
            pk__in={key[3] for key in keys}
        )
    }
    rows = []
    for key in sorted(keys, key=str):
        allocated, locked, damaged = (holds or {}).get(key, (0, 0, 0))
        onhand = quantities.get(key, 0)
        available = onhand - allocated - locked - damaged
        if min(available, allocated, locked, damaged) < 0:
            raise ValueError("库存倒推后出现负数，请先复核库存与流水。")
        if onhand:
            product = products[key[3]]
            values = dict(zip(QUANTITIES, (onhand, available, allocated, locked, damaged)))
            rows.append(
                {
                    **_dim(key),
                    **{
                        field: str(_q4(qty)) if holds is not None or field == "onhand_qty" else None
                        for field, qty in values.items()
                    },
                    "base_unit_code": product.base_uom.code,
                    "unit_volume_m3_snapshot": (
                        str(product.volume) if product.volume is not None else None
                    ),
                    "location_area_m2_snapshot": None,
                }
            )
    return rows


def opening_is_current(opening, close):
    """A signed opening stays immutable; backdated entries require a newly signed version."""
    if (
        not opening.approved_at
        or not opening.approved_by_id
        or opening.approved_by_id == opening.submitted_by_id
    ):
        return False
    late = InventoryTransaction.all_objects.filter(
        owner_id=close.owner_id,
        warehouse_id=close.warehouse_id,
        posted_at__date__lte=close.business_date,
        updated_at__gt=opening.approved_at,
    )
    if late.exists():
        return False
    try:
        current = opening_candidate(close, allow_unknown_holds=True)
    except ValueError:
        return False
    expected = {tuple(row.get(f) for f in DIMENSIONS): row for row in opening.rows}
    if len(expected) != len(current):
        return False
    for row in current:
        stored = expected.get(tuple(row[f] for f in DIMENSIONS))
        if stored is None or any(
            row[f] is not None and Decimal(row[f]) != Decimal(stored[f]) for f in QUANTITIES
        ):
            return False
    return True


def posting_evidence(business_date, scope):
    """Hash source identities as well as amounts so offsetting late entries remain detectable."""
    ledger = InventoryTransaction.objects.filter(**scope, posted_at__date=business_date).order_by(
        "pk"
    )
    digest = hashlib.sha256()
    count = 0
    for row in ledger.values_list(
        "pk", *DIMENSIONS, "qty_delta", "posted_at", "updated_at"
    ).iterator():
        digest.update(
            json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":")).encode()
        )
        digest.update(b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def _collect_rows(close, previous, movements, holds):
    """Aggregate legacy nullable dimensions without hiding duplicate or inconsistent evidence."""
    rows = InventorySnapshotDaily.objects.filter(
        owner_id=close.owner_id, warehouse_id=close.warehouse_id, snapshot_date=close.business_date
    )
    data = {}
    issues = []
    approx = False
    count = 0
    for row in rows.iterator():
        key = _snapshot_dim_key_from_row(row)
        if key in data:
            issues.append(_issue("duplicate_dimension", **_dim(key)))
        entry = data.setdefault(
            key,
            {
                "snapshot_source": row.snapshot_source,
                "base_unit_code": row.base_unit_code,
                **dict.fromkeys(QUANTITIES, Decimal(0)),
            },
        )
        for field in QUANTITIES:
            entry[field] += getattr(row, field)
        approx |= row.snapshot_source != InventorySnapshotDaily.Source.TX_ROLLFORWARD
        count += 1
    details = []
    totals = {}
    keys = set(data) | set(previous or {}) | set(movements) | set(holds or {})
    for key in sorted(keys, key=str):
        start_row = (previous or {}).get(key, {})
        entry = data.get(
            key,
            {"base_unit_code": start_row.get("base_unit_code", ""), **dict.fromkeys(QUANTITIES, 0)},
        )
        opening = start_row.get("onhand_qty", 0) if previous is not None else None
        delta = movements.get(key, 0)
        end = entry["onhand_qty"]
        if opening is not None and opening + delta != end:
            issues.append(
                _issue(
                    "rollforward_difference",
                    **_dim(key),
                    expected_onhand=_q4(opening + delta),
                    snapshot_onhand=_q4(end),
                )
            )
        if (
            end - sum(entry[f] for f in HELD) != entry["available_qty"]
            or min(entry[f] for f in QUANTITIES) < 0
        ):
            issues.append(_issue("quantity_difference", **_dim(key)))
        if holds is not None:
            target = holds.get(key, (0, 0, 0))
            for field, expected in zip(HELD, target):
                if expected != entry[field]:
                    issues.append(
                        _issue(
                            "hold_difference",
                            **_dim(key),
                            field=field,
                            expected=_q4(expected),
                            observed=_q4(entry[field]),
                        )
                    )
        amounts = {
            "opening_qty": opening,
            "movement_qty": delta,
            **{f: entry[f] for f in QUANTITIES},
        }
        unit = entry["base_unit_code"]
        subtotals = totals.setdefault(unit, {})
        for f, value in amounts.items():
            subtotals[f] = subtotals.get(f, 0) + value if value is not None else None
        details.append(
            {
                **_dim(key),
                "base_unit_code": unit,
                "snapshot_source": entry.get("snapshot_source", ""),
                "hold_source": "历史日终占用" if holds is not None else "缺少历史记录",
                **{f: str(_q4(v)) if v is not None else None for f, v in amounts.items()},
            }
        )
    totals = {
        unit: {f: str(_q4(v)) if v is not None else None for f, v in amounts.items()}
        for unit, amounts in totals.items()
    }
    empty = {f: "0.0000" for f in ("opening_qty", "movement_qty", *QUANTITIES)}
    if previous is None:
        empty["opening_qty"] = None
    summary = (
        next(iter(totals.values()))
        if len(totals) == 1
        else dict.fromkeys(empty) if totals else empty
    )
    return count, approx, details, {**summary, "by_unit": totals}, issues


@close_transaction()
def run_daily_close(*, owner_id, warehouse_id, business_date):
    """Compute or recheck an ended business day; sealed physical rows and figures stay fixed."""
    from allapp.inventory.daily_close_review import sync_close_alert

    scope = dict(owner_id=owner_id, warehouse_id=warehouse_id)
    if business_date >= shanghai_now().date():
        raise ValueError("只能日结已经结束的上海业务日。")
    _lock_snapshot_scope(**scope)
    observed_at = timezone.now()
    pilot_scope = _scope_for(**scope)
    if business_date < pilot_scope.start_date - DAY:
        raise ValueError("该日期早于日结范围启用日前一天。")
    close, _ = InventoryDailyClose.objects.select_for_update().get_or_create(
        **scope, business_date=business_date
    )
    was_sealed = bool(close.sealed_at)
    observed_postings = posting_evidence(business_date, scope)
    previous = None
    prior_date = business_date - DAY
    basis = {"date": prior_date.isoformat(), "kind": "unknown"}
    if trusted_inventory_baseline(prior_date, **scope):
        previous, _ = _load_previous_snapshot_payloads(prior_date, **scope)
        approved = (
            InventoryOpeningVersion.objects.filter(
                close__owner_id=owner_id,
                close__warehouse_id=warehouse_id,
                close__business_date=prior_date,
                status=InventoryOpeningVersion.Status.APPROVED,
            )
            .order_by("-version")
            .values("pk", "version", "close_id")
            .first()
        )
        basis = {**basis, "kind": "opening" if approved else "snapshot", **(approved or {})}
    movements, _, _, _ = _load_tx_aggregates(business_date, **scope)
    holds = _load_hold_balances(business_date, **scope)
    issues = []
    generated = False
    if (
        not was_sealed
        and previous is not None
        and holds is not None
        and not InventorySnapshotDaily.objects.filter(**scope, snapshot_date=business_date).exists()
    ):
        try:
            generate_inventory_snapshot_for_date(business_date, **scope)
            generated = True
        except ValueError as exc:
            issues.append({"issue": "generation_failed", "message": str(exc)})
    count, approx, details, totals, row_issues = _collect_rows(close, previous, movements, holds)
    claim_empty = count == 0 and (
        generated or (was_sealed and close.row_count == 0 and close.quality == Quality.TRUSTED)
    )
    audit = audit_day(business_date, scope, confirmed_empty=claim_empty)
    for finding in audit["issues"]:
        code = finding.pop("issue")
        issues.append(_issue(code, **finding))
    confirmed_empty = claim_empty and (was_sealed or (not issues and not row_issues))
    if not count and not confirmed_empty:
        quality = Quality.MISSING
        issues.append(_issue("snapshot_missing"))
    elif approx or holds is None or issues or row_issues:
        quality = Quality.APPROXIMATE
    else:
        quality = Quality.TRUSTED
    if approx:
        issues.append(_issue("approximate_source"))
    if holds is None:
        issues.append(_issue("hold_history_missing"))
    if count or claim_empty:
        issues.extend(row_issues)
    if not count and not confirmed_empty:
        # Missing snapshots convey no independent closing quantities, even in a quiet warehouse.
        for row in details:
            for field in QUANTITIES:
                row[field] = None
        for amounts in [totals, *totals["by_unit"].values()]:
            for field in QUANTITIES:
                amounts[field] = None
    elif holds is None:
        # Unrecorded reservations cannot be reconstructed from onhand movements.
        for row in details:
            for field in ("available_qty", *HELD):
                row[field] = None
        for amounts in [totals, *totals["by_unit"].values()]:
            for field in ("available_qty", *HELD):
                amounts[field] = None
    if was_sealed:
        original_basis = close.totals.get("opening_basis", {})
        if original_basis.get("kind") in ("opening", "snapshot") and original_basis != basis:
            issues.append(_issue("opening_basis_changed", previous=original_basis, current=basis))
        original_postings = close.totals.get("posting_evidence")
        if original_postings and original_postings != observed_postings:
            issues.append(
                _issue(
                    "posting_evidence_changed",
                    previous=original_postings,
                    current=observed_postings,
                )
            )
        if count != close.row_count:
            issues.append(
                _issue("sealed_rows_changed", expected_rows=close.row_count, observed_rows=count)
            )
        amended = (
            InventoryTransaction.all_objects.filter(
                **scope,
                posted_at__date=business_date,
                updated_at__gt=close.sealed_at,
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        if original_postings != observed_postings:
            for pk in amended:
                issues.append(_issue("late_posting", transaction_id=pk))
    signed = close.opening_versions.filter(status="APPROVED").order_by("-version").first()
    if signed and not opening_is_current(signed, close):
        issues.append(_issue("opening_changed", version=signed.version))
    issues.sort(key=lambda item: (item["issue"], str(item)))
    now = timezone.now()
    evidence_changed = False
    if not was_sealed:
        totals["posting_evidence"] = observed_postings
        totals["opening_basis"] = basis
        evidence_changed = (close.totals, close.details, close.quality) != (
            totals,
            details,
            quality,
        )
        close.row_count, close.details, close.totals = count, details, totals
        close.quality = quality
        if count or quality == Quality.TRUSTED:
            close.sealed_at = observed_at
    changed = close.issues != issues
    new_finding = changed and any(issue not in close.issues for issue in issues)
    if new_finding or (
        close.review_state == close.ReviewState.FINANCE and (changed or evidence_changed)
    ):
        close.review_state = close.ReviewState.WAREHOUSE
    if not close.checked_at or changed or evidence_changed:
        close.revision += 1
    if (changed or evidence_changed) and close.checked_at:
        InventoryCloseReview.objects.create(
            close=close,
            action="RECHECK",
            note=json.dumps({"原差异": close.issues, "本次核查": issues}, ensure_ascii=False),
        )
    close.issues = issues
    close.checked_at = now
    close.save()
    sync_close_alert(close)
    return close
