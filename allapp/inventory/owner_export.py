"""Owner inventory exports reuse the displayed summary and batch grouping."""

from rest_framework.exceptions import NotFound

from allapp.core.owner_exports import bounded_rows, export_params, xlsx_download
from allapp.products.identifier_lookup import product_search_q

from .models import InventoryDetail, InventorySummary
from .owner_permissions import owner_inventory_scope
from .owner_detail import QTY_FIELDS, InventoryFilters, _base, _filtered, inventory_groups

QUANTITY_HEADERS = ["现有数量", "可用数量", "分配数量", "锁定数量", "残次数量"]
PRODUCT_HEADERS = ["商品编码", "SKU", "商品名称", "规格"]


def summary_queryset(user, search=""):
    scope = owner_inventory_scope(user)
    qs = InventorySummary.objects.filter(owner_id=scope.single_owner_id, is_active=True)
    return filter_summary(qs, search)


def filter_summary(qs, search):
    if search:
        qs = qs.filter(product_search_q(search))
    return qs.select_related("product").order_by("product_id", "id")


def export_summary(request):
    scope = owner_inventory_scope(request.user)
    params = export_params(request.query_params, {"search"})
    search = params.get("search", "").strip()
    records = bounded_rows(summary_queryset(request.user, search))
    rows = [
        [
            r.product.code,
            r.product.sku,
            r.product.name,
            r.product.spec,
            r.base_unit,
            *[getattr(r, key) for key in QTY_FIELDS],
        ]
        for r in records
    ]
    return xlsx_download(
        request,
        scope=scope,
        kind="inventory",
        title="商品库存汇总",
        headers=[*PRODUCT_HEADERS, "基本单位", *QUANTITY_HEADERS],
        rows=rows,
        filters=f"商品搜索：{search}" if search else "",
        basis="当前商品库存汇总；可用＝现有－分配－锁定－残次；数量按基本单位，不跨商品或单位合计。",
    )


def export_detail(request, summary):
    scope = owner_inventory_scope(request.user)
    if summary.owner_id != scope.single_owner_id:
        raise NotFound()
    params = export_params(request.query_params, {"warehouse_id", "batch", "expiry"})
    serializer = InventoryFilters(data=params)
    serializer.is_valid(raise_exception=True)
    groups = inventory_groups(_filtered(_base(InventoryDetail, summary), serializer.validated_data))
    records = bounded_rows(groups)
    product = summary.product
    rows = [
        [
            product.code,
            product.sku,
            product.name,
            product.spec,
            r["warehouse__name"],
            r["batch_no"] or "未记录",
            r["production_date"] or "未记录",
            r["expiry_date"] or "未记录",
            r["base_unit"] or "未记录",
            *[r[key] for key in QTY_FIELDS],
        ]
        for r in records
    ]
    labels = {"warehouse_id": "仓库编号", "batch": "批次搜索", "expiry": "效期"}
    expiry_labels = {"expired": "已过期", "valid": "未过期", "unknown": "未记录"}
    return xlsx_download(
        request,
        scope=scope,
        kind="inventory-detail",
        title="商品批次库存明细",
        headers=[
            *PRODUCT_HEADERS,
            "仓库",
            "批次",
            "生产日期",
            "到期日",
            "基本单位",
            *QUANTITY_HEADERS,
        ],
        rows=rows,
        filters="；".join(
            [f"商品：{product.code} {product.name}"]
            + [
                f"{labels[k]}：{expiry_labels.get(v, v) if k == 'expiry' else v}"
                for k, v in params.items()
            ]
        ),
        basis="按仓库、批次、生产日期、到期日及单位汇总，合并库位、容器和序列号；全零分组隐藏；不同单位不合并。",
    )
