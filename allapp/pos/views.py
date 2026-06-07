from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.products.models import Product

from .models import PosSale
from .serializers import (
    PosCheckoutSerializer,
    PosProductSerializer,
    PosSaleReadSerializer,
    serialize_checkout_result,
)
from .services import build_receipt, void_pos_sale


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
        return context


class PosCheckoutApi(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PosCheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            result = serializer.save()
        except DjangoValidationError as exc:
            return Response(_validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_checkout_result(result, request), status=status.HTTP_201_CREATED)


def _validation_error_data(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    if hasattr(exc, "messages"):
        return exc.messages
    return {"detail": str(exc)}


def _sale_queryset_for_user(user):
    queryset = PosSale.objects.select_related("payment", "cashier", "warehouse").prefetch_related(
        "lines__product",
        "sale_orders__outbound_order",
    )
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        return queryset.none()
    return queryset.filter(warehouse_id=warehouse_id)


class PosSaleDetailApi(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, sale_id):
        sale = get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        return Response(
            PosSaleReadSerializer(sale, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class PosSaleReceiptApi(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, sale_id):
        sale = get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        return Response(build_receipt(sale), status=status.HTTP_200_OK)


class PosSaleVoidApi(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, sale_id):
        reason = (request.data.get("reason") or "").strip()
        get_object_or_404(_sale_queryset_for_user(request.user), pk=sale_id)
        try:
            result = void_pos_sale(sale_id=sale_id, user=request.user, reason=reason)
        except DjangoValidationError as exc:
            return Response(_validation_error_data(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_checkout_result(result, request), status=status.HTTP_200_OK)
