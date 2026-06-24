from __future__ import annotations

import datetime
import io

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from allapp.core.choices import ZoneType
from allapp.products.models import Product

from .exports import (
    build_pos_stats_export_workbook,
    build_sales_export_workbook,
    build_shift_export_workbook,
)
from .models import PosPrintLog, PosReturn, PosSale, PosShift
from .serializers import (
    PosCheckoutSerializer,
    PosProductSerializer,
    PosReturnCreateSerializer,
    PosReturnReadSerializer,
    PosSaleReadSerializer,
    PosShiftCloseSerializer,
    PosShiftOpenSerializer,
    PosShiftReopenSerializer,
    serialize_checkout_result,
    serialize_return_result,
)
from .services import build_receipt, void_pos_sale
from .shift_services import (
    close_pos_shift,
    current_shift_for_user,
    open_pos_shift,
    record_print_log,
    reopen_pos_shift,
    serialize_shift,
)
from .stats import build_pos_stats_payload


class HasPosCheckoutPermission(BasePermission):
    message = "缺少 POS 收银权限。"

    def has_permission(self, request, view):
        return bool(request.user and request.user.has_perm("pos.add_possale"))


class HasPosSaleViewPermission(BasePermission):
    message = "缺少 POS 销售单查看权限。"

    def has_permission(self, request, view):
        return bool(request.user and request.user.has_perm("pos.view_possale"))


class HasPosVoidPermission(BasePermission):
    message = "缺少 POS 作废权限。"

    def has_permission(self, request, view):
        return bool(request.user and request.user.has_perm("pos.change_possale"))


class HasPosReturnPermission(BasePermission):
    message = "缺少 POS 退货/退款权限。"

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.has_perm("pos.add_posreturn")
            and request.user.has_perm("pos.add_posrefund")
        )


class HasPosShiftSupervisorPermission(BasePermission):
    message = "缺少 POS 主管权限。"

    def has_permission(self, request, view):
        return bool(request.user and request.user.has_perm("pos.change_possale"))


class QueryTokenAuthentication(JWTAuthentication):
    def authenticate(self, request):
        token = request.query_params.get("token") or request.query_params.get("access")
        if token:
            validated_token = self.get_validated_token(token)
            return self.get_user(validated_token), validated_token
        return super().authenticate(request)


class PosProductPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class PosProductListApi(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PosProductSerializer
    pagination_class = PosProductPagination

    def _scope_error(self):
        user = self.request.user
        if not getattr(user, "warehouse_id", None):
            return "当前用户未绑定仓库(warehouse)，无法查询 POS 商品。"
        zone_type = (self.request.query_params.get("zone_type") or "").strip()
        if zone_type:
            try:
                parsed_zone_type = int(zone_type)
            except ValueError:
                return "POS 商品库存范围参数无效。"
            if parsed_zone_type not in {choice.value for choice in ZoneType}:
                return "POS 商品库存范围参数无效。"
        return ""

    def list(self, request, *args, **kwargs):
        error = self._scope_error()
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        warehouse_id = getattr(user, "warehouse_id", None)
        if not warehouse_id:
            return Product.objects.none()

        queryset = Product.objects.filter(
            is_active=True,
        ).select_related("base_uom")
        search = (self.request.query_params.get("search") or "").strip()
        barcode = (self.request.query_params.get("barcode") or "").strip()

        if barcode:
            queryset = queryset.filter(
                Q(code=barcode)
                | Q(sku=barcode)
                | Q(gtin=barcode)
                | Q(unit_barcode=barcode)
                | Q(carton_barcode=barcode)
                | Q(product_package__barcode=barcode)
            )
        elif search:
            queryset = queryset.filter(
                Q(code__icontains=search)
                | Q(sku__icontains=search)
                | Q(name__icontains=search)
                | Q(gtin__icontains=search)
                | Q(unit_barcode__icontains=search)
                | Q(carton_barcode__icontains=search)
            )

        return queryset.distinct().order_by("code", "id")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["warehouse_id"] = getattr(self.request.user, "warehouse_id", None)
        zone_type = (self.request.query_params.get("zone_type") or "").strip()
        if zone_type:
            try:
                context["zone_type"] = int(zone_type)
            except ValueError:
                context["zone_type"] = None
        return context


class PosCheckoutApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosCheckoutPermission]

    def post(self, request):
        serializer = PosCheckoutSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        try:
            result = serializer.save()
        except DjangoValidationError as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            serialize_checkout_result(result, request), status=status.HTTP_201_CREATED
        )


def _validation_error_data(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    if hasattr(exc, "messages"):
        return exc.messages
    return {"detail": str(exc)}


def _sale_queryset_for_user(user):
    queryset = PosSale.objects.select_related(
        "payment", "cashier", "warehouse", "selected_customer", "shift"
    ).prefetch_related(
        "lines__product",
        "payment_lines",
        "sale_orders__outbound_order",
    )
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        return queryset.none()
    return queryset.filter(warehouse_id=warehouse_id)


def _return_queryset_for_user(user):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        return PosReturn.objects.none()
    return (
        PosReturn.objects.select_related("sale", "warehouse", "shift", "cashier")
        .prefetch_related("lines__product", "refunds")
        .filter(warehouse_id=warehouse_id)
    )


def _date_bounds(start_date, end_date):
    start_at = datetime.datetime.combine(start_date, datetime.time.min)
    end_at = datetime.datetime.combine(
        end_date + datetime.timedelta(days=1), datetime.time.min
    )
    if timezone.is_naive(start_at) and timezone.is_aware(timezone.now()):
        tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(start_at, tz)
        end_at = timezone.make_aware(end_at, tz)
    return start_at, end_at


def _parse_date(raw, name):
    try:
        return datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must use YYYY-MM-DD format.")


def _filter_sale_queryset(queryset, params):
    search = (params.get("search") or "").strip()
    status_value = (params.get("status") or "").strip().upper()
    shift_id = (params.get("shift") or params.get("shift_id") or "").strip()
    start_raw = (params.get("start_date") or "").strip()
    end_raw = (params.get("end_date") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(sale_no__icontains=search) | Q(src_bill_no__icontains=search)
        )
    if status_value:
        queryset = queryset.filter(status=status_value)
    if shift_id:
        if not shift_id.isdigit():
            raise ValueError("shift must be an integer id.")
        queryset = queryset.filter(shift_id=int(shift_id))
    if start_raw or end_raw:
        start_date = (
            _parse_date(start_raw, "start_date")
            if start_raw
            else _parse_date(end_raw, "end_date")
        )
        end_date = _parse_date(end_raw, "end_date") if end_raw else start_date
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date.")
        start_at, end_at = _date_bounds(start_date, end_date)
        queryset = queryset.filter(created_at__gte=start_at, created_at__lt=end_at)
    return queryset


def _shift_queryset_for_user(user):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        return PosShift.objects.none()
    return PosShift.objects.select_related("warehouse", "cashier").filter(
        warehouse_id=warehouse_id
    )


def _xlsx_response(workbook, filename):
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class PosSaleListApi(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]
    serializer_class = PosSaleReadSerializer
    pagination_class = PosProductPagination

    def get_queryset(self):
        return _filter_sale_queryset(
            _sale_queryset_for_user(self.request.user),
            self.request.query_params,
        ).order_by("-created_at", "-id")


class PosStatsApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request):
        try:
            payload = build_pos_stats_payload(
                user=request.user, params=request.query_params
            )
        except (DjangoValidationError, ValueError) as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(payload, status=status.HTTP_200_OK)


class PosStatsExportApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request):
        try:
            payload = build_pos_stats_payload(
                user=request.user, params=request.query_params
            )
        except (DjangoValidationError, ValueError) as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        workbook = build_pos_stats_export_workbook(payload)
        start = payload.get("period", {}).get("start_date", "start")
        end = payload.get("period", {}).get("end_date", "end")
        return _xlsx_response(workbook, f"pos-stats-{start}-{end}.xlsx")


class PosSaleExportApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request):
        try:
            queryset = _filter_sale_queryset(
                _sale_queryset_for_user(request.user), request.query_params
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        workbook = build_sales_export_workbook(queryset)
        return _xlsx_response(workbook, "pos-sales.xlsx")


class PosSaleDetailApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request, sale_id):
        sale = get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        return Response(
            PosSaleReadSerializer(sale, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class PosSaleReceiptApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request, sale_id):
        sale = get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        return Response(build_receipt(sale), status=status.HTTP_200_OK)


class PosSalePrintApi(APIView):
    authentication_classes = [QueryTokenAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request, sale_id):
        sale = get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        receipt = build_receipt(sale)
        log = record_print_log(
            user=request.user,
            print_type=PosPrintLog.PrintType.RECEIPT,
            sale=sale,
            payload=receipt,
        )
        return TemplateResponse(
            request,
            "pos/receipt_print.html",
            {"receipt": receipt, "copy_no": log.copy_no},
        )


class PosSalePrintLogApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def post(self, request, sale_id):
        sale = get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        receipt = build_receipt(sale)
        log = record_print_log(
            user=request.user,
            print_type=PosPrintLog.PrintType.RECEIPT,
            sale=sale,
            payload=receipt,
            source=PosPrintLog.Source.FRONTEND_HTML,
            remark=(request.data.get("remark") or "").strip(),
        )
        return Response(
            {"copy_no": log.copy_no, "source": log.source},
            status=status.HTTP_201_CREATED,
        )


class PosSaleVoidApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosVoidPermission]

    def post(self, request, sale_id):
        reason = (request.data.get("reason") or "").strip()
        get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        try:
            result = void_pos_sale(sale_id=sale_id, user=request.user, reason=reason)
        except DjangoValidationError as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            serialize_checkout_result(result, request), status=status.HTTP_200_OK
        )


class PosReturnListCreateApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get_permissions(self):
        if self.request.method == "POST":
            return [permissions.IsAuthenticated(), HasPosReturnPermission()]
        return super().get_permissions()

    def get(self, request):
        queryset = _return_queryset_for_user(request.user).order_by(
            "-created_at", "-id"
        )
        sale_id = (request.query_params.get("sale_id") or "").strip()
        if sale_id:
            if not sale_id.isdigit():
                return Response(
                    {"detail": "sale_id must be an integer id."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(sale_id=int(sale_id))
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(queryset, request)
        rows = PosReturnReadSerializer(page, many=True).data
        return paginator.get_paginated_response(rows)

    def post(self, request):
        serializer = PosReturnCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        try:
            result = serializer.save()
        except DjangoValidationError as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(serialize_return_result(result), status=status.HTTP_201_CREATED)


class PosReturnDetailApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request, return_id):
        return_order = get_object_or_404(
            _return_queryset_for_user(request.user), pk=return_id
        )
        return Response(
            {"return": PosReturnReadSerializer(return_order).data},
            status=status.HTTP_200_OK,
        )


class PosShiftCurrentApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosCheckoutPermission]

    def get(self, request):
        try:
            shift = current_shift_for_user(request.user)
        except DjangoValidationError as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            {"shift": serialize_shift(shift) if shift else None},
            status=status.HTTP_200_OK,
        )


class PosShiftOpenApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosCheckoutPermission]

    def post(self, request):
        serializer = PosShiftOpenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            shift = open_pos_shift(
                user=request.user,
                opening_cash_amount=serializer.validated_data.get(
                    "opening_cash_amount"
                ),
                remark=serializer.validated_data.get("remark", ""),
            )
        except DjangoValidationError as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            {"shift": serialize_shift(shift)}, status=status.HTTP_201_CREATED
        )


class PosShiftListApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request):
        queryset = _shift_queryset_for_user(request.user).order_by("-opened_at", "-id")
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(queryset, request)
        rows = [serialize_shift(shift) for shift in page]
        return paginator.get_paginated_response(rows)


class PosShiftDetailApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request, shift_id):
        shift = get_object_or_404(_shift_queryset_for_user(request.user), pk=shift_id)
        return Response({"shift": serialize_shift(shift)}, status=status.HTTP_200_OK)


class PosShiftCloseApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosCheckoutPermission]

    def post(self, request, shift_id):
        serializer = PosShiftCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            shift = close_pos_shift(
                shift_id=shift_id,
                user=request.user,
                actual_cash_amount=serializer.validated_data.get("actual_cash_amount"),
                actual_payments=serializer.validated_data.get("payments", []),
                remark=serializer.validated_data.get("remark", ""),
            )
        except DjangoValidationError as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"shift": serialize_shift(shift)}, status=status.HTTP_200_OK)


class PosShiftReopenApi(APIView):
    permission_classes = [
        permissions.IsAuthenticated,
        HasPosShiftSupervisorPermission,
    ]

    def post(self, request, shift_id):
        serializer = PosShiftReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            shift = reopen_pos_shift(
                shift_id=shift_id,
                user=request.user,
                reason=serializer.validated_data["reason"],
            )
        except DjangoValidationError as exc:
            return Response(
                _validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"shift": serialize_shift(shift)}, status=status.HTTP_200_OK)


class PosShiftPrintApi(APIView):
    authentication_classes = [QueryTokenAuthentication, SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request, shift_id):
        shift = get_object_or_404(_shift_queryset_for_user(request.user), pk=shift_id)
        payload = serialize_shift(shift)
        log = record_print_log(
            user=request.user,
            print_type=PosPrintLog.PrintType.SHIFT_SUMMARY,
            shift=shift,
            payload=payload,
        )
        return TemplateResponse(
            request,
            "pos/shift_print.html",
            {"shift": payload, "copy_no": log.copy_no},
        )


class PosShiftExportApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request, shift_id):
        shift = get_object_or_404(_shift_queryset_for_user(request.user), pk=shift_id)
        payload = serialize_shift(shift)
        workbook = build_shift_export_workbook(shift, payload)
        return _xlsx_response(workbook, f"{shift.shift_no}.xlsx")


class PosShiftListExportApi(APIView):
    permission_classes = [permissions.IsAuthenticated, HasPosSaleViewPermission]

    def get(self, request):
        queryset = _shift_queryset_for_user(request.user).order_by("-opened_at", "-id")
        sales = PosSale.objects.filter(shift__in=queryset)
        workbook = build_sales_export_workbook(sales)
        return _xlsx_response(workbook, "pos-shifts-sales.xlsx")
