# allapp/reports/management/commands/etl_incremental_reports.py
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.db.models import Q

from reports.models import (
    OwnerDim, WarehouseDim, ProductDim, FactInventoryTxn, FactOutboundLine, FactBilling
)
from reports.etl_utils import (
    InvTxn, OutboundLine, BillDaily,
    ensure_datedim, get_watermark, set_watermark, f
)

class Command(BaseCommand):
    help = "报表库增量装载：库存交易 / 出库行 / 计费事实（基于 watermark）"

    def add_arguments(self, parser):
        parser.add_argument("--since", help="覆盖起点 ISO 时间，如 2025-08-01T00:00:00（可选）")

    def handle(self, *args, **opts):
        override_since = opts.get("since")
        if override_since:
            try:
                since = datetime.fromisoformat(override_since)
            except Exception:
                since = None
        else:
            since = None

        self._load_inv_txn(since)
        self._load_ob_line(since)
        self._load_billing(since)
        self.stdout.write(self.style.SUCCESS("INCR ETL 完成"))

    # ---------- 库存交易 ----------
    @transaction.atomic
    def _load_inv_txn(self, since_override):
        if not InvTxn:
            self.stdout.write("跳过：无 inventory.InventoryTransaction")
            return
        domain = "inv_txn"
        wm = since_override or get_watermark(domain, "1970-01-01T00:00:00")
        qs = InvTxn.objects.all()
        # 猜测时间字段（occurred_at/updated_at/created_at）
        time_field = None
        for name in ["occurred_at", "updated_at", "created_at"]:
            try:
                InvTxn._meta.get_field(name)
                time_field = name
                break
            except Exception:
                continue
        if not time_field:
            self.stdout.write("库存交易：未找到时间字段，跳过")
            return

        qs = qs.filter(**{f"{time_field}__gt": wm}).order_by(time_field)

        created = 0
        for tx in qs.iterator(chunk_size=1000):
            owner_dim = OwnerDim.objects.filter(owner_id=f(tx, "owner_id") or (getattr(tx, "owner", None) and tx.owner_id), is_current=True).first()
            wh_dim = WarehouseDim.objects.filter(warehouse_id=f(tx, "warehouse_id") or (getattr(tx, "warehouse", None) and tx.warehouse_id), is_current=True).first()
            prod_dim = ProductDim.objects.filter(product_id=f(tx, "product_id") or (getattr(tx, "product", None) and tx.product_id), is_current=True).first()
            if not (owner_dim and wh_dim and prod_dim):
                continue

            FactInventoryTxn.objects.update_or_create(
                txn_id=tx.pk,
                defaults=dict(
                    occurred_at=f(tx, time_field),
                    owner=owner_dim, warehouse=wh_dim,
                    location_id=f(tx, "location_id", default=None),
                    product=prod_dim, lot_no=f(tx, "lot_no", default=""),
                    reason=f(tx, "reason_code", "reason", default=""),
                    order_type=f(tx, "order_type", default=""),
                    order_id=f(tx, "order_id", default=None),
                    qty_delta=f(tx, "qty_delta", "quantity_delta", default=0) or 0,
                    amount_delta=f(tx, "amount_delta", "value_delta", default=0) or 0,
                )
            )
            created += 1
            wm = str(f(tx, time_field).isoformat())
        set_watermark(domain, wm)
        self.stdout.write(self.style.SUCCESS(f"库存交易增量 OK：{created}"))

    # ---------- 出库行 ----------
    @transaction.atomic
    def _load_ob_line(self, since_override):
        if not OutboundLine:
            self.stdout.write("跳过：无 outbound.OutboundOrderLine")
            return
        domain = "outbound_line"
        wm = since_override or get_watermark(domain, "1970-01-01T00:00:00")

        time_field = None
        for name in ["updated_at", "created_at"]:
            try:
                OutboundLine._meta.get_field(name)
                time_field = name
                break
            except Exception:
                continue
        if not time_field:
            self.stdout.write("出库行：未找到时间字段，跳过")
            return

        qs = OutboundLine.objects.filter(**{f"{time_field}__gt": wm}).order_by(time_field)

        created = 0
        for ln in qs.iterator(chunk_size=1000):
            owner_dim = OwnerDim.objects.filter(owner_id=f(ln, "owner_id") or (getattr(ln, "owner", None) and ln.owner_id), is_current=True).first()
            wh_dim = WarehouseDim.objects.filter(warehouse_id=f(ln, "warehouse_id") or (getattr(ln, "warehouse", None) and ln.warehouse_id), is_current=True).first()
            prod_dim = ProductDim.objects.filter(product_id=f(ln, "product_id") or (getattr(ln, "product", None) and ln.product_id), is_current=True).first()
            if not (owner_dim and wh_dim and prod_dim):
                continue

            order_dt = f(ln, "order_date", "created_at")
            ship_dt = f(ln, "ship_date", "shipped_at", "outbound_date")

            FactOutboundLine.objects.update_or_create(
                line_id=ln.pk,
                defaults=dict(
                    order_id=f(ln, "order_id", default=None) or (getattr(ln, "order", None) and ln.order_id) or ln.pk,
                    owner=owner_dim, warehouse=wh_dim, customer_id=f(ln, "customer_id", default=0) or 0,
                    product=prod_dim,
                    order_date=ensure_datedim(order_dt.date() if hasattr(order_dt, "date") else order_dt),
                    ship_date=ensure_datedim(ship_dt.date()) if ship_dt else None,
                    qty_plan=f(ln, "qty_plan", "plan_qty", "quantity", default=0) or 0,
                    qty_alloc=f(ln, "qty_alloc", "allocated_qty", default=0) or 0,
                    qty_picked=f(ln, "qty_picked", "picked_qty", default=0) or 0,
                    qty_packed=f(ln, "qty_packed", "packed_qty", default=0) or 0,
                    qty_shipped=f(ln, "qty_shipped", "shipped_qty", default=0) or 0,
                    sec_alloc=None, sec_pick=None, sec_pack=None, sec_ship=None,
                    in_full=False, on_time=False,
                )
            )
            created += 1
            wm = str(getattr(ln, time_field).isoformat())
        set_watermark(domain, wm)
        self.stdout.write(self.style.SUCCESS(f"出库行增量 OK：{created}"))

    # ---------- 计费 ----------
    @transaction.atomic
    def _load_billing(self, since_override):
        if not BillDaily:
            self.stdout.write("跳过：无 billing.BillingDailyRecord")
            return
        domain = "billing_daily"
        wm = since_override or get_watermark(domain, "1970-01-01T00:00:00")

        time_field = None
        for name in ["updated_at", "created_at"]:
            try:
                BillDaily._meta.get_field(name)
                time_field = name
                break
            except Exception:
                continue
        if not time_field:
            self.stdout.write("计费：未找到时间字段，跳过")
            return

        qs = BillDaily.objects.filter(**{f"{time_field}__gt": wm}).order_by(time_field)

        created = 0
        for br in qs.iterator(chunk_size=1000):
            owner_dim = OwnerDim.objects.filter(owner_id=f(br, "owner_id") or (getattr(br, "owner", None) and br.owner_id), is_current=True).first()
            if not owner_dim:
                continue
            wh_dim = None
            wh_id = f(br, "warehouse_id") or (getattr(br, "warehouse", None) and br.warehouse_id)
            if wh_id:
                wh_dim = WarehouseDim.objects.filter(warehouse_id=wh_id, is_current=True).first()

            FactBilling.objects.update_or_create(
                owner=owner_dim, warehouse=wh_dim, date=ensure_datedim(f(br, "date")),
                fee_type=f(br, "fee_type", default="MIXED"),
                defaults=dict(amount=f(br, "amount", default=0) or 0, dedup_key=f(br, "dedup_key", default=""))
            )
            created += 1
            wm = str(getattr(br, time_field).isoformat())
        set_watermark(domain, wm)
        self.stdout.write(self.style.SUCCESS(f"计费增量 OK：{created}"))
