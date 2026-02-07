from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import F, Sum, ExpressionWrapper, DecimalField
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.tasking.models import WmsTask


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
    """拣货任务打印页（给 PDA 打开用，打印“出库单格式”）。
    MVP：用 ?token=<access> 认证（因为 window.open/openURL 不带 Authorization 头）。
    """
    token = request.GET.get("token") or request.GET.get("access")

    # 允许两种方式：
    # 1) 已有 session 登录（后台/PC）
    # 2) PDA 传 token（JWT）
    if not request.user.is_authenticated:
        if not token:
            return HttpResponse("Unauthorized: missing token", status=401)
        try:
            at = AccessToken(token)
            user_id = at.get("user_id")
            if not user_id:
                return HttpResponse("Unauthorized: invalid token", status=401)
            user = get_user_model().objects.get(id=user_id)
            request.user = user
        except Exception:
            return HttpResponse("Unauthorized: invalid token", status=401)

    # 1) 拿拣货任务
    task = (
        WmsTask.objects
        .select_related("owner", "warehouse")
        .get(id=task_id, task_type=WmsTask.TaskType.PICK)
    )

    # 2) 通过任务行的 src_id 解析出库单
    tl = (
        task.lines
        .filter(src_model="OutboundOrderLine", src_id__isnull=False)
        .order_by("id")
        .first()
    )
    if not tl:
        return HttpResponse("Bad Request: pick task has no OutboundOrderLine binding", status=400)

    try:
        ol = (
            OutboundOrderLine.objects
            .select_related("order", "order__owner", "order__customer", "order__supplier", "order__warehouse")
            .get(pk=tl.src_id)
        )
    except OutboundOrderLine.DoesNotExist:
        return HttpResponse("Bad Request: outbound order line not found", status=400)

    order: OutboundOrder = ol.order

    # 3) 订单行 + 金额（amount = base_qty * base_price）
    amount_expr = ExpressionWrapper(
        F("base_qty") * F("base_price"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    lines = (
        OutboundOrderLine.objects
        .filter(order_id=order.id)
        .select_related("product", "base_uom", "product__base_uom")
        .annotate(amount=amount_expr)
        .order_by("line_no", "id")
    )

    agg = lines.aggregate(
        total_qty=Sum("base_qty"),
        total_amount=Sum("amount"),
    )
    total_qty = agg["total_qty"] or Decimal("0")
    total_amount = agg["total_amount"] or Decimal("0")

    # 4) 打印头部字段（按你的要求）
    # 发货人：货主（不是业务员）
    sender_name = getattr(order.owner, "name", "") or ""
    sender_phone = getattr(order.owner, "phone", "") or ""

    # 收货人：客户（退供单 customer 为空时兜底 supplier）
    receiver_name = ""
    if order.customer_id:
        receiver_name = order.customer.name or ""
    elif order.supplier_id:
        receiver_name = order.supplier.name or ""

    # 电话/地址：优先用订单快照字段，更可靠
    receiver_phone = order.contact_phone or ""
    if not receiver_phone and order.customer_id:
        receiver_phone = order.customer.phone or ""
    receiver_address = order.ship_to or ""

    # 发货时间：优先仓库确认时间，其次预计发货时间(etd)
    ship_time = order.approved_at_warehouse or order.etd or timezone.now()

    total_amount_upper = _money_to_rmb_upper(total_amount)

    return render(request, "outbound/print/pick_task.html", {
        "object": order,            # ✅ 模板里 object 现在就是 OutboundOrder
        "lines": lines,             # ✅ 明细是 OutboundOrderLine（带 base_price / amount）
        "ship_time": ship_time,
        "sender_name": sender_name,
        "sender_phone": sender_phone,
        "receiver_name": receiver_name,
        "receiver_phone": receiver_phone,
        "receiver_address": receiver_address,
        "total_qty": total_qty,
        "total_amount": total_amount,
        "total_amount_upper": total_amount_upper,
    })



from django.http import HttpResponse
from django.shortcuts import render
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken

from allapp.tasking.models import WmsTask, WmsTaskLine
#
# from decimal import Decimal
# from django.http import HttpResponse
# from django.shortcuts import render
# from django.contrib.auth import get_user_model
# from django.utils import timezone
# from rest_framework_simplejwt.tokens import AccessToken
#
# from allapp.tasking.models import WmsTask, WmsTaskLine
# from allapp.outbound.models import OutboundOrder, OutboundOrderLine
#
#
# def pick_task_print(request, task_id: int):
#     """
#     拣货任务打印页（给 PDA 打开用）
#     MVP：用 ?token=<access> 认证（因为 window.open/openURL 不带 Authorization 头）
#     """
#     token = request.GET.get("token") or request.GET.get("access")
#
#     # 允许两种方式：
#     # 1) 已有 session 登录（后台/PC）
#     # 2) PDA 传 token（JWT）
#     if not request.user.is_authenticated:
#         if not token:
#             return HttpResponse("Unauthorized: missing token", status=401)
#         try:
#             at = AccessToken(token)
#             user_id = at.get("user_id")
#             if not user_id:
#                 return HttpResponse("Unauthorized: invalid token", status=401)
#             user = get_user_model().objects.get(id=user_id)
#             request.user = user
#         except Exception:
#             return HttpResponse("Unauthorized: invalid token", status=401)
#
#     task = (
#         WmsTask.objects
#         .select_related("owner", "warehouse")
#         .get(id=task_id, task_type=WmsTask.TaskType.PICK)
#     )
#
#     # 任务行（如果你打印用它）
#     task_lines = (
#         WmsTaskLine.objects
#         .filter(task_id=task.id)
#         .select_related("product__base_uom", "from_location")
#         .order_by("id")
#     )
#
#     # --- 关键：找到对应 OutboundOrder ---
#     order = None
#
#     # 1) 优先用 task.source_pk（你 outbound/services.py 已经 canonical 写了 source_model=outboundorder, source_pk=str(order.pk)）
#     if task.source_pk:
#         try:
#             order = (
#                 OutboundOrder.objects
#                 .select_related("owner", "warehouse", "created_by", "approved_by_warehouse")
#                 .get(pk=task.source_pk)
#             )
#         except OutboundOrder.DoesNotExist:
#             order = None
#
#     # 2) 兜底：从 taskline.src_id 找 OutboundOrderLine -> order
#     if order is None:
#         tl = (
#             WmsTaskLine.objects
#             .filter(task_id=task.id, src_model="OutboundOrderLine", src_id__isnull=False)
#             .order_by("id")
#             .first()
#         )
#         if tl:
#             try:
#                 ol = (
#                     OutboundOrderLine.objects
#                     .select_related("order", "order__owner", "order__warehouse", "order__created_by", "order__approved_by_warehouse")
#                     .get(pk=tl.src_id)
#                 )
#                 order = ol.order
#             except OutboundOrderLine.DoesNotExist:
#                 order = None
#
#     if order is None:
#         return HttpResponse("Bad Request: cannot resolve outbound order for this pick task", status=400)
#
#     # --- 要打印的“头部字段” ---
#     receiver_name = order.contact or ""
#     receiver_phone = order.contact_phone or ""
#     receiver_address = order.ship_to or ""
#
#     # 发货时间（你可以按业务选：仓库确认时间/预计发货时间/订单日期）
#     ship_time = order.approved_at_warehouse or order.etd or timezone.now()
#
#     sender_user = order.approved_by_warehouse or order.created_by or request.user
#     sender_name = getattr(sender_user, "name", "") or str(sender_user)
#     sender_phone = getattr(sender_user, "phone", "") or ""
#
#     # 如果你模板需要合计（按任务行 qty_plan 合计）
#     total_qty = Decimal("0")
#     for l in task_lines:
#         total_qty += (l.qty_plan or Decimal("0"))
#
#     return render(request, "outbound/print/pick_task.html", {
#         # ✅ object 改成 order：模板里可直接用 object.order_no / object.owner.name
#         "object": order,
#
#         # ✅ 明细你可以继续用任务行（qty_plan），也可以改成订单行（base_qty）
#         "lines": task_lines,
#
#         # ✅ 额外字段单独传给模板
#         "ship_time": ship_time,
#         "receiver_name": receiver_name,
#         "receiver_phone": receiver_phone,
#         "receiver_address": receiver_address,
#         "sender_name": sender_name,
#         "sender_phone": sender_phone,
#         "total_qty": total_qty,
#     })




# def pick_task_print(request, task_id: int):
#     """
#     拣货任务打印页（给 PDA 打开用）
#     MVP：用 ?token=<access> 认证（因为 window.open/openURL 不带 Authorization 头）
#     """
#     token = request.GET.get("token") or request.GET.get("access")
#
#     # 允许两种方式：
#     # 1) 已有 session 登录（后台/PC）
#     # 2) PDA 传 token（JWT）
#     if not request.user.is_authenticated:
#         if not token:
#             return HttpResponse("Unauthorized: missing token", status=401)
#         try:
#             at = AccessToken(token)
#             user_id = at.get("user_id")
#             if not user_id:
#                 return HttpResponse("Unauthorized: invalid token", status=401)
#             user = get_user_model().objects.get(id=user_id)
#             request.user = user
#         except Exception:
#             return HttpResponse("Unauthorized: invalid token", status=401)
#
#     task = (
#         WmsTask.objects
#         .select_related("owner", "warehouse")
#         .get(id=task_id, task_type=WmsTask.TaskType.PICK)
#     )
#
#     lines = (
#         WmsTaskLine.objects
#         .filter(task_id=task.id)
#         .select_related("product", "from_location")
#         .order_by("id")
#     )
#
#     return render(request, "outbound/print/pick_task.html", {"object": task, "lines": lines})
