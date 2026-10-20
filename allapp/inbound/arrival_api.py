"""Versioned APIs shared by the owner portal and PDA ordered receiving."""

from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from allapp.baseinfo.models import Supplier
from allapp.baseinfo.owner_warehouse_access import owner_warehouse_queryset
from allapp.locations.models import Location
from allapp.products.models import Product

from . import arrival_services as svc
from .arrival_models import ArrivalPhoto
from .arrival_posting import post_batch
from .models import InboundOrder, InboundReceiptLine
from .views import InboundOrderViewSet

TZ = ZoneInfo("Asia/Shanghai")


def stamp(value):
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, TZ)
    return value.astimezone(TZ).isoformat()


class CommandInput(serializers.Serializer):
    request_id = serializers.CharField(min_length=8, max_length=64)
    version = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(max_length=500, allow_blank=True, default="")
    approve_overage = serializers.BooleanField(default=False)


class OrderLineInput(serializers.Serializer):
    id = serializers.IntegerField(min_value=1, required=False)
    product_id = serializers.IntegerField(min_value=1)
    base_qty = serializers.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    base_price = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"), default=Decimal("0")
    )
    note = serializers.CharField(max_length=200, allow_blank=True, default="")


class OrderInput(CommandInput):
    supplier_id = serializers.IntegerField(min_value=1)
    warehouse_id = serializers.IntegerField(min_value=1)
    biz_date = serializers.DateField()
    eta = serializers.DateTimeField(allow_null=True, default=None, default_timezone=TZ)
    delivery_contact = serializers.CharField(max_length=80, allow_blank=True, default="")
    delivery_phone = serializers.CharField(max_length=40, allow_blank=True, default="")
    vehicle_no = serializers.CharField(max_length=30, allow_blank=True, default="")
    delivery_note = serializers.CharField(max_length=500, allow_blank=True, default="")
    src_bill_no = serializers.CharField(max_length=60, allow_blank=True, default="")
    delivery_method = serializers.CharField(max_length=30, allow_blank=True, default="")
    lines = OrderLineInput(many=True, allow_empty=False)

    def validate_eta(self, value):
        return (
            timezone.make_naive(value, TZ)
            if value and not settings.USE_TZ and timezone.is_aware(value)
            else value
        )


class ReceiptLineInput(serializers.Serializer):
    id = serializers.IntegerField(min_value=1, required=False)
    order_line_id = serializers.IntegerField(min_value=1)
    location_id = serializers.IntegerField(min_value=1)
    qty_ok = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0"), default=Decimal("0")
    )
    qty_damage = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0"), default=Decimal("0")
    )
    qty_reject = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0"), default=Decimal("0")
    )
    lot_no = serializers.CharField(max_length=60, allow_blank=True, default="")
    mfg_date = serializers.DateField(allow_null=True, default=None)
    exp_date = serializers.DateField(allow_null=True, default=None)
    serial_no = serializers.CharField(max_length=64, allow_blank=True, default="")
    damage_reason = serializers.CharField(max_length=500, allow_blank=True, default="")
    reject_reason = serializers.CharField(max_length=500, allow_blank=True, default="")


class PutawayInput(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    location_id = serializers.IntegerField(min_value=1)
    qty = serializers.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))


class BatchInput(CommandInput):
    lines = ReceiptLineInput(many=True, required=False, allow_empty=False)
    putaway_lines = PutawayInput(many=True, required=False, allow_empty=False)


def validated(cls, request):
    serializer = cls(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def actions(user, order, scope=None):
    role = (scope or svc.scope_for(user)).roles
    result = []
    if order.receipt_mode != "BATCH" or order.is_closed:
        return result
    if "owner_salesperson" in role and user.has_perm("inbound.submit_as_owner_buyers"):
        if order.submit_status == "DRAFT":
            result += ["edit", "submit"]
        elif order.approval_status in {"OWNER_PENDING", "OWNER_APPROVED", "WHS_PENDING"}:
            result += ["withdraw"]
    if (
        "owner_manager" in role
        and user.has_perm("inbound.approve_as_owner_manager")
        and order.approval_status == "OWNER_PENDING"
    ):
        result += ["owner-approve", "owner-reject"]
    if "warehouse_manager" in role and user.has_perm("inbound.manage_arrival"):
        if order.approval_status in {"OWNER_APPROVED", "WHS_PENDING"} and user.has_perm(
            "inbound.approve_as_wh_manager"
        ):
            result += ["warehouse-confirm", "warehouse-reject"]
        if order.approval_status == "WHS_APPROVED" and not order.receiving_closed_at:
            result += ["close", "batch-create"]
    if (
        "warehouse_operator" in role
        and user.has_perm("tasking.claim_task_as_wh_operator")
        and order.approval_status == "WHS_APPROVED"
        and not order.receiving_closed_at
    ):
        result += ["batch-create"]
    return result


def brief(user, order, scope=None):
    label = (
        "已完成"
        if order.is_closed
        else (
            "等待后续到货"
            if order.approval_status == "WHS_APPROVED"
            else order.get_approval_status_display()
        )
    )
    if order.receiving_closed_at and not order.is_closed:
        label = "收货已结束，待入账／上架"
    if order.submit_status == "DRAFT" and order.approval_status == "NOT_READY":
        label = "草稿"
    return {
        "id": order.pk,
        "order_no": order.order_no,
        "version": order.version,
        "receipt_mode": order.receipt_mode,
        "supplier_id": order.supplier_id,
        "supplier_name": order.supplier.name,
        "warehouse_id": order.warehouse_id,
        "warehouse_name": order.warehouse.name,
        "biz_date": str(order.biz_date),
        "eta": stamp(order.eta),
        "src_bill_no": order.src_bill_no or "",
        "status_label": label,
        "approval_status": order.approval_status,
        "submit_status": order.submit_status,
        "is_closed": order.is_closed,
        "receiving_closed_at": stamp(order.receiving_closed_at),
        "reason": order.workflow_reason,
        "allowed_actions": actions(user, order, scope),
    }


def payload(user, order):
    result = brief(user, order)
    result.update(
        {
            name: getattr(order, name) or ""
            for name in (
                "delivery_contact",
                "delivery_phone",
                "delivery_method",
                "vehicle_no",
                "delivery_note",
            )
        }
    )
    result["as_of"] = stamp(timezone.now())
    receipts = list(
        order.receipts.filter(
            is_deleted=False, owner_id=order.owner_id, warehouse_id=order.warehouse_id
        )
        .exclude(stage="LEGACY")
        .order_by("id")
        .select_related("task", "putaway_task", "made_by", "approved_by")
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=InboundReceiptLine.objects.select_related(
                    "location", "product__base_uom"
                ).prefetch_related("arrival_photos"),
            ),
            "putaway_task__lines__from_location",
            "putaway_task__lines__to_location",
            "task__assignments",
            "putaway_task__assignments",
        )
    )
    totals = svc.order_quantities(order, receipts)
    putaway = {}
    for receipt in receipts:
        if receipt.putaway_task_id and receipt.putaway_task.posting_status == "POSTED":
            by_id = {r.pk: r for r in receipt.lines.all()}
            for line in receipt.putaway_task.lines.all():
                original = by_id.get(line.src_id)
                if original and line.status != "CANCELLED":
                    key = original.order_line_id
                    putaway[key] = putaway.get(key, svc.ZERO) + line.qty_done
    result["lines"] = []
    for row in order.lines.filter(is_deleted=False).select_related("product__base_uom"):
        v = totals[row.pk]
        handled = v["ok"] + v["damage"] + v["reject"]
        gap = max(svc.ZERO, row.base_qty - handled)
        result["lines"].append(
            {
                "id": row.pk,
                "product_id": row.product_id,
                "product_name": row.product.name,
                "product_code": row.product.code,
                "spec": row.product.spec or "",
                "unit": row.product.base_uom.name,
                "base_unit": row.base_uom,
                "batch_control": row.product.batch_control,
                "expiry_control": row.product.expiry_control,
                "serial_control": row.product.serial_control,
                "base_qty": str(row.base_qty),
                "base_price": str(row.base_price),
                "note": row.note or "",
                "pending_qty": str(v["pending"]),
                "qty_ok": str(v["ok"]),
                "qty_damage": str(v["damage"]),
                "qty_reject": str(v["reject"]),
                "waiting_qty": str(
                    max(svc.ZERO, gap - v["pending"]) if not order.receiving_closed_at else svc.ZERO
                ),
                "short_qty": str(gap if order.receiving_closed_at else svc.ZERO),
                "over_qty": str(max(svc.ZERO, handled - row.base_qty)),
                "posted_qty": str(v["posted"]),
                "putaway_qty": str(putaway.get(row.pk, svc.ZERO)),
            }
        )
    role = svc.scope_for(user).roles
    manager = "warehouse_manager" in role and user.has_perm("inbound.manage_arrival")
    reviewer = manager and user.has_perm("tasking.taskconfirm_as_wh_manager")
    operator = "warehouse_operator" in role and user.has_perm("tasking.claim_task_as_wh_operator")
    result["batches"] = []
    for receipt in receipts:
        assigned = [a.assignee_id for a in receipt.task.assignments.all() if not a.finished_at]
        pa = receipt.putaway_task
        pa_assigned = (
            [a.assignee_id for a in pa.assignments.all() if not a.finished_at] if pa else []
        )
        allowed = []
        if operator:
            if receipt.stage in {"DRAFT", "REJECTED"}:
                if not assigned and receipt.task.status == "RELEASED":
                    allowed += ["claim"]
                if user.pk in assigned:
                    allowed += ["batch-edit", "batch-submit"]
            if pa and pa.status in {"RELEASED", "IN_PROGRESS"}:
                if not pa_assigned and pa.status == "RELEASED":
                    allowed += ["putaway-claim"]
                if user.pk in pa_assigned:
                    allowed += ["putaway-record"]
        if manager:
            if reviewer and receipt.stage == "PENDING" and receipt.made_by_id != user.pk:
                allowed += ["batch-approve", "batch-reject"]
            if receipt.stage == "APPROVED" or (
                pa and pa.review_status == "APPROVED" and pa.posting_status != "POSTED"
            ):
                allowed += ["retry-posting"]
            if (
                reviewer
                and pa
                and pa.review_status == "PENDING"
                and pa.status == "COMPLETED"
                and user.pk not in pa_assigned
            ):
                allowed += ["putaway-approve", "putaway-reject"]
        # Operators see only claimable or assigned batches, never other operators' evidence.
        if operator and not (allowed or receipt.made_by_id == user.pk or user.pk in pa_assigned):
            continue
        rows = []
        for row in receipt.lines.all():
            rows.append(
                {
                    "id": row.pk,
                    "order_line_id": row.order_line_id,
                    "product_name": row.product_name,
                    "unit": svc.receipt_unit(row),
                    "qty_ok": str(row.qty_ok),
                    "qty_damage": str(row.qty_damage),
                    "qty_reject": str(row.qty_reject),
                    "lot_no": row.lot_no or "",
                    "mfg_date": str(row.mfg_date) if row.mfg_date else None,
                    "exp_date": str(row.exp_date) if row.exp_date else None,
                    "serial_no": row.serial_no,
                    "location_id": row.location_id,
                    "location_name": row.location.code if row.location else "未记录",
                    "damage_reason": row.damage_reason,
                    "reject_reason": row.reject_reason,
                    "photos": [
                        {
                            "id": p.pk,
                            "sealed": p.sealed,
                            "url": f"/api/inbound/orders/{order.pk}/batches/{receipt.pk}/photos/{p.pk}/",
                        }
                        for p in row.arrival_photos.all()
                    ],
                }
            )
        result["batches"].append(
            {
                "id": receipt.pk,
                "number": receipt.receipt_no,
                "version": receipt.version,
                "stage": receipt.stage,
                "reason": receipt.reason,
                "task_id": receipt.task_id,
                "putaway_task_id": receipt.putaway_task_id,
                "posting_error": receipt.posting_error,
                "submitted_at": stamp(receipt.submitted_at),
                "approved_at": stamp(receipt.approved_at),
                "posted_at": stamp(receipt.posted_at),
                "allowed_actions": allowed,
                "can_append_evidence": manager or (operator and receipt.made_by_id == user.pk),
                "lines": rows,
                "putaway_status": pa.posting_status if pa else None,
                "putaway_lines": (
                    [
                        {
                            "id": task_line.pk,
                            "product_name": next(
                                (
                                    r.product_name
                                    for r in receipt.lines.all()
                                    if r.pk == task_line.src_id
                                ),
                                "待核对",
                            ),
                            "unit": next(
                                (
                                    svc.receipt_unit(r)
                                    for r in receipt.lines.all()
                                    if r.pk == task_line.src_id
                                ),
                                "",
                            ),
                            "quality": task_line.plan_meta.get("quality"),
                            "qty_plan": str(task_line.qty_plan),
                            "qty_done": str(task_line.qty_done),
                            "location_id": task_line.to_location_id,
                            "from_location": (
                                task_line.from_location.code
                                if task_line.from_location
                                else "未记录"
                            ),
                            "to_location": (
                                task_line.to_location.code if task_line.to_location else "未记录"
                            ),
                        }
                        for task_line in pa.lines.all()
                    ]
                    if pa
                    else []
                ),
            }
        )
    events = order.arrival_events.select_related("actor").order_by("id")
    if operator:
        events = events.filter(
            Q(receipt_id__in=[r["id"] for r in result["batches"]]) | Q(receipt__isnull=True)
        )
    result["events"] = [
        {
            "id": e.pk,
            "action": svc.LABELS.get(e.action, "处理记录"),
            "receipt_id": e.receipt_id,
            "actor": e.actor.get_full_name() or e.actor.username if e.actor else "未记录",
            "time": stamp(e.created_at),
            "reason": e.reason,
            "lines": (
                e.snapshot.get("lines", [])
                if e.action in {"batch-submit", "putaway-record"}
                else []
            ),
        }
        for e in events
    ]
    result["locations"] = (
        list(
            Location.objects.filter(
                warehouse_id=order.warehouse_id,
                is_disabled=False,
                is_frozen=False,
                is_deleted=False,
            ).values("id", "code")
        )
        if manager or operator
        else []
    )
    result["no_stock_required"] = bool(
        order.receipt_mode == "BATCH"
        and order.receiving_closed_at
        and all(r.stage in {"NO_STOCK", "CANCELLED"} for r in receipts)
    )
    result["legacy_notice"] = (
        "历史订单未记录分批验收，请按原收货流程处理；未记录数量不作推测。"
        if order.receipt_mode != "BATCH"
        else ""
    )
    return result


class ArrivalOrderViewSet(InboundOrderViewSet):
    """Extend existing paths; legacy clients retain their established read/write contract."""

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            exc = ValidationError(getattr(exc, "message_dict", None) or exc.messages)
        return super().handle_exception(exc)

    def arrival_request(self):
        return self.request.query_params.get("owner_portal") == "1"

    def get_queryset(self):
        if not self.arrival_request():
            return super().get_queryset().filter(is_deleted=False)
        qs = svc.orders_for(self.request.user).filter(inbound_type="PURCHASE")
        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(order_no__icontains=search)
                | Q(src_bill_no__icontains=search)
                | Q(supplier__name__icontains=search)
                | Q(lines__product__name__icontains=search)
                | Q(lines__product__code__icontains=search)
            ).distinct()
        state = self.request.query_params.get("state", "")
        if state == "CLOSED":
            qs = qs.filter(is_closed=True)
        elif state == "POSTING_FAILED":
            qs = qs.filter(receipts__posting_error=True).distinct()
        elif state == "RECEIPT_PENDING":
            qs = qs.filter(receipts__stage="PENDING").distinct()
        elif state == "PUTAWAY_PENDING":
            qs = qs.filter(
                receipts__putaway_task__review_status="PENDING",
                receipts__putaway_task__status="COMPLETED",
            ).distinct()
        elif state == "DRAFT":
            qs = qs.filter(submit_status="DRAFT", approval_status="NOT_READY", is_closed=False)
        elif state:
            if state not in dict(InboundOrder.APPROVAL_CHOICES):
                raise ValidationError("无效状态。")
            qs = qs.filter(approval_status=state, is_closed=False)
        for param, lookup in (("date_from", "biz_date__gte"), ("date_to", "biz_date__lte")):
            if self.request.query_params.get(param):
                value = serializers.DateField().run_validation(self.request.query_params[param])
                qs = qs.filter(**{lookup: value})
        return qs.order_by("-biz_date", "-pk")

    def list(self, request, *args, **kwargs):
        if not self.arrival_request():
            return super().list(request, *args, **kwargs)
        page = self.paginate_queryset(self.get_queryset())
        scope = svc.scope_for(request.user)
        return self.get_paginated_response([brief(request.user, o, scope) for o in page])

    def retrieve(self, request, *args, **kwargs):
        if self.arrival_request():
            return Response(payload(request.user, svc.order_for(request.user, kwargs["pk"])))
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if request.data.get("receipt_mode") != "BATCH":
            return super().create(request, *args, **kwargs)
        result = svc.save_order(request.user, validated(OrderInput, request))
        return Response(
            payload(request.user, svc.order_for(request.user, result["order_id"])), status=201
        )

    def partial_update(self, request, pk=None):
        result = svc.save_order(request.user, validated(OrderInput, request), pk=int(pk))
        return Response(payload(request.user, svc.order_for(request.user, result["order_id"])))

    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        return Response(payload(request.user, svc.order_for(request.user, pk)))

    @action(detail=True, methods=["post"], url_path=r"actions/(?P<operation>[^/.]+)")
    def arrival_action(self, request, pk=None, operation=None):
        result = svc.order_action(
            request.user, int(pk), operation, validated(CommandInput, request)
        )
        return Response(payload(request.user, svc.order_for(request.user, result["order_id"])))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"batches/(?P<receipt_id>\d+)/actions/(?P<operation>[^/.]+)",
    )
    def batch_action(self, request, pk=None, receipt_id=None, operation=None):
        data = validated(BatchInput, request)
        if operation == "batch-edit" and "lines" not in data:
            raise ValidationError("请填写本次验收明细。")
        result = svc.batch_action(request.user, int(pk), int(receipt_id), operation, data)
        if operation in {"batch-approve", "putaway-approve", "retry-posting"}:
            post_batch(request.user, result["receipt_id"], putaway=result["post_putaway"])
        return Response(payload(request.user, svc.order_for(request.user, pk)))

    @action(detail=False, methods=["get"], url_path="arrival-options")
    def arrival_options(self, request):
        scope = svc.scope_for(request.user)
        svc.orders_for(request.user)
        if not scope.roles.intersection({"owner_salesperson", "owner_manager"}):
            raise PermissionDenied()
        warehouses = owner_warehouse_queryset(scope.single_owner_id)
        search = request.query_params.get("search", "").strip()
        products = Product.objects.filter(
            owner_id=scope.single_owner_id, is_deleted=False, is_active=True
        ).select_related("base_uom")
        if search:
            products = products.filter(
                Q(name__icontains=search) | Q(code__icontains=search) | Q(sku__icontains=search)
            )
        page = self.paginate_queryset(products.order_by("code", "id"))
        result = self.get_paginated_response(
            [
                {
                    "id": p.pk,
                    "name": p.name,
                    "code": p.code,
                    "spec": p.spec or "",
                    "unit": p.base_uom.name,
                    "purchase_price": str(p.purchase_price) if p.purchase_price else None,
                }
                for p in page
            ]
        ).data
        result.update(
            {
                "warehouses": list(warehouses.values("id", "name")),
                "suppliers": list(
                    Supplier.objects.filter(
                        owner_id=scope.single_owner_id, is_deleted=False, is_active=True
                    ).values("id", "name")
                ),
            }
        )
        return Response(result)

    @action(detail=True, methods=["post"], url_path=r"batches/(?P<receipt_id>\d+)/photos")
    def upload_photo(self, request, pk=None, receipt_id=None):
        order = svc.order_for(request.user, pk)
        receipt = svc.receipt_for(request.user, order, receipt_id)
        scope = svc.scope_for(request.user)
        if "warehouse_operator" in scope.roles:
            svc.require(request.user, {"warehouse_operator"}, "tasking.claim_task_as_wh_operator")
            if receipt.made_by_id != request.user.pk:
                raise PermissionDenied()
        else:
            svc.require(request.user, {"warehouse_manager"}, "inbound.manage_arrival")
        file = request.FILES.get("file")
        if not file or file.size > 5 * 1024 * 1024:
            raise ValidationError("图片不得超过 5MB。")
        try:
            with Image.open(file) as im:
                if im.format not in {"JPEG", "PNG", "WEBP"} or im.width * im.height > 25000000:
                    raise ValueError()
                im.load()
                output = BytesIO()
                im.convert("RGB").save(output, format="JPEG", quality=90)
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ValidationError("仅支持真实 JPEG、PNG、WebP 图片。") from None
        data = validated(CommandInput, request)
        data["line_id"] = serializers.IntegerField(min_value=1).run_validation(
            request.data.get("line_id")
        )
        import hashlib

        data["digest"] = hashlib.sha256(output.getvalue()).hexdigest()
        saved = []

        def operation():
            locked = svc.order_for(request.user, pk, lock=True)
            batch = svc.receipt_for(request.user, locked, receipt_id, lock=True)
            svc.check_version(batch, data.get("version"))
            row = batch.lines.filter(pk=data["line_id"], is_deleted=False).first()
            if not row:
                raise NotFound()
            if row.arrival_photos.count() >= 9:
                raise ValidationError("每条验收明细最多 9 张照片。")
            photo = ArrivalPhoto(
                line=row, actor=request.user, sealed=batch.stage not in {"DRAFT", "REJECTED"}
            )
            photo.file.save("evidence.jpg", ContentFile(output.getvalue()), save=False)
            saved.append(photo.file.name)
            photo.save()
            svc.event(locked, request.user, "photo", receipt=batch, snapshot={"photo_id": photo.pk})
            svc.bump(batch)
            return {"photo_id": photo.pk}

        try:
            svc.command(request.user, data, "photo", [int(pk), int(receipt_id)], operation)
        except Exception:
            for name in saved:
                ArrivalPhoto._meta.get_field("file").storage.delete(name)
            raise
        return Response(payload(request.user, svc.order_for(request.user, pk)), status=201)

    @action(
        detail=True,
        methods=["get", "delete"],
        url_path=r"batches/(?P<receipt_id>\d+)/photos/(?P<photo_id>\d+)",
    )
    def photo(self, request, pk=None, receipt_id=None, photo_id=None):
        order = svc.order_for(request.user, pk)
        receipt = svc.receipt_for(request.user, order, receipt_id)
        photo = ArrivalPhoto.objects.filter(pk=photo_id, line__receipt=receipt).first()
        if not photo:
            raise NotFound()
        if request.method == "GET":
            response = FileResponse(photo.file.open("rb"), content_type="image/jpeg")
            response["Cache-Control"] = "private, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        svc.require(request.user, {"warehouse_operator"}, "tasking.claim_task_as_wh_operator")
        data = validated(CommandInput, request)

        def operation():
            locked_order = svc.order_for(request.user, pk, lock=True)
            batch = svc.receipt_for(request.user, locked_order, receipt_id, lock=True)
            svc.check_version(batch, data.get("version"))
            if (
                batch.stage not in {"DRAFT", "REJECTED"}
                or photo.sealed
                or photo.actor_id != request.user.pk
            ):
                raise svc.ArrivalConflict("已提交证据不能删除。")
            name = photo.file.name
            photo.delete()
            transaction.on_commit(lambda: ArrivalPhoto._meta.get_field("file").storage.delete(name))
            svc.event(locked_order, request.user, "note", receipt=batch, reason="删除未提交照片")
            svc.bump(batch)
            return {"order_id": int(pk)}

        svc.command(request.user, data, "delete-photo", int(photo_id), operation)
        return Response(payload(request.user, svc.order_for(request.user, pk)))
