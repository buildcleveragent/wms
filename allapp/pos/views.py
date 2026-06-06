from __future__ import annotations

from django.db.models import Q
from rest_framework import generics, permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.products.models import Product

from .serializers import PosCheckoutResponseSerializer, PosCheckoutSerializer, PosProductSerializer


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
        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if not warehouse_id:
            return Product.objects.none()

        queryset = Product.objects.filter(
            is_active=True,
        ).select_related("base_uom")
        if owner_id:
            queryset = queryset.filter(owner_id=owner_id)
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
        context["owner_id"] = getattr(self.request.user, "owner_id", None)
        context["warehouse_id"] = getattr(self.request.user, "warehouse_id", None)
        return context


class PosCheckoutApi(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PosCheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response(
            PosCheckoutResponseSerializer(order, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
