from urllib.parse import quote

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.baseinfo.models import Owner

from .excel_import import (
    XLSX_CONTENT_TYPE,
    InboundExcelFileError,
    build_no_order_receive_template,
    create_preview_credentials,
    load_preview_credentials,
    parse_no_order_receive_excel,
)
from .permissions import CanReceiveWithoutOrder
from .serializers import ReceiveWithoutOrderPayloadSerializer
from .services import (
    NoOrderReceiveConflict,
    no_order_items_hash,
    receive_goods_without_order,
)
from .views import IdempotencyConflict


def _resolve_scope(request, owner_id, warehouse_id=None):
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"owner_id": "必须提供有效的货主 ID"}) from exc
    scope = AccessScope.for_user(request.user)
    resolved_warehouse_id = warehouse_id or scope.single_warehouse_id
    if not resolved_warehouse_id:
        raise ValidationError("必须提供 warehouse_id；仅单一仓库范围账号可自动确定仓库")
    if not scope.allows(owner_id=owner_id, warehouse_id=resolved_warehouse_id):
        raise PermissionDenied("无权处理指定货主或仓库")
    try:
        owner = Owner.objects.get(pk=owner_id, is_active=True)
    except Owner.DoesNotExist as exc:
        raise ValidationError({"owner_id": "货主不存在或已停用"}) from exc
    return owner, int(resolved_warehouse_id)


class NoOrderReceiveImportTemplateApi(APIView):
    permission_classes = [permissions.IsAuthenticated, CanReceiveWithoutOrder]

    def get(self, request):
        owner, _warehouse_id = _resolve_scope(
            request, request.query_params.get("owner_id")
        )
        content = build_no_order_receive_template(owner)
        filename = f"{owner.code}-无订单批量入库模板.xlsx"
        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = (
            "attachment; filename=no_order_receive_template.xlsx; "
            f"filename*=UTF-8''{quote(filename)}"
        )
        response["Cache-Control"] = "private, no-store"
        return response


class NoOrderReceiveImportPreviewApi(APIView):
    permission_classes = [permissions.IsAuthenticated, CanReceiveWithoutOrder]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        owner, warehouse_id = _resolve_scope(request, request.data.get("owner_id"))
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return Response(
                {"detail": "请上传 Excel 文件，字段名为 file。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = parse_no_order_receive_excel(uploaded_file, owner=owner)
        except InboundExcelFileError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response_data = {
            "total_rows": result["total_rows"],
            "product_count": result["product_count"],
            "error_count": result["error_count"],
            "rows": result["rows"],
            "errors": result["errors"],
        }
        if result["errors"]:
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        request_id, preview_token = create_preview_credentials(
            user_id=request.user.id,
            owner_id=owner.id,
            warehouse_id=warehouse_id,
            items=result["normalized_items"],
            file_sha256=result["file_sha256"],
        )
        response_data.update(
            {
                "request_id": request_id,
                "preview_token": preview_token,
                "items": result["normalized_items"],
                "can_confirm": True,
            }
        )
        return Response(response_data, status=status.HTTP_200_OK)


class NoOrderReceiveImportConfirmApi(APIView):
    permission_classes = [permissions.IsAuthenticated, CanReceiveWithoutOrder]

    def post(self, request):
        try:
            credentials = load_preview_credentials(
                request.data.get("preview_token") or ""
            )
        except InboundExcelFileError as exc:
            raise ValidationError({"preview_token": str(exc)}) from exc
        if int(credentials.get("user_id") or 0) != request.user.id:
            raise PermissionDenied("该预览不属于当前用户")

        owner, warehouse_id = _resolve_scope(
            request,
            credentials.get("owner_id"),
            credentials.get("warehouse_id"),
        )
        request_id = request.data.get("request_id") or ""
        if request_id != credentials.get("request_id"):
            raise ValidationError({"request_id": "与预览凭证不一致"})

        serializer = ReceiveWithoutOrderPayloadSerializer(
            data={
                "request_id": request_id,
                "owner_id": owner.id,
                "warehouse_id": warehouse_id,
                "remark": "Excel批量无订单收货",
                "items": request.data.get("items"),
            }
        )
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if no_order_items_hash(payload["items"]) != credentials.get("items_hash"):
            raise ValidationError({"items": "确认内容与已校验的 Excel 预览不一致"})

        try:
            result = receive_goods_without_order(
                owner_id=owner.id,
                warehouse_id=warehouse_id,
                items=payload["items"],
                request_id=request_id,
                by_user=request.user,
                remark=payload["remark"],
                request=request,
                source="excel",
            )
        except DjangoValidationError as exc:
            detail = (
                exc.message_dict
                if hasattr(exc, "message_dict")
                else getattr(exc, "messages", [str(exc)])
            )
            raise ValidationError(detail) from exc
        except NoOrderReceiveConflict as exc:
            raise IdempotencyConflict from exc
        response_status = (
            status.HTTP_200_OK if result["idempotent"] else status.HTTP_201_CREATED
        )
        return Response(result, status=response_status)
