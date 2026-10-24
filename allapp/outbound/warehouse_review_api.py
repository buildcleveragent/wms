"""Read-only warehouse review queues, independent of legacy shadow authorization."""

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import OutboundOrder
from .order_filters import filter_orders
from .serializers import OutboundOrderReadSerializer
from .warehouse_review import review_detail, scoped_review_orders


def review_stage_orders(qs, stage):
    """Apply the warehouse review stage shared by list and reminder counts."""
    states = {
        "pending": "OWNER_APPROVED",
        "waiting": "WHS_PENDING",
        "approved": "WHS_APPROVED",
        "rejected": "WHS_REJECTED",
    }
    qs = qs.filter(approval_status=states[stage])
    if stage in {"pending", "waiting"}:
        qs = qs.filter(submit_status="SUBMITTED", is_closed=False)
    return qs


class ReviewQuery(serializers.Serializer):
    stage = serializers.ChoiceField(
        choices=["pending", "waiting", "approved", "rejected"], default="pending"
    )
    owner_id = serializers.IntegerField(min_value=1, required=False)
    warehouse_id = serializers.IntegerField(min_value=1, required=False)
    search = serializers.CharField(max_length=100, allow_blank=True, default="")


class ReviewPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class WarehouseReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def queryset(self, request):
        return scoped_review_orders(
            OutboundOrder.objects.select_related(
                "owner",
                "warehouse",
                "customer",
                "approved_by_warehouse",
                "created_by",
                "priced_by",
                "assisted_by",
            ).prefetch_related("lines__product", "lines__base_uom"),
            request.user,
        )

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request, pk=None):
        qs = self.queryset(request)
        if pk is not None:
            return Response(
                review_detail(get_object_or_404(qs, pk=pk), request.user, request=request)
            )
        inputs = ReviewQuery(data=request.query_params)
        inputs.is_valid(raise_exception=True)
        params = dict(inputs.validated_data)
        stage = params.pop("stage")
        qs = review_stage_orders(filter_orders(qs, params), stage)
        pagination = ReviewPagination()
        page = pagination.paginate_queryset(qs.order_by("-biz_date", "-id"), request)
        return pagination.get_paginated_response(
            OutboundOrderReadSerializer(page, many=True, context={"request": request}).data
        )


class WarehouseReviewOptionsView(WarehouseReviewView):
    def get(self, request):
        qs = self.queryset(request)
        return Response(
            {
                "owners": [
                    {"id": pk, "name": name}
                    for pk, name in qs.order_by("owner__name")
                    .values_list("owner_id", "owner__name")
                    .distinct()
                ],
                "warehouses": [
                    {"id": pk, "name": name}
                    for pk, name in qs.order_by("warehouse__name")
                    .values_list("warehouse_id", "warehouse__name")
                    .distinct()
                ],
            }
        )
