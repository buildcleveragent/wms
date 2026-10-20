"""Read-only owner stock alerts based on current stock and provable posted facts."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.pos.models import PosSaleLine, PosSaleOrder
from allapp.products.models import Product
from allapp.tasking.models import WmsTask

from .models import (
    InventoryDetail,
    InventorySummary,
    InventoryTransaction,
    OwnerInventoryAlertPolicy,
    ProductInventoryAlertPolicy,
)
from .owner_detail import BUSINESS_TZ, QTY_FIELDS, iso

CATEGORIES = {"low": "低库存", "expiry": "临期／已过期", "slow": "长期未动销"}
DEFAULTS = dict(
    low_enabled=True, expiry_enabled=True, slow_enabled=True, expiry_days=30, slow_days=90
)
ZERO = Decimal(0)
BATCH_SIZE = 100


def defaults(owner_id):
    policy = OwnerInventoryAlertPolicy.objects.filter(owner_id=owner_id).first()
    return {k: getattr(policy, k, v) for k, v in DEFAULTS.items()}


def effective(product, policy, override=None):
    result = {
        "low": {
            "enabled": policy["low_enabled"]
            and product.is_active
            and product.min_stock is not None,
            "threshold": str(product.min_stock) if product.min_stock is not None else None,
            "source": "商品最低库存",
        }
    }
    for key in ("expiry", "slow"):
        mode = getattr(override, f"{key}_mode", "inherit")
        days = getattr(override, f"{key}_days", None) if mode == "custom" else policy[f"{key}_days"]
        result[key] = {
            "enabled": policy[f"{key}_enabled"] and mode != "off",
            "days": days,
            "source": (
                "商品自定义" if mode == "custom" else "商品关闭" if mode == "off" else "货主默认"
            ),
        }
    return result


def product_info(product):
    return {
        "id": product.pk,
        "name": product.name,
        "code": product.code,
        "sku": product.sku,
        "spec": product.spec or "",
        "base_unit": product.base_uom.code,
        "unit_label": product.base_uom.name,
        "is_active": product.is_active,
    }


def alert_products(owner_id):
    """Keep stopped products with physical stock visible for expiry and idle-stock alerts."""
    stock = InventoryDetail.objects.filter(
        owner_id=owner_id, product_id=OuterRef("pk"), is_active=True, onhand_qty__gt=0
    )
    return (
        Product.objects.filter(owner_id=owner_id)
        .alias(has_stock=Exists(stock))
        .filter(Q(is_active=True) | Q(has_stock=True))
        .select_related("base_uom")
        .order_by("pk")
    )


def source(model, pk, app=""):
    parts = (model or "").lower().split(".")
    name = parts[-1]
    namespaces = {"wmstask": "tasking", "outboundorder": "outbound", "possaleline": "pos"}
    if len(parts) > 2 or name not in namespaces:
        return None
    expected = namespaces[name]
    if len(parts) == 2 and parts[0] != expected:
        return None
    if app and app.lower() != expected and not (app.lower() == "pos" and name == "outboundorder"):
        return None
    try:
        number = int(pk)
        return (name, number) if number > 0 else None
    except (TypeError, ValueError):
        return None


def sales_evidence(rows, owner_id):
    """Classify ISSUEs in batches without returning document identities to the caller."""
    candidates = [r for r in rows if r.tx_type == "ISSUE" and not r.pair_id]
    roots = {r.pk: source(r.src_model, r.src_id) for r in candidates}
    tasks = {}
    pending = {
        (s[1], r.warehouse_id) for r in candidates if (s := roots[r.pk]) and s[0] == "wmstask"
    }
    # A bounded traversal fails closed for unresolved deep or cyclic historical sources.
    for _ in range(32):
        pending -= tasks.keys()
        if not pending:
            break
        edges = Q(pk__in=[])
        for task_id, warehouse_id in pending:
            edges |= Q(pk=task_id, warehouse_id=warehouse_id)
        found = {
            (t.pk, t.warehouse_id): t
            for t in WmsTask.objects.filter(edges, owner_id=owner_id, is_active=True).exclude(
                status="CANCELLED"
            )
        }
        tasks.update({key: found.get(key) for key in pending})
        pending = set()
        for t in found.values():
            parent = source(t.source_model, t.source_pk, t.source_app)
            if parent and parent[0] == "wmstask":
                pending.add((parent[1], t.warehouse_id))
    pos_required = set()
    for row in candidates:
        s = roots[row.pk]
        seen = set()
        while s and s[0] == "wmstask":
            if s in seen:
                s = None
                break
            seen.add(s)
            t = tasks.get((s[1], row.warehouse_id))
            if not t:
                s = None
                break
            if (t.source_app or "").lower() == "pos":
                pos_required.add(row.pk)
            s = source(t.source_model, t.source_pk, t.source_app)
        roots[row.pk] = s
    order_ids = {s[1] for s in roots.values() if s and s[0] == "outboundorder"}
    orders = {o.pk: o for o in OutboundOrder.objects.filter(pk__in=order_ids, owner_id=owner_id)}
    products = set(
        OutboundOrderLine.objects.filter(order_id__in=orders).values_list("order_id", "product_id")
    )
    bindings = {
        b.outbound_order_id: b
        for b in PosSaleOrder.objects.filter(
            outbound_order_id__in=orders, owner_id=owner_id
        ).select_related("sale")
    }
    pos_ids = {s[1] for s in roots.values() if s and s[0] == "possaleline"}
    pos_lines = {
        p.pk: p
        for p in PosSaleLine.objects.filter(pk__in=pos_ids, owner_id=owner_id).select_related(
            "sale"
        )
    }
    result = {}
    for row in candidates:
        s = roots[row.pk]
        value = None
        if s and s[0] == "outboundorder":
            order = orders.get(s[1])
            if (
                order
                and order.warehouse_id == row.warehouse_id
                and (order.pk, row.product_id) in products
            ):
                binding = bindings.get(order.pk)
                if binding and binding.sale.warehouse_id == row.warehouse_id:
                    value = binding.sale.status == "COMPLETED" and order.outbound_type == "SALES"
                elif row.pk not in pos_required and not binding:
                    value = order.outbound_type == "SALES"
        elif s and s[0] == "possaleline":
            line = pos_lines.get(s[1])
            if (
                line
                and line.product_id == row.product_id
                and line.sale.warehouse_id == row.warehouse_id
            ):
                value = line.sale.status == "COMPLETED"
        result[row.pk] = value
    return result


def stock_history(rows, onhand, base_unit, sales, now, days):
    """Reconcile a complete ledger before claiming continuous possession or no sales."""
    unknown = {"status": "needs_review", "reason": "库存历史、单位或过账时间无法核对"}
    if not rows:
        return unknown
    pairs = defaultdict(list)
    for row in rows:
        if not row.posted_at or row.base_unit != base_unit:
            return unknown
        if timezone.is_naive(row.posted_at):
            row.posted_at = row.posted_at.replace(tzinfo=BUSINESS_TZ)
        if row.pair_id and row.tx_type in {"MOVE_IN", "MOVE_OUT", "RECEIVE", "ISSUE"}:
            pairs[row.pair_id].append(row)
        if row.posted_at > now:
            return unknown
    ignored = set()
    for pair in pairs.values():
        if len(pair) != 2:
            return unknown
        a, b = pair
        if (
            {a.tx_type, b.tx_type} not in ({"MOVE_IN", "MOVE_OUT"}, {"RECEIVE", "ISSUE"})
            or a.qty_delta + b.qty_delta != 0
            or any(
                getattr(a, k) != getattr(b, k)
                for k in ("base_unit", "batch_no", "production_date", "expiry_date", "posted_at")
            )
        ):
            return unknown
        ignored.update((a.pk, b.pk))
    balance = ZERO
    start = None
    last_sale = None
    uncertain_issues = []
    for row in sorted(rows, key=lambda r: (r.posted_at, r.pk)):
        if row.pk in ignored:
            continue
        previous = balance
        balance += row.qty_delta
        if balance < 0:
            return unknown
        if previous == 0 and balance > 0:
            start = row.posted_at
        if balance == 0:
            start = None
        if row.tx_type == "ISSUE":
            if sales.get(row.pk) is True:
                last_sale = max(last_sale, row.posted_at) if last_sale else row.posted_at
            elif sales.get(row.pk) is None:
                uncertain_issues.append(row.posted_at)
    if balance != onhand or start is None:
        return unknown
    observed = max(start, last_sale) if last_sale else start
    cutoff = now - timedelta(days=days)
    if any(t >= min(observed, cutoff) for t in uncertain_issues):
        return {"status": "needs_review", "reason": "观察期内存在来源待核对的出库流水"}
    return {
        "status": "confirmed" if observed <= cutoff else "normal",
        "reason": "长期未发生销售出库" if last_sale else "持有库存已达观察期，尚无有效销售出库",
        "last_sale_at": iso(last_sale),
        "stock_since": iso(start),
        "observed_since": iso(observed),
        "inactive_days": (now - observed).days,
    }


def classify_batch(products, owner_id, policy, now):
    ids = [p.pk for p in products]
    overrides = {
        p.product_id: p for p in ProductInventoryAlertPolicy.objects.filter(product_id__in=ids)
    }
    summary = dict(
        InventorySummary.objects.filter(
            owner_id=owner_id, product_id__in=ids, is_active=True
        ).values_list("product_id", "pk")
    )
    sums = {k: Sum(k) for k in QTY_FIELDS}
    dims = (
        "product_id",
        "warehouse_id",
        "warehouse__name",
        "batch_no",
        "production_date",
        "expiry_date",
        "base_unit",
    )
    groups = defaultdict(list)
    for row in (
        InventoryDetail.objects.filter(owner_id=owner_id, product_id__in=ids, is_active=True)
        .values(*dims)
        .annotate(**sums)
        .order_by()
    ):
        groups[row["product_id"]].append(row)
    slow_ids = [
        p.pk
        for p in products
        if effective(p, policy, overrides.get(p.pk))["slow"]["enabled"]
        and any(r["onhand_qty"] > 0 for r in groups[p.pk])
    ]
    ledger = defaultdict(list)
    transactions = list(
        InventoryTransaction.objects.filter(
            owner_id=owner_id, product_id__in=slow_ids, is_active=True
        )
    )
    for tx in transactions:
        ledger[tx.product_id].append(tx)
    sales = sales_evidence(transactions, owner_id) if transactions else {}
    today = now.astimezone(BUSINESS_TZ).date()
    for product in products:
        threshold = effective(product, policy, overrides.get(product.pk))
        units = defaultdict(lambda: {k: ZERO for k in QTY_FIELDS})
        for row in groups[product.pk]:
            for key in QTY_FIELDS:
                units[row["base_unit"]][key] += row[key] or ZERO
        mismatch = any(u != product.base_uom.code and any(q.values()) for u, q in units.items())
        totals = units[product.base_uom.code]
        base = {
            "product": product_info(product),
            "summary_id": summary.get(product.pk),
            "thresholds": threshold,
            "quantities": [
                {"base_unit": u, **{k: str(v) for k, v in q.items()}}
                for u, q in sorted(units.items())
            ],
            "unit_mismatch": mismatch,
        }
        findings = {}
        if threshold["low"]["enabled"]:
            if mismatch:
                findings["low"] = {
                    "status": "needs_review",
                    "reason": "库存单位与商品基本单位不一致",
                }
            elif totals["available_qty"] <= product.min_stock:
                findings["low"] = {
                    "status": "confirmed",
                    "reason": "全仓可用库存不高于商品最低库存",
                    "available_qty": str(totals["available_qty"]),
                }
        if threshold["expiry"]["enabled"]:
            batches = []
            unknown_expiry = False
            for row in groups[product.pk]:
                if row["onhand_qty"] <= 0:
                    continue
                if row["expiry_date"] is None:
                    unknown_expiry = True
                    continue
                remaining = (row["expiry_date"] - today).days
                if remaining <= threshold["expiry"]["days"]:
                    batches.append(
                        {
                            "warehouse_id": row["warehouse_id"],
                            "warehouse_name": row["warehouse__name"],
                            "batch_no": row["batch_no"],
                            "production_date": row["production_date"],
                            "expiry_date": row["expiry_date"],
                            "base_unit": row["base_unit"],
                            "onhand_qty": str(row["onhand_qty"]),
                            "remaining_days": remaining,
                            "status": "expired" if remaining < 0 else "expiring",
                        }
                    )
            batches.sort(key=lambda b: (b["remaining_days"], b["warehouse_id"], b["batch_no"]))
            if batches or unknown_expiry:
                findings["expiry"] = {
                    "status": "confirmed" if batches else "needs_review",
                    "reason": "存在临期或已过期库存" if batches else "效期未记录",
                    "batches": batches,
                    "missing_expiry": unknown_expiry,
                }
        if threshold["slow"]["enabled"] and any(q["onhand_qty"] > 0 for q in units.values()):
            finding = (
                stock_history(
                    ledger[product.pk],
                    totals["onhand_qty"],
                    product.base_uom.code,
                    sales,
                    now,
                    threshold["slow"]["days"],
                )
                if not mismatch
                else {"status": "needs_review", "reason": "库存单位与商品基本单位不一致"}
            )
            if finding["status"] != "normal":
                findings["slow"] = finding
        yield {**base, "findings": findings}


def alert_response(owner_id, filters=None):
    """Counts and selected page share exactly the same classification, without a count cap."""
    policy = defaults(owner_id)
    now = timezone.now()
    if timezone.is_naive(now):
        now = now.replace(tzinfo=BUSINESS_TZ)
    qs = alert_products(owner_id)
    if filters and filters.get("search"):
        q = filters["search"]
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(sku__icontains=q))
    counts = dict.fromkeys(CATEGORIES, 0)
    review = dict.fromkeys(CATEGORIES, 0)
    selected = []
    matched = 0
    offset = (filters["page"] - 1) * filters["page_size"] if filters else 0
    # Expiry ordering must be global across batches, so retain only sortable page candidates.
    limit = offset + filters["page_size"] if filters else 0

    def consume(batch):
        nonlocal matched, selected
        for row in classify_batch(batch, owner_id, policy, now):
            for key, finding in row["findings"].items():
                if finding["status"] == "confirmed":
                    counts[key] += 1
                if finding["status"] == "needs_review" or finding.get("missing_expiry"):
                    review[key] += 1
            if not filters:
                continue
            key = filters["category"]
            finding = row["findings"].get(key)
            if not finding:
                continue
            needs_review = finding["status"] == "needs_review" or finding.get("missing_expiry")
            if filters["status"] == "needs_review":
                if not needs_review:
                    continue
            elif finding["status"] != "confirmed":
                continue
            if key == "expiry" and filters["expiry"] != "all":
                finding = {
                    **finding,
                    "batches": [b for b in finding["batches"] if b["status"] == filters["expiry"]],
                }
                if not finding["batches"]:
                    continue
            matched += 1
            selected.append({**row, **finding})
            selected.sort(
                key=lambda r: (
                    (
                        min((b["remaining_days"] for b in r.get("batches", [])), default=100000),
                        r["product"]["id"],
                    )
                    if key == "expiry"
                    else (r["product"]["id"],)
                )
            )
            selected = selected[:limit]

    batch = []
    for product in qs.iterator(chunk_size=BATCH_SIZE):
        batch.append(product)
        if len(batch) == BATCH_SIZE:
            consume(batch)
            batch = []
    if batch:
        consume(batch)
    payload = {
        "as_of": iso(now),
        "scope_label": "本货主全仓库存",
        "settings": policy,
        "categories": [
            {
                "code": key,
                "label": label,
                "count": counts[key],
                "needs_review_count": review[key],
                "enabled": policy[f"{key}_enabled"],
            }
            for key, label in CATEGORIES.items()
        ],
        "notes": [
            "各类按商品去重，同一商品可能属于多个类别。",
            "可用＝现有－分配－锁定－残次。",
            "未动销按真实销售出库判断，不代表各批次库龄。",
        ],
    }
    if filters:
        payload.update(
            category=filters["category"],
            count=matched,
            results=selected[offset:],
            next_page=filters["page"] + 1 if offset + filters["page_size"] < matched else None,
        )
    return payload
