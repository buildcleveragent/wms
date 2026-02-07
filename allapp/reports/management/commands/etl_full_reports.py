# allapp/reports/management/commands/etl_full_reports.py
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from reports.models import (
    OwnerDim, WarehouseDim, ProductDim, CustomerDim, SupplierDim, CarrierDim,
    FactInventorySnapshotDaily, FactOutboundLine, FactBilling
)
from reports.etl_utils import (
    Owner, Warehouse, Product, Customer, Supplier, Carrier,
    OutboundLine, InvDetail, BillDaily,
    ensure_datedim, date_to_key, upsert_scd2, f,
)

class Command(BaseCommand):
    help = "报表库全量装载：维度(SCD2) + 库存日快照 + 出库/计费事实"

    def add_arguments(self, parser):
        parser.add_argument("--snapdate", help="库存日快照日期 YYYY-MM-DD（必传）", required=True)
        parser.add_argument("--from", dest="dfrom", help="事实范围开始 YYYY-MM-DD（可选）")
        parser.add_argument("--to", dest="dto", help="事实范围结束 YYYY-MM-DD（可选）")

    def handle(self, *args, **opts):
        try:
            snapdate = datetime.strptime(opts["snapdate"], "%Y-%m-%d").date()
            dfrom = datetime.strptime(opts["dfrom"], "%Y-%m-%d").date() if opts.get("dfrom") else None
            dto = datetime.strptime(opts["dto"], "%Y-%m-%d").date() if opts.get("dto") else None
            if dfrom and dto and dto < dfrom:
                raise ValueError("to < from")
        except Exception as e:
            raise CommandError(f"参数错误: {e}")

        # 1) 维度装载（SCD2）
        self._load_dims()

        # 2) 库存日快照（按 snapdate）
        self._load_inventory_snapshot(snapdate)

        # 3) 出库行事实 / 计费事实（按日期范围）
        if dfrom and dto:
            self._load_outbound_fact(dfrom, dto)
            self._load_billing_fact(dfrom, dto)

        self.stdout.write(self.style.SUCCESS("FULL ETL 完成"))

    # ---------- 维度 ----------
    def _load_dims(self):
        now = timezone.now()
        # Owner
        if Owner:
            for o in Owner.objects.all().iterator(chunk_size=1000):
                upsert_scd2(
                    OwnerDim,
                    natural_key=dict(owner_id=o.pk),
                    attrs=dict(code=f(o, "code", "owner_code", default=str(o.pk)),
                               name=f(o, "name", "owner_name", default=str(o)))
                )
        # Warehouse
        if Warehouse:
            for w in Warehouse.objects.all().iterator(chunk_size=1000):
                upsert_scd2(
                    WarehouseDim,
                    natural_key=dict(warehouse_id=w.pk),
                    attrs=dict(owner_id=f(w, "owner_id", default=None) or f(w, "owner", default=None) and w.owner_id,
                               code=f(w, "code", default=str(w.pk)),
                               name=f(w, "name", default=str(w)),
                               city=f(w, "city", default=""))
                )
        # Product
        if Product:
            for p in Product.objects.all().iterator(chunk_size=1000):
                upsert_scd2(
                    ProductDim,
                    natural_key=dict(product_id=p.pk),
                    attrs=dict(owner_id=f(p, "owner_id", default=None) or f(p, "owner", default=None) and p.owner_id,
                               sku_code=f(p, "sku", "code", "sku_code", default=str(p.pk)),
                               name=f(p, "name", default=str(p)),
                               category_code=f(p, "category_code", "category_id", default=""),
                               uom=f(p, "uom", "base_uom", default="EA"),
                               net_weight_kg=f(p, "net_weight_kg", "net_weight", default=0),
                               volume_m3=f(p, "volume_m3", "volume", default=0),
                               shelf_life_days=f(p, "shelf_life_days", default=None))
                )
        # Customer
        if Customer:
            for c in Customer.objects.all().iterator(chunk_size=1000):
                upsert_scd2(
                    CustomerDim,
                    natural_key=dict(customer_id=c.pk),
                    attrs=dict(owner_id=f(c, "owner_id", default=None) or f(c, "owner", default=None) and c.owner_id,
                               code=f(c, "code", default=str(c.pk)),
                               name=f(c, "name", default=str(c)),
                               level=f(c, "level", "grade", default=""))
                )
        # Supplier
        if Supplier:
            for s in Supplier.objects.all().iterator(chunk_size=1000):
                upsert_scd2(
                    SupplierDim,
                    natural_key=dict(supplier_id=s.pk),
                    attrs=dict(owner_id=f(s, "owner_id", default=None) or f(s, "owner", default=None) and s.owner_id,
                               code=f(s, "code", default=str(s.pk)),
                               name=f(s, "name", default=str(s)))
                )
        # Carrier
        if Carrier:
            for c in Carrier.objects.all().iterator(chunk_size=1000):
                upsert_scd2(
                    CarrierDim,
                    natural_key=dict(carrier_id=c.pk),
                    attrs=dict(code=f(c, "code", default=str(c.pk)),
                               name=f(c, "name", default=str(c)))
                )

    # ---------- 库存日快照 ----------
    @transaction.atomic
    def _load_inventory_snapshot(self, d):
        if not InvDetail:
            self.stdout.write("跳过库存快照：未找到 inventory.InventoryDetail")
            return

        # 准备日期维
        dd = ensure_datedim(d)

        # 清理当日快照（幂等）
        FactInventorySnapshotDaily.objects.filter(snapshot_date=dd).delete()

        created = 0
        buf = []
        for it in InvDetail.objects.all().iterator(chunk_size=1000):
            owner_id = f(it, "owner_id") or (getattr(it, "owner", None) and it.owner_id)
            wh_id = f(it, "warehouse_id") or (getattr(it, "warehouse", None) and it.warehouse_id)
            prod_id = f(it, "product_id") or (getattr(it, "product", None) and it.product_id)

            if not (owner_id and wh_id and prod_id):
                continue  # 略过不完整记录

            # 维度映射（要求维度已装载）
            try:
                owner_dim = OwnerDim.objects.get(owner_id=owner_id, is_current=True)
                wh_dim = WarehouseDim.objects.get(warehouse_id=wh_id, is_current=True)
                prod_dim = ProductDim.objects.get(product_id=prod_id, is_current=True)
            except (OwnerDim.DoesNotExist, WarehouseDim.DoesNotExist, ProductDim.DoesNotExist):
                continue

            buf.append(FactInventorySnapshotDaily(
                snapshot_date=dd,
                owner=owner_dim, warehouse=wh_dim, location_id=f(it, "location_id", default=0),
                product=prod_dim, lot_no=f(it, "lot_no", default=""),
                qty_onhand=f(it, "qty_onhand", "onhand_qty", "on_hand", default=0) or 0,
                qty_alloc=f(it, "qty_alloc", "alloc_qty", "allocated_qty", default=0) or 0,
                qty_available=f(it, "qty_available", "available_qty", default=0) or 0,
                qty_damage=f(it, "qty_damage", "damage_qty", default=0) or 0,
                qty_expired=f(it, "qty_expired", "expired_qty", default=0) or 0,
                amount_value=f(it, "amount_value", "goods_value", default=0) or 0,
            ))
            if len(buf) >= 1000:
                FactInventorySnapshotDaily.objects.bulk_create(buf, batch_size=1000)
                created += len(buf)
                buf = []
        if buf:
            FactInventorySnapshotDaily.objects.bulk_create(buf, batch_size=1000)
            created += len(buf)
        self.stdout.write(self.style.SUCCESS(f"库存日快照 OK：{created} 行"))

    # ---------- 出库事实 ----------
    @transaction.atomic
    def _load_outbound_fact(self, dfrom, dto):
        if not OutboundLine:
            self.stdout.write("跳过出库事实：未找到 outbound.OutboundOrderLine")
            return

        # 先清已存在的范围（幂等）
        FactOutboundLine.objects.filter(order_date__date__gte=dfrom, order_date__date__lte=dto).delete()

        created = 0
        buf = []
        qs = OutboundLine.objects.all()
        # 若有日期字段则按范围筛选（尽量猜测字段）
        for name in ["order_date", "created_at", "biz_date", "date"]:
            if qs.model._meta.get_field(name, None):
                qs = qs.filter(**{f"{name}__date__gte": dfrom, f"{name}__date__lte": dto})
                break

        for ln in qs.iterator(chunk_size=1000):
            owner_id = f(ln, "owner_id") or (getattr(ln, "owner", None) and ln.owner_id)
            wh_id = f(ln, "warehouse_id") or (getattr(ln, "warehouse", None) and ln.warehouse_id)
            prod_id = f(ln, "product_id") or (getattr(ln, "product", None) and ln.product_id)
            cust_id = f(ln, "customer_id") or (getattr(ln, "customer", None) and ln.customer_id)

            if not (owner_id and wh_id and prod_id):
                continue

            try:
                owner_dim = OwnerDim.objects.get(owner_id=owner_id, is_current=True)
                wh_dim = WarehouseDim.objects.get(warehouse_id=wh_id, is_current=True)
                prod_dim = ProductDim.objects.get(product_id=prod_id, is_current=True)
            except (OwnerDim.DoesNotExist, WarehouseDim.DoesNotExist, ProductDim.DoesNotExist):
                continue

            order_dt = f(ln, "order_date", "created_at", "biz_date")
            ship_dt = f(ln, "ship_date", "shipped_at", "outbound_date")

            dd_order = ensure_datedim((order_dt.date() if hasattr(order_dt, "date") else order_dt) or dfrom)
            dd_ship = ensure_datedim(ship_dt.date()) if ship_dt else None

            buf.append(FactOutboundLine(
                line_id=ln.pk, order_id=f(ln, "order_id", default=None) or getattr(ln, "order", None) and ln.order_id or ln.pk,
                owner=owner_dim, warehouse=wh_dim, customer_id=cust_id or 0,
                product=prod_dim, order_date=dd_order, ship_date=dd_ship,
                qty_plan=f(ln, "qty_plan", "plan_qty", "quantity", default=0) or 0,
                qty_alloc=f(ln, "qty_alloc", "allocated_qty", default=0) or 0,
                qty_picked=f(ln, "qty_picked", "picked_qty", default=0) or 0,
                qty_packed=f(ln, "qty_packed", "packed_qty", default=0) or 0,
                qty_shipped=f(ln, "qty_shipped", "shipped_qty", default=0) or 0,
                sec_alloc=None, sec_pick=None, sec_pack=None, sec_ship=None,
                in_full=False, on_time=False,
            ))
            if len(buf) >= 1000:
                FactOutboundLine.objects.bulk_create(buf, batch_size=1000)
                created += len(buf)
                buf = []
        if buf:
            FactOutboundLine.objects.bulk_create(buf, batch_size=1000)
            created += len(buf)
        self.stdout.write(self.style.SUCCESS(f"出库事实 OK：{created} 行"))

    # ---------- 计费事实 ----------
    @transaction.atomic
    def _load_billing_fact(self, dfrom, dto):
        if not BillDaily:
            self.stdout.write("跳过计费事实：未找到 billing.BillingDailyRecord")
            return

        # 清理范围
        FactBilling.objects.filter(date__date__gte=dfrom, date__date__lte=dto).delete()

        created = 0
        buf = []
        qs = BillDaily.objects.filter(date__gte=dfrom, date__lte=dto)
        for br in qs.iterator(chunk_size=1000):
            owner_id = f(br, "owner_id") or (getattr(br, "owner", None) and br.owner_id)
            wh_id = f(br, "warehouse_id") or (getattr(br, "warehouse", None) and br.warehouse_id)
            dd = ensure_datedim(br.date)

            try:
                owner_dim = OwnerDim.objects.get(owner_id=owner_id, is_current=True)
            except OwnerDim.DoesNotExist:
                continue

            wh_dim = None
            if wh_id:
                wh_dim = WarehouseDim.objects.filter(warehouse_id=wh_id, is_current=True).first()

            buf.append(FactBilling(
                owner=owner_dim, warehouse=wh_dim, date=dd,
                fee_type=f(br, "fee_type", default="MIXED"),
                amount=f(br, "amount", default=0) or 0,
                dedup_key=f(br, "dedup_key", default="")
            ))
            if len(buf) >= 1000:
                FactBilling.objects.bulk_create(buf, batch_size=1000)
                created += len(buf)
                buf = []
        if buf:
            FactBilling.objects.bulk_create(buf, batch_size=1000)
            created += len(buf)
        self.stdout.write(self.style.SUCCESS(f"计费事实 OK：{created} 行"))
