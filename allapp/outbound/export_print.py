from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render

from allapp.accounts.audit import record_audit_event
from allapp.core.models import PrintConfig
from allapp.core.print_tickets import user_from_print_ticket
from allapp.core.printing import normalize_print_css
from allapp.outbound.print_documents import build_document, document_queryset
from allapp.pos.models import PosReceiptWarehouseInfo
from allapp.tasking.models import WmsTask

OUTBOUND_PRINT_CSS_DEFAULTS = {
    "page_size_css": "9.5in 3.6667in",
    "page_margin": "5mm",
    "sheet_width": "100%",
    "sheet_padding_top": "0",
    "sheet_padding_right": "0",
    "sheet_padding_bottom": "0",
    "sheet_padding_left": "0",
    "font_family": "Microsoft YaHei, Arial, sans-serif",
    "body_font_size": "13px",
    "company_font_size": "22px",
    "title_font_size": "22px",
    "meta_font_size": "13px",
    "table_font_size": "13px",
    "table_header_font_size": "13px",
    "money_font_size": "13px",
    "footer_font_size": "12px",
    "body_line_height": "1.4",
    "meta_line_height": "1.4",
    "table_line_height": "18px",
    "money_line_height": "18px",
    "footer_line_height": "1.4",
    "table_cell_padding": "6px 6px",
}


def _money_to_rmb_upper(amount: Decimal) -> str:
    """极简人民币大写（MVP 用）。amount 需要是 >=0 的 Decimal。"""
    try:
        amt = Decimal(amount).quantize(Decimal("0.01"))
    except Exception:
        return ""
    if amt < 0:
        return ""
    if amt == 0:
        return "零元整"

    digit = "零壹贰叁肆伍陆柒捌玖"
    unit1 = ["", "拾", "佰", "仟"]
    unit2 = ["", "万", "亿", "兆"]
    s = f"{amt:.2f}"
    integer, frac = s.split(".")

    def four_to_cn(n4: str) -> str:
        out = ""
        zero = False
        for i, ch in enumerate(n4.zfill(4)):
            d = int(ch)
            pos = 3 - i
            if d == 0:
                zero = True
                continue
            if zero and out:
                out += "零"
            zero = False
            out += digit[d] + unit1[pos]
        return out

    groups = []
    while integer:
        groups.insert(0, integer[-4:])
        integer = integer[:-4]

    cn_int = ""
    for gi, g in enumerate(groups):
        part = four_to_cn(g)
        if part:
            cn_int += part + unit2[len(groups) - 1 - gi]
    cn_int = (cn_int or "零") + "元"

    jiao = int(frac[0])
    fen = int(frac[1])
    if jiao == 0 and fen == 0:
        return cn_int + "整"
    out = cn_int
    if jiao:
        out += digit[jiao] + "角"
    elif fen:
        out += "零"
    if fen:
        out += digit[fen] + "分"
    return out


def pick_task_print(request, task_id: int):
    """Print a scoped picking list or a delivery note backed by successful posting."""
    token = request.GET.get("ticket")
    # PDA windows use a short-lived, document-scoped ticket; admin sessions remain supported.
    if not request.user.is_authenticated:
        if not token:
            return HttpResponse("Unauthorized: missing token", status=401)
        try:
            user = user_from_print_ticket(token, request.path)
            request.user = user
        except Exception:
            return HttpResponse("Unauthorized: invalid token", status=401)

    can_print = bool(
        request.user.is_superuser
        or request.user.has_perm("tasking.view_wmstask")
        or request.user.has_perm("tasking.claim_task_as_wh_operator")
        or request.user.has_perm("tasking.taskconfirm_as_wh_manager")
    )
    if not can_print:
        return HttpResponse("Forbidden", status=403)

    # 1) 拿拣货任务。打印内容包含价格、电话和地址，因此无论兼容开关
    # 如何配置，打印端点都始终使用 fail-closed 的任务范围。
    base = WmsTask.objects.select_related("owner", "warehouse").filter(
        task_type=WmsTask.TaskType.PICK
    )
    allowed = document_queryset(base, request.user)
    try:
        task = allowed.get(id=task_id)
    except WmsTask.DoesNotExist:
        return HttpResponse("Not Found", status=404)

    kind = request.GET.get("kind", "delivery")
    if kind not in {"pick", "delivery"}:
        return HttpResponse("kind 必须为 pick 或 delivery。", status=400)
    try:
        order, lines = build_document(task, kind)
    except ValidationError as exc:
        return HttpResponse("；".join(exc.messages), status=409)

    # 拣货单用于库内作业，不依赖送货单地址配置。
    warehouse_infos = []
    selected_info = None
    if kind == "delivery":
        # 与 POS 销售单共用仓库信息，按实际出库仓库限定可选范围。
        warehouse_infos = list(
            PosReceiptWarehouseInfo.objects.filter(is_active=True)
            .filter(Q(warehouse_id=order.warehouse_id) | Q(warehouse__isnull=True))
            .order_by("warehouse_id", "-is_default", "sort_order", "id")
        )
        warehouse_infos = [info for info in warehouse_infos if (info.address or "").strip()]
        if not warehouse_infos:
            return HttpResponse("请先补充当前仓库的有效打印地址配置，再补打送货单。", status=409)
        selected_info_id = request.GET.get("warehouse_info_id", "")
        if selected_info_id:
            selected_info = next(
                (info for info in warehouse_infos if str(info.pk) == selected_info_id), None
            )
            if selected_info is None:
                return HttpResponse("所选仓库信息不可用，请重新选择。", status=400)
        elif len(warehouse_infos) == 1:
            selected_info = warehouse_infos[0]

    total_qty = sum(
        (line["base_qty"] if kind == "pick" else line.base_qty for line in lines), Decimal(0)
    )
    total_amount = (
        sum((line.amount or Decimal(0) for line in lines), Decimal(0))
        if kind == "delivery"
        else Decimal(0)
    )
    unpriced_line_count = sum(not line.is_priced for line in lines) if kind == "delivery" else 0
    all_unpriced = bool(lines) and unpriced_line_count == len(lines)

    # 4) 打印头部字段（按你的要求）
    # 发货人：货主（不是业务员）
    sender_name = getattr(order.owner, "name", "") or ""
    sender_phone = getattr(order.owner, "phone", "") or ""

    # 收货人优先使用下单时保存的联系人快照。散客/一件代发订单的客户档案
    # 是共享客户，不能用客户名称覆盖本单实际收件人。
    receiver_name = (order.contact or "").strip()
    if not receiver_name and order.customer_id:
        receiver_name = order.customer.name or ""
    elif not receiver_name and order.supplier_id:
        receiver_name = order.supplier.name or ""

    # 电话/地址：优先用订单快照字段，更可靠
    receiver_phone = order.contact_phone or ""
    if not receiver_phone and order.customer_id:
        receiver_phone = order.customer.phone or ""
    is_pickup = order.delivery_method == "PICKUP"
    receiver_address = ""
    if not is_pickup:
        receiver_address = (order.ship_to or "").strip()
        if not receiver_address and order.customer_id:
            receiver_address = "".join(
                (getattr(order.customer, field, "") or "").strip()
                for field in ("province", "city", "district", "street", "address")
            )

    # 正式送货单记录实际过账时间。
    ship_time = task.posted_at

    total_amount_upper = _money_to_rmb_upper(total_amount)
    print_config = PrintConfig.get_default(PrintConfig.Module.OUTBOUND)
    print_css = normalize_print_css(print_config, OUTBOUND_PRINT_CSS_DEFAULTS)
    record_audit_event(
        action="outbound.pick.print",
        module="outbound",
        request=request,
        obj=task,
        metadata={
            "order_id": order.id,
            "kind": kind,
            "warehouse_info_id": selected_info.pk if selected_info else None,
        },
    )

    return render(
        request,
        "outbound/print/pick_task.html",
        {
            "task": task,
            "kind": kind,
            "document_title": "拣货单" if kind == "pick" else "送货单",
            "customer_name": (order.customer.name or "").strip() if order.customer_id else "",
            "warehouse_name": task.warehouse.name,
            "warehouse_phone": selected_info.phone if selected_info else "",
            "document_ready": kind == "pick" or selected_info is not None,
            "object": order,
            "lines": lines,
            "ship_time": ship_time,
            "sender_name": sender_name,
            "sender_phone": sender_phone,
            "receiver_name": receiver_name,
            "receiver_phone": receiver_phone,
            "receiver_address": receiver_address,
            "is_pickup": is_pickup,
            "warehouse_address": selected_info.address if selected_info else "",
            "warehouse_infos": warehouse_infos,
            "selected_warehouse_info": selected_info,
            "auto_print": len(warehouse_infos) <= 1 or selected_info is not None,
            "total_qty": total_qty,
            "total_amount": total_amount,
            "total_amount_upper": total_amount_upper,
            "unpriced_line_count": unpriced_line_count,
            "all_unpriced": all_unpriced,
            "print_config": print_config,
            "print_css": print_css,
        },
    )
