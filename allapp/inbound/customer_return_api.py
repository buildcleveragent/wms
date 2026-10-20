"""Customer returns: private evidence, exact quantities and explicit allowed actions."""

from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from allapp.locations.models import Location
from allapp.outbound.progress import timestamp

from . import customer_return_services as service
from .customer_return_models import CustomerReturnPhoto


def quantity(**kwargs):
    return serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal(0), **kwargs)


class LineInput(serializers.Serializer):
    id = serializers.IntegerField(required=False, min_value=1)
    original_line_id = serializers.IntegerField(min_value=1)
    qty = quantity()
    reason_code = serializers.ChoiceField(choices=service.REASONS)
    reason = serializers.CharField(max_length=500)

    def validate_qty(self, value):
        if value <= 0:
            raise serializers.ValidationError("退货数量必须大于零。")
        return value


class PhotoInput(serializers.Serializer):
    line_id = serializers.IntegerField(min_value=1)
    purpose = serializers.ChoiceField(
        choices=["APPLICATION", "RECEIPT", "NOTE"], default="APPLICATION"
    )


class BaseInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    version = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class DraftInput(BaseInput):
    original_order_id = serializers.IntegerField(min_value=1, required=False)
    lines = LineInput(many=True, allow_empty=False, max_length=200)


class ReceiptLineInput(serializers.Serializer):
    line_id = serializers.IntegerField(min_value=1)
    location_id = serializers.IntegerField(min_value=1)
    qty_ok = quantity(default=Decimal(0))
    qty_damage = quantity(default=Decimal(0))
    qty_reject = quantity(default=Decimal(0))
    batch_no = serializers.CharField(max_length=64, default="", allow_blank=True)
    production_date = serializers.DateField(default=None, allow_null=True)
    expiry_date = serializers.DateField(default=None, allow_null=True)
    serial_no = serializers.CharField(max_length=64, default="", allow_blank=True)
    reason = serializers.CharField(max_length=500, default="", allow_blank=True)
    photo_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), max_length=9, default=list
    )

    def validate_photo_ids(self, ids):
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("照片不能重复。")
        return ids


class ReceiptInput(BaseInput):
    lines = ReceiptLineInput(many=True, allow_empty=False, max_length=200)


def validate(cls, data):
    serializer = cls(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def actions(order, user):
    roles = service.scope_for(user).roles
    result = []
    owner = bool(set(roles) & {"owner_manager", "owner_salesperson"})
    if owner and user.has_perm("inbound.apply_customer_return"):
        if order.status in service.EDITABLE:
            result += ["edit", "submit", "cancel"]
        if order.status in {"OWNER_PENDING", "WHS_PENDING"}:
            result += ["withdraw", "cancel"]
    if (
        "owner_manager" in roles
        and user.has_perm("inbound.approve_customer_return")
        and order.status == "OWNER_PENDING"
    ):
        result += ["owner-approve", "owner-reject"]
    if "warehouse_manager" in roles and user.has_perm("inbound.manage_customer_return"):
        if order.status == "WHS_PENDING":
            result += ["warehouse-confirm", "warehouse-reject"]
        if order.status in {"RECEIVING", "PARTIAL"}:
            result += ["close"]
    if (
        "warehouse_operator" in roles
        and user.has_perm("inbound.receive_customer_return")
        and user.has_perm("tasking.claim_task_as_wh_operator")
    ):
        if order.assignee_id is None and order.status in {"RECEIVING", "PARTIAL"}:
            result += ["claim"]
        if order.assignee_id == user.pk:
            if order.status in {"RECEIVING", "PARTIAL"}:
                result += ["receive"]
            if any(receipt.posted_at is None for receipt in order.receipts.all()):
                result += ["retry-posting"]
    if any(
        user.has_perm(f"inbound.{p}")
        for p in [
            "apply_customer_return",
            "approve_customer_return",
            "manage_customer_return",
            "receive_customer_return",
        ]
    ):
        result += ["note"]
    return result


def photo_payload(photo):
    return dict(
        id=photo.pk,
        line_id=photo.line_id,
        purpose=photo.purpose,
        sealed=photo.sealed,
        url=f"/api/inbound/customer-returns/{photo.order_id}/photos/{photo.pk}/",
    )


def payload(order, user, detail=True):
    result = dict(
        id=order.pk,
        number=order.number,
        original_order_id=order.original_order_id,
        original_order_no=order.original_order.order_no,
        customer=order.original_order.customer.name if order.original_order.customer else "",
        warehouse=order.warehouse.name,
        status=order.status,
        status_label=order.get_status_display(),
        version=order.version,
        created_at=timestamp(order.created_at),
        updated_at=timestamp(order.updated_at),
        allowed_actions=actions(order, user),
    )
    if not detail:
        return result
    result["lines"] = []
    for line in (
        order.lines.filter(active=True)
        .select_related("product__base_uom", "original_line")
        .prefetch_related("receipts", "photos")
    ):
        records = list(line.receipts.all())
        totals = {
            key: sum((getattr(r, key) for r in records), Decimal(0))
            for key in ("qty_ok", "qty_damage", "qty_reject")
        }
        processed = sum(totals.values(), Decimal(0))
        result["lines"].append(
            dict(
                id=line.pk,
                original_line_id=line.original_line_id,
                original_line_no=line.original_line.line_no,
                product_id=line.product_id,
                product_name=line.product_name,
                product_code=line.product_code,
                spec=line.spec,
                unit=line.unit,
                unit_label=line.product.base_uom.name,
                qty=str(line.qty),
                reason_code=line.reason_code,
                reason=line.reason,
                **{k: str(v) for k, v in totals.items()},
                received_qty=str(processed),
                pending_qty=(
                    str(max(line.qty - processed, Decimal(0)))
                    if order.status not in {"CLOSED", "CANCELLED"}
                    else "0"
                ),
                photos=[photo_payload(p) for p in line.photos.all() if p.purpose == "APPLICATION"],
            )
        )
    result["receipts"] = []
    for receipt in (
        order.receipts.prefetch_related("lines__photos", "lines__location", "lines__line")
        .select_related("actor")
        .order_by("id")
    ):
        result["receipts"].append(
            dict(
                id=receipt.pk,
                created_at=timestamp(receipt.created_at),
                actor=receipt.actor.get_full_name() or receipt.actor.username,
                posted_at=timestamp(receipt.posted_at),
                posting_label=(
                    "已回补库存"
                    if receipt.posted_at
                    else "入账失败，可重试" if receipt.posting_error else "待入账"
                ),
                lines=[
                    dict(
                        id=r.pk,
                        line_id=r.line_id,
                        product_name=r.line.product_name,
                        unit=r.line.unit,
                        location=r.location.code,
                        batch_no=r.batch_no,
                        production_date=r.production_date,
                        expiry_date=r.expiry_date,
                        serial_no=r.serial_no,
                        qty_ok=str(r.qty_ok),
                        qty_damage=str(r.qty_damage),
                        qty_reject=str(r.qty_reject),
                        reason=r.reason,
                        photos=[photo_payload(p) for p in r.photos.all()],
                    )
                    for r in receipt.lines.all()
                ],
            )
        )
    result["events"] = [
        dict(
            id=e.pk,
            action=e.action,
            label=service.LABELS.get(e.action, e.action),
            reason=e.reason,
            actor=e.actor.get_full_name() or e.actor.username,
            occurred_at=timestamp(e.occurred_at),
            lines=(e.snapshot or {}).get("lines", []),
        )
        for e in order.events.select_related("actor")
    ]
    result["pending_photos"] = [
        photo_payload(p)
        for p in order.photos.filter(
            purpose="RECEIPT", actor=user, sealed=False, receipt_line__isnull=True
        )
    ]
    result["photos"] = [photo_payload(p) for p in order.photos.all() if p.purpose == "NOTE"]
    return result


class ReturnPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class ReturnEligibilityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        order = service.original_for(request.user, pk)
        rows, _ = service.allowance(order)
        return Response(
            dict(
                original_order_id=order.pk,
                original_order_no=order.order_no,
                warehouse=order.warehouse.name,
                customer=order.customer.name if order.customer else "",
                lines=rows,
                as_of=timestamp(service.timezone.now()),
            )
        )


class CustomerReturnViewSet(viewsets.GenericViewSet):
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = ReturnPagination
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        return service.queryset(self.request.user)

    def list(self, request):
        qs = self.get_queryset()
        if request.query_params.get("original_order_id"):
            try:
                qs = qs.filter(original_order_id=int(request.query_params["original_order_id"]))
            except ValueError:
                raise ValidationError("原订单编号无效。")
        search = request.query_params.get("search", "").strip()[:200]
        if search:
            qs = qs.filter(
                Q(number__icontains=search)
                | Q(original_order__order_no__icontains=search)
                | Q(lines__product_name__icontains=search)
                | Q(lines__product_code__icontains=search)
            ).distinct()
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        page = self.paginate_queryset(qs.order_by("-id"))
        return self.get_paginated_response([payload(o, request.user, False) for o in page])

    def retrieve(self, request, pk=None):
        return Response(payload(self.get_object(), request.user))

    def create(self, request):
        data = validate(DraftInput, request.data)
        if not data.get("original_order_id"):
            raise ValidationError("请选择原订单。")
        pk, _ = service.command(request.user, "create", data)
        return Response(payload(self.get_queryset().get(pk=pk), request.user), status=201)

    def partial_update(self, request, pk=None):
        data = validate(DraftInput, request.data)
        result, _ = service.command(request.user, "edit", data, pk)
        return Response(payload(self.get_queryset().get(pk=result), request.user))

    @action(
        detail=True,
        methods=["post"],
        url_path=(
            r"actions/(?P<operation>submit|withdraw|cancel|owner-approve|owner-reject|"
            r"warehouse-confirm|warehouse-reject|close|claim|note)"
        ),
    )
    def workflow(self, request, pk=None, operation=None):
        obj = self.get_object()
        if operation in {"submit", "withdraw", "cancel"}:
            service.require(
                request.user, {"owner_manager", "owner_salesperson"}, "apply_customer_return"
            )
        elif operation in {"owner-approve", "owner-reject"}:
            service.require(request.user, "owner_manager", "approve_customer_return")
        elif operation in {"warehouse-confirm", "warehouse-reject", "close"}:
            service.require(request.user, "warehouse_manager", "manage_customer_return")
        elif operation == "claim":
            service.require(request.user, "warehouse_operator", "receive_customer_return")
        if operation not in actions(obj, request.user):
            # Allow a successful request to be replayed after its transition.
            old = service.CustomerReturnCommand.objects.filter(
                actor=request.user,
                request_id=validate(BaseInput, request.data)["request_id"],
                order=obj,
            ).exists()
            if not old:
                raise service.ReturnConflict("当前角色或状态不允许此操作。")
        data = validate(BaseInput, request.data)
        result, _ = service.command(request.user, operation, data, pk)
        return Response(payload(self.get_queryset().get(pk=result), request.user))

    @action(detail=True, methods=["get", "post"])
    def receipts(self, request, pk=None):
        obj = self.get_object()
        if request.method == "GET":
            return Response(payload(obj, request.user)["receipts"])
        data = validate(ReceiptInput, request.data)
        result, receipt_id = service.command(request.user, "receive", data, pk)
        service.post_receipt(request.user, receipt_id)
        return Response(payload(self.get_queryset().get(pk=result), request.user))

    @action(detail=True, methods=["post"], url_path=r"receipts/(?P<receipt_id>\d+)/retry")
    def retry_posting(self, request, pk=None, receipt_id=None):
        obj = self.get_object()
        receipt = obj.receipts.filter(pk=receipt_id).first()
        if receipt is None:
            raise NotFound()
        data = validate(BaseInput, request.data)
        data["receipt_id"] = receipt.pk
        _, target_id = service.command(request.user, "retry-posting", data, pk)
        service.post_receipt(request.user, target_id)
        return Response(payload(self.get_queryset().get(pk=pk), request.user))

    @action(detail=True, methods=["get"], url_path="receipt-options")
    def receipt_options(self, request, pk=None):
        obj = self.get_object()
        service.operator(obj, request.user)
        return Response(
            {
                "locations": list(
                    Location.objects.filter(
                        warehouse_id=obj.warehouse_id, is_disabled=False, is_frozen=False
                    ).values("id", "code", "name")
                )
            }
        )

    @action(detail=True, methods=["post"])
    def photos(self, request, pk=None):
        initial = self.get_object()
        photo_input = validate(PhotoInput, request.data)
        image = request.FILES.get("file")
        if not image or image.size > 5 * 1024 * 1024:
            raise ValidationError("请选择不超过 5MB 的照片。")
        try:
            with Image.open(image) as source:
                source.verify()
            image.seek(0)
            with Image.open(image) as source:
                if (
                    source.format not in {"JPEG", "PNG", "WEBP"}
                    or source.width * source.height > 25_000_000
                ):
                    raise ValidationError("仅支持不超过 2500 万像素的 JPEG、PNG、WebP 图片。")
                output = BytesIO()
                source.convert("RGB").save(output, format="JPEG", quality=90)
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValidationError("图片损坏或格式无效。") from exc
        if output.tell() > 5 * 1024 * 1024:
            raise ValidationError("处理后的照片超过 5MB，请压缩后重试。")
        saved = None
        try:
            with transaction.atomic():
                obj = self.get_queryset().select_for_update().get(pk=initial.pk)
                purpose = photo_input["purpose"]
                line = obj.lines.filter(pk=photo_input["line_id"], active=True).first()
                if line is None:
                    raise ValidationError("退货商品无效。")
                if purpose == "APPLICATION":
                    service.require(
                        request.user,
                        {"owner_manager", "owner_salesperson"},
                        "apply_customer_return",
                    )
                    if obj.status not in service.EDITABLE:
                        raise service.ReturnConflict()
                elif purpose == "RECEIPT":
                    service.operator(obj, request.user)
                    if obj.status not in {"RECEIVING", "PARTIAL"}:
                        raise service.ReturnConflict()
                elif purpose == "NOTE":
                    if "note" not in actions(obj, request.user):
                        raise service.PermissionDenied()
                else:
                    raise ValidationError("照片用途无效。")
                qs = line.photos.filter(purpose=purpose)
                if purpose == "RECEIPT":
                    qs = qs.filter(sealed=False)
                if qs.count() >= 9:
                    raise ValidationError("每组最多上传 9 张照片。")
                saved = CustomerReturnPhoto.objects.create(
                    order=obj,
                    line=line,
                    purpose=purpose,
                    actor=request.user,
                    content_type="image/jpeg",
                    file=ContentFile(output.getvalue(), name="photo.jpg"),
                    sealed=purpose == "NOTE",
                )
                service.record_audit_event(
                    action="inbound.cust_return.photo",
                    module="inbound",
                    user=request.user,
                    obj=obj,
                    owner_id=obj.owner_id,
                    warehouse_id=obj.warehouse_id,
                    metadata={"photo_id": saved.pk},
                )
                if purpose == "NOTE":
                    service.event(obj, request.user, "photo", snapshot={"photo_ids": [saved.pk]})
                return Response(photo_payload(saved), status=201)
        except Exception:
            if saved:
                saved.file.delete(save=False)
            raise

    @action(detail=True, methods=["get", "delete"], url_path=r"photos/(?P<photo_id>\d+)")
    def photo(self, request, pk=None, photo_id=None):
        obj = self.get_object()
        photo = obj.photos.filter(pk=photo_id).first()
        if photo is None:
            raise NotFound()
        if request.method == "GET":
            try:
                stream = photo.file.open("rb")
            except FileNotFoundError:
                raise NotFound("照片文件暂不可用。") from None
            response = FileResponse(stream, content_type="image/jpeg")
            response["Cache-Control"] = "private, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        with transaction.atomic():
            obj = self.get_queryset().select_for_update().get(pk=obj.pk)
            photo = obj.photos.get(pk=photo.pk)
            if photo.sealed or photo.actor_id != request.user.pk:
                raise service.PermissionDenied("已提交证据不可删除。")
            if photo.purpose == "APPLICATION":
                service.require(
                    request.user, {"owner_manager", "owner_salesperson"}, "apply_customer_return"
                )
                if obj.status not in service.EDITABLE:
                    raise service.ReturnConflict()
            else:
                service.operator(obj, request.user)
            file = photo.file
            photo.delete()
            service.record_audit_event(
                action="inbound.cust_return.photo_delete",
                module="inbound",
                user=request.user,
                obj=obj,
                owner_id=obj.owner_id,
                warehouse_id=obj.warehouse_id,
            )
            transaction.on_commit(lambda: file.delete(save=False))
        return Response(status=204)
