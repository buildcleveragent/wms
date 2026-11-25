"""Owner-authorized register projections. Only listed columns can reach an export."""

from datetime import datetime, time, timedelta
from itertools import batched

from django.conf import settings
from django.db.models import Case, F, Q, When
from django.db.models.functions import Coalesce

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import (
    OwnerCustomerAddress,
    OwnerCustomerBinding,
    OwnerWarehouseBinding,
    Supplier,
)
from allapp.billing.models import Bill, BillLine
from allapp.core.owner_exports import BUSINESS_TZ
from allapp.inbound.arrival_models import ArrivalEvent, ArrivalPhoto
from allapp.inbound.customer_return_models import (
    CustomerReturn,
    CustomerReturnEvent,
    CustomerReturnLine,
    CustomerReturnPhoto,
    CustomerReturnReceiptLine,
)
from allapp.inbound.models import InboundOrder, InboundOrderLine, InboundReceiptLine
from allapp.inventory.models import InventoryDetail, InventorySummary, InventoryTransaction
from allapp.inventory.owner_alerts import alert_products, classify_batch, defaults
from allapp.inventory.owner_detail import QTY_FIELDS
from allapp.inventory.owner_history import stamp
from allapp.inventory.owner_statistics import unit_name
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.return_models import ReturnLine, ReturnOrder
from allapp.outbound.transfer_models import AssistedOwnershipTransferLine
from allapp.pos.models import PosReturnLine, PosSaleLine
from allapp.products.models import Product, ProductBarcode, ProductUom


def rows(qs, fields):
    """Primary-key paging also bounds MySQL driver's buffered result size."""
    last = 0
    while batch := list(qs.filter(pk__gt=last).order_by("pk").values_list("pk", *fields)[:1500]):
        for pk, *values in batch:
            yield values
        last = batch[-1][0]


def ranged(qs, field, job):
    def midnight(day):
        return datetime.combine(day, time.min, BUSINESS_TZ if settings.USE_TZ else None)

    if job.date_from:
        qs = qs.filter(**{field + "__gte": midnight(job.date_from)})
    if job.date_to:
        qs = qs.filter(**{field + "__lt": midnight(job.date_to + timedelta(days=1))})
    return qs


def masked(value):
    if not value:
        return None
    return "****" + (str(value)[-4:] if len(str(value)) > 4 else "")


def write_registers(sheet, job, owner, cutoff):
    now = stamp(cutoff)
    current = "当前快照（北京时间）" + now
    units = dict(ProductUom.all_objects.values_list("code", "name"))

    def uom(code):
        return unit_name(units.get(code, code))

    def copy(name, basis, qs, fields, headers, *, at=None, convert=None):
        target = sheet(name, headers, basis)
        if at:
            qs = ranged(qs, at, job)
        fields = fields.split()
        for values in rows(qs, fields):
            for i, (field, value) in enumerate(zip(fields, values)):
                if convert and field in convert:
                    values[i] = convert[field](value)
                elif isinstance(value, datetime):
                    values[i] = stamp(value)
                elif field in ("id", "pk") or field.endswith("_id"):
                    values[i] = str(value) if value is not None else None
            target.append(values)
        return target

    own = sheet("货主与仓库", "货主编号 货主名称 仓库编号 仓库名称 绑定有效", current)
    own.append([owner.code, owner.name, None, None, None])
    for wid, name, active in rows(
        OwnerWarehouseBinding.objects.filter(owner=owner),
        "warehouse_id warehouse__name is_active".split(),
    ):
        own.append([owner.code, owner.name, str(wid), name, active])
    copy(
        "商品档案",
        current,
        Product.all_objects.filter(owner=owner),
        "id code sku name spec gtin unit_barcode carton_barcode base_uom__name is_active is_deleted",
        "商品ID 商品编码 SKU 商品名称 规格 GTIN 单品条码 箱条码 基本单位 启用 已删除",
    )
    copy(
        "商品条码",
        current,
        ProductBarcode.all_objects.filter(owner=owner, product__owner=owner),
        "product_id product__code barcode barcode_type qty_in_base is_active",
        "商品ID 商品编码 条码 类型 基本单位数量 启用",
    )
    copy(
        "客户档案",
        current,
        OwnerCustomerBinding.all_objects.filter(owner=owner),
        "customer_id code customer__name salesperson_id customer__province customer__city customer__district customer__phone customer__mobile is_active",
        "客户ID 本货主客户编号 客户名称 业务员ID 省 市 区县 电话尾号 手机尾号 启用",
        convert={"customer__phone": masked, "customer__mobile": masked},
    )
    addresses = OwnerCustomerAddress.objects.filter(
        binding__owner=owner,
        binding__is_active=True,
        binding__is_deleted=False,
        is_active=True,
    ).filter(
        Q(binding__code__iexact="CASH", private_user_id=job.user_id)
        | (~Q(binding__code__iexact="CASH") & Q(private_user__isnull=True))
    )
    copy(
        "客户收货资料",
        current,
        addresses,
        "id binding__customer_id binding__code binding__customer__name phone is_default",
        "收货资料ID 客户ID 客户编号 客户名称 收件电话尾号 默认地址",
        convert={"phone": masked},
    )
    copy(
        "供应商档案",
        current,
        Supplier.objects.filter(owner=owner),
        "id code name province city district phone is_active",
        "供应商ID 编号 供应商名称 省 市 区县 联系电话尾号 启用",
        convert={"phone": masked},
    )
    copy(
        "货主业务员",
        current,
        UserRoleScope.objects.filter(owner=owner, role="owner_salesperson"),
        "user_id user__first_name user__last_name is_active user__is_active",
        "业务员ID 名 姓 授权有效 账号启用",
    )
    qty_fields = " ".join(QTY_FIELDS)
    qty_headers = "现有数量 可用数量 分配数量 锁定数量 残次数量"
    copy(
        "当前库存",
        current,
        InventorySummary.objects.filter(owner=owner, product__owner=owner, is_active=True),
        "product_id product__code product__name base_unit " + qty_fields,
        "商品ID 编码 名称 单位 " + qty_headers,
        convert={"base_unit": uom},
    )
    # Include physical positions so each batch balance can be traced to its stock record.
    copy(
        "仓库批次库存",
        current,
        InventoryDetail.objects.filter(owner=owner, product__owner=owner, is_active=True),
        "id product__code product__name warehouse__name location_id batch_no production_date expiry_date serial_no base_unit "
        + qty_fields,
        "库存记录ID 商品编码 名称 仓库 库位ID 批次 生产日期 到期日 序列号 单位 " + qty_headers,
        convert={"base_unit": uom},
    )
    copy(
        "库存流水",
        "库存过账时间，包含移库等内部移动",
        InventoryTransaction.objects.filter(owner=owner, product__owner=owner, is_active=True),
        "id posting_batch posted_at tx_type product_id product__code warehouse__name batch_no src_model src_id src_line_id src_no base_unit qty_delta",
        "流水ID 过账批号 入账时间 类型 商品ID 商品编码 仓库 批次 来源类型 来源ID 来源行ID 来源单号 单位 数量变动",
        at="posted_at",
        convert={"base_unit": uom},
    )
    alerts = sheet(
        "库存预警", "商品编码 商品名称 预警类别 状态 原因 仓库 批次 到期日 数量 单位", current
    )
    policy = defaults(owner.pk)
    for batch in batched(alert_products(owner.pk).iterator(chunk_size=100), 100):
        for row in classify_batch(
            batch, owner.pk, policy, cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=BUSINESS_TZ)
        ):
            p = row["product"]
            for key, finding in row["findings"].items():
                for item in finding.get("batches") or [{}]:
                    alerts.append(
                        [
                            p["code"],
                            p["name"],
                            {"low": "低库存", "expiry": "效期", "slow": "未动销"}[key],
                            "已确认" if finding["status"] == "confirmed" else "待核对",
                            finding["reason"],
                            item.get("warehouse_name"),
                            item.get("batch_no"),
                            item.get("expiry_date"),
                            item.get("onhand_qty", finding.get("available_qty")),
                            uom(item.get("base_unit", p["base_unit"])),
                        ]
                    )

    for qs, lines, label, party in [
        (InboundOrder.objects, InboundOrderLine.objects, "入库", "supplier"),
        (OutboundOrder.objects, OutboundOrderLine.objects, "出库", "customer"),
    ]:
        phone = "delivery_phone" if label == "入库" else "contact_phone"
        copy(
            label + "单据",
            "单据创建时间",
            qs.filter(owner=owner),
            f"id order_no created_at biz_date warehouse__name {party}_id created_by_id submit_status approval_status is_closed {phone}",
            "单据ID 单号 创建时间 业务日期 仓库 往来单位ID 经手人ID 提交状态 审批状态 已关闭 联系电话尾号",
            convert={phone: masked},
            at="created_at",
        )
        unit_field = "base_uom" if label == "入库" else "base_uom__name"
        detail = lines.filter(order__owner=owner, product__owner=owner)
        price = "base_price"
        extra_fields = extra_headers = ""
        if label == "出库":
            detail = detail.annotate(
                priced_unit=Case(When(is_priced=True, then=F("base_price"))),
                priced_amount=Case(When(is_priced=True, then=F("final_line_amount"))),
            )
            price = "priced_unit"
            extra_fields, extra_headers = " is_priced priced_amount", " 已计价 确认行金额"
        copy(
            label + "单明细",
            "所属订单创建时间",
            detail,
            f"order_id order__order_no id line_no product_id product__code product__name {unit_field} base_qty {price}"
            + extra_fields,
            "单据ID 单号 明细ID 行号 商品ID 商品编码 商品名称 单位 计划数量 单价" + extra_headers,
            at="order__created_at",
            convert={"base_uom": uom},
        )
    receipts = InboundReceiptLine.objects.filter(receipt__owner=owner).annotate(
        at=Coalesce("receipt__posted_at", "receipt__created_at")
    )
    copy(
        "收货验收明细",
        "按入账时间；未入账按收货记录创建时间",
        receipts,
        "receipt_id receipt__receipt_no receipt__order_no_snap id line_no product_code product_name lot_no base_uom qty_ok qty_damage qty_reject receipt__stage at",
        "收货ID 收货单号 原单号 明细ID 行号 商品编码 商品名称 批次 单位 良品 破损 拒收 状态 统计时间",
        at="at",
        convert={"base_uom": uom},
    )
    copy(
        "销售退货单",
        "退货申请创建时间",
        CustomerReturn.objects.filter(owner=owner, original_order__owner=owner),
        "id number original_order__order_no warehouse__name created_by_id status created_at",
        "退单ID 退单号 原单号 仓库 经手人ID 状态 创建时间",
        at="created_at",
    )
    copy(
        "退供与退货主单据",
        "退单创建时间",
        ReturnOrder.objects.filter(owner=owner),
        "id return_no warehouse__name recipient_type supplier_id status created_at confirmed_at reversed_at",
        "退单ID 退单号 仓库 退还对象 供应商ID 状态 创建时间 确认时间 冲销时间",
        at="created_at",
    )
    copy(
        "销售退货明细",
        "退货申请创建时间",
        CustomerReturnLine.objects.filter(order__owner=owner, order__original_order__owner=owner),
        "order_id order__number order__original_order__order_no id product_code product_name unit qty order__status order__created_at",
        "退单ID 退单号 原单号 明细ID 商品编码 商品名称 单位 申请数量 状态 创建时间",
        at="order__created_at",
        convert={"unit": uom},
    )
    copy(
        "退货验收明细",
        "验收记录创建时间",
        CustomerReturnReceiptLine.objects.filter(
            receipt__order__owner=owner, line__order_id=F("receipt__order_id")
        ),
        "receipt__order__number receipt_id id line__product_code batch_no line__unit qty_ok qty_damage qty_reject receipt__posted_at",
        "退单号 验收ID 明细ID 商品编码 批次 单位 良品 破损 拒收 入账时间",
        at="receipt__created_at",
        convert={"line__unit": uom},
    )
    copy(
        "退供与退货主",
        "退单创建时间",
        ReturnLine.objects.filter(order__owner=owner),
        "order__return_no id product_code product_name base_unit available_qty damaged_qty order__status order__confirmed_at order__reversed_at",
        "退单号 明细ID 商品编码 商品名称 单位 可用数量 残次数量 状态 确认时间 冲销时间",
        at="order__created_at",
        convert={"base_unit": uom},
    )
    copy(
        "POS销售明细",
        "销售单创建时间，仅含本货主的商品行",
        PosSaleLine.objects.filter(owner=owner, product__owner=owner),
        "sale__sale_no id line_no product_id product__code product__name product__base_uom__name qty price amount sale__status sale__created_at",
        "销售单号 明细ID 行号 商品ID 商品编码 商品名称 单位 数量 单价 行金额 状态 创建时间",
        at="sale__created_at",
    )
    copy(
        "POS退货",
        "POS退货行创建时间",
        PosReturnLine.objects.filter(
            owner=owner,
            product__owner=owner,
            sale_line__owner=owner,
            return_order__sale_id=F("sale_line__sale_id"),
        ),
        "return_order__return_no id sale_line_id product_id qty price amount created_at",
        "退单号 明细ID 原销售行ID 商品ID 数量 单价 金额 创建时间",
        at="created_at",
    )
    for direction, label, product in (
        ("source", "转出", "source_product"),
        ("target", "转入", "sales_line__product"),
    ):
        transfers = AssistedOwnershipTransferLine.objects.filter(
            **{
                "transfer__" + direction + "_owner": owner,
                product + "__owner": owner,
            }
        )
        copy(
            "货权" + label + "明细",
            "转货单创建时间",
            transfers,
            f"transfer__transfer_no id {product}__code {product}__name {product}__base_uom__name quantity transfer_price transfer__status transfer__created_at transfer__posted_at",
            "转货单号 明细ID 商品编码 商品名称 单位 转货数量 转货单价 状态 创建时间 入账时间",
            at="transfer__created_at",
        )
    approvals = sheet("审批记录", "来源 单据编号或ID 审批动作 经手人ID 时间", "按审批发生时间")
    actions = {
        "outbound.order.owner_approve",
        "outbound.order.owner_reject",
        "outbound.order.warehouse_confirm",
        "outbound.order.warehouse_approve",
        "outbound.order.warehouse_reject",
    }
    for qs, fields, kind, at in [
        (
            AuditEvent.objects.filter(
                owner=owner,
                object_type="outbound.outboundorder",
                succeeded=True,
                action__in=actions,
            ),
            "object_id action actor_id occurred_at",
            "出库订单",
            "occurred_at",
        ),
        (
            ArrivalEvent.objects.filter(order__owner=owner, action__iregex="approv|reject"),
            "order__order_no action actor_id created_at",
            "入库",
            "created_at",
        ),
        (
            CustomerReturnEvent.objects.filter(order__owner=owner, action__iregex="approv|reject"),
            "order__number action actor_id occurred_at",
            "销售退货",
            "occurred_at",
        ),
    ]:
        for number, action, actor, when in rows(ranged(qs, at, job), fields.split()):
            approvals.append([kind, number, action, str(actor) if actor else None, stamp(when)])
    bills = Bill.objects.filter(owner=owner, period__owner=owner)
    if job.date_from:
        bills = bills.filter(period__end_date__gte=job.date_from)
    if job.date_to:
        bills = bills.filter(period__start_date__lte=job.date_to)
    copy(
        "仓储账单",
        "账期与所选期间相交的整张账单",
        bills,
        "id invoice_no period__start_date period__end_date warehouse__name issue_date currency subtotal tax_total total document_status payment_status",
        "账单ID 账单号 账期开始 账期结束 仓库 开票日期 币种 税前金额 税额 总金额 单据状态 收款状态",
    )
    copy(
        "账单费用明细",
        "上述账单的全部费用行",
        BillLine.objects.filter(bill__in=bills),
        "bill_id bill__invoice_no id service_date charge_type quantity unit_price amount tax_amount bill__currency",
        "账单ID 账单号 费用行ID 服务日期 费用类型 数量 单价 金额 税额 币种",
    )
    evidence = sheet(
        "凭据索引",
        "类型 原单号 凭据ID 时间 系统内查看路径",
        "按照片创建时间；使用本账号登录查看原件",
    )
    for pk, number, order, receipt, when in rows(
        ranged(
            ArrivalPhoto.objects.filter(
                line__receipt__owner=owner, line__receipt__order__owner=owner
            ),
            "created_at",
            job,
        ),
        "id line__receipt__order__order_no line__receipt__order_id line__receipt_id created_at".split(),
    ):
        evidence.append(
            [
                "收货照片",
                number,
                str(pk),
                stamp(when),
                f"/api/inbound/orders/{order}/batches/{receipt}/photos/{pk}/",
            ]
        )
    for pk, number, order, when in rows(
        ranged(CustomerReturnPhoto.objects.filter(order__owner=owner), "created_at", job),
        "id order__number order_id created_at".split(),
    ):
        evidence.append(
            [
                "退货照片",
                number,
                str(pk),
                stamp(when),
                f"/api/inbound/customer-returns/{order}/photos/{pk}/",
            ]
        )
