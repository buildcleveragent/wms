from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination

from .models import InventorySummary
from .serializers import OwnerInventorySummarySerializer


class DefaultPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class OwnerInventorySummaryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    货主端实时库存（MVP 第一层）
    只提供 owner + product 粒度的汇总库存，不做 detail。
    """
    permission_classes = [IsAuthenticated]
    serializer_class = OwnerInventorySummarySerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        user = self.request.user
        owner_id = getattr(user, "owner_id", None)

        # 未绑定货主，直接返回空
        if not owner_id:
            return InventorySummary.objects.none()

        qs = (
            InventorySummary.objects
            .filter(owner_id=owner_id, is_active=True)
            .select_related("product")
            .order_by("product_id")
        )

        q = (self.request.query_params.get("search") or "").strip()
        if q:
            qs = qs.filter(
                Q(product__name__icontains=q)
                | Q(product__code__icontains=q)
                | Q(product__sku__icontains=q)
                | Q(product__gtin__icontains=q)
            )

        return qs