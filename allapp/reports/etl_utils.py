# allapp/reports/etl_utils.py
from datetime import date, datetime, timedelta
from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    DateDim, OwnerDim, WarehouseDim, ProductDim, CustomerDim, SupplierDim, CarrierDim,
    FactInventorySnapshotDaily, FactOutboundLine, FactBilling, FactInventoryTxn,
    AggThroughputDaily, AggBillingDaily, EtlWatermark
)

# -------------------------
# 通用工具
# -------------------------
def date_to_key(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day

def ensure_datedim(d: date) -> DateDim:
    key = date_to_key(d)
    obj, _ = DateDim.objects.get_or_create(
        date_key=key,
        defaults=dict(
            date=d,
            year=d.year,
            quarter=((d.month - 1)//3) + 1,
            month=d.month,
            day=d.day,
            week=d.isocalendar().week,
            is_month_start=(d.day == 1),
            is_month_end=((d + timedelta(days=1)).day == 1),
            is_weekend=(d.weekday() >= 5),
        )
    )
    return obj

def _get_model(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None

# -------------------------
# SCD2 装载：若维度快照变化则关前开后
# natural_key: dict（如 {"owner_id": 1}）
# attrs: dict（当前最新属性快照）
# -------------------------
@transaction.atomic
def upsert_scd2(model, natural_key: dict, attrs: dict):
    now = timezone.now()
    current = model.objects.filter(is_current=True, **natural_key).first()
    if current:
        # 比较是否有变化（排除 SCD2 管理字段）
        changed = False
        for k, v in attrs.items():
            if getattr(current, k, None) != v:
                changed = True
                break
        if not changed:
            return current, False  # 无变化
        # 关闭旧版本
        model.objects.filter(pk=current.pk).update(is_current=False, valid_to=now)
    # 开新版本
    obj = model.objects.create(**natural_key, **attrs, valid_from=now, valid_to=None, is_current=True)
    return obj, True

# -------------------------
# 取业务模型（存在即用）
# -------------------------
Owner = _get_model('baseinfo', 'Owner')
Warehouse = _get_model('locations', 'Warehouse')
Product = _get_model('products', 'Product')
Customer = _get_model('baseinfo', 'Customer') or _get_model('customers', 'Customer')
Supplier = _get_model('baseinfo', 'Supplier') or _get_model('suppliers', 'Supplier')
Carrier = _get_model('baseinfo', 'Carrier') or _get_model('tms', 'Carrier')

InboundLine = _get_model('inbound', 'InboundOrderLine')
OutboundLine = _get_model('outbound', 'OutboundOrderLine')

InvDetail = _get_model('inventory', 'InventoryDetail')
InvTxn = _get_model('inventory', 'InventoryTransaction')

BillDaily = _get_model('billing', 'BillingDailyRecord')

# -------------------------
# 字段安全读取（支持你代码里不同命名）
# -------------------------
def f(obj, *candidates, default=None):
    for name in candidates:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default

# -------------------------
# Watermark 辅助（domain: str）
# -------------------------
def get_watermark(domain: str, default_value: str = "") -> str:
    wm, _ = EtlWatermark.objects.get_or_create(domain=domain, defaults={"watermark_value": default_value})
    return wm.watermark_value

def set_watermark(domain: str, value: str):
    EtlWatermark.objects.update_or_create(domain=domain, defaults={"watermark_value": value})
