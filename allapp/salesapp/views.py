from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from .serializers import *
from .models import *
from .services.pricing import compute_price_for_line
from .services.promotion import apply_promotions_for_order

class BaseModelViewSet(viewsets.ModelViewSet):
    # 统一分页、过滤钩子可在此添加
    pass

class SimpleViewSet(BaseModelViewSet):
    """无自定义动作的简单模型通用 ViewSet"""
    queryset = None
    serializer_class = None

class BizOrgViewSet(SimpleViewSet):
    queryset = BizOrg.objects.all()
    serializer_class = BizOrgSerializer
    filterset_fields = ("owner", "org_type")
    search_fields = ("name", "code")

class SalespersonViewSet(SimpleViewSet):
    queryset = Salesperson.objects.all()
    serializer_class = SalespersonSerializer
    filterset_fields = ("owner", "org")
    search_fields = ("user__username", "employee_no", "phone")

class ChannelViewSet(SimpleViewSet):
    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer
    filterset_fields = ("owner",)
    search_fields = ("name", "code")

class CustomerChannelViewSet(SimpleViewSet):
    queryset = CustomerChannel.objects.all()
    serializer_class = CustomerChannelSerializer
    filterset_fields = ("owner", "channel", "customer")

class CustomerProductPolicyViewSet(SimpleViewSet):
    queryset = CustomerProductPolicy.objects.all()
    serializer_class = CustomerProductPolicySerializer
    filterset_fields = ("owner", "customer")

class ChannelProductPolicyViewSet(SimpleViewSet):
    queryset = ChannelProductPolicy.objects.all()
    serializer_class = ChannelProductPolicySerializer
    filterset_fields = ("owner", "channel")

class PriceGroupViewSet(SimpleViewSet):
    queryset = PriceGroup.objects.all()
    serializer_class = PriceGroupSerializer
    filterset_fields = ("owner",)

class PriceListViewSet(SimpleViewSet):
    queryset = PriceList.objects.all()
    serializer_class = PriceListSerializer
    filterset_fields = ("owner", "channel", "is_default")

class PriceItemViewSet(SimpleViewSet):
    queryset = PriceItem.objects.all()
    serializer_class = PriceItemSerializer
    filterset_fields = ("owner", "price_list", "product")

class CustomerSpecialPriceViewSet(SimpleViewSet):
    queryset = CustomerSpecialPrice.objects.all()
    serializer_class = CustomerSpecialPriceSerializer
    filterset_fields = ("owner", "customer")

class PriceMemoryViewSet(SimpleViewSet):
    queryset = PriceMemory.objects.all()
    serializer_class = PriceMemorySerializer
    filterset_fields = ("owner", "customer")

class PromotionViewSet(SimpleViewSet):
    queryset = Promotion.objects.all()
    serializer_class = PromotionSerializer
    filterset_fields = ("owner", "promo_type", "channel", "customer")
    search_fields = ("name", "code")

class PromotionGiftItemViewSet(SimpleViewSet):
    queryset = PromotionGiftItem.objects.all()
    serializer_class = PromotionGiftItemSerializer
    filterset_fields = ("owner", "promotion")

class PromotionDiscountStepViewSet(SimpleViewSet):
    queryset = PromotionDiscountStep.objects.all()
    serializer_class = PromotionDiscountStepSerializer
    filterset_fields = ("owner", "promotion")

class PromotionSpecialPriceViewSet(SimpleViewSet):
    queryset = PromotionSpecialPrice.objects.all()
    serializer_class = PromotionSpecialPriceSerializer
    filterset_fields = ("owner", "promotion")

# —— 订单 ——
class SalesOrderViewSet(BaseModelViewSet):
    queryset = SalesOrder.objects.all().select_related("salesperson", "customer")
    serializer_class = SalesOrderSerializer
    filterset_fields = ("owner", "status", "order_type", "order_date", "customer")
    search_fields = ("id", "customer__name", "salesperson__user__username")

    @transaction.atomic
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        order = self.get_object()
        if order.status != SalesOrder.Status.DRAFT:
            return Response({"detail": "仅草稿可提交"}, status=400)
        order.status = SalesOrder.Status.SUBMITTED
        order.save(update_fields=["status", "updated_at"])
        return Response({"detail": "已提交"})

    @transaction.atomic
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        order = self.get_object()
        if order.status not in [SalesOrder.Status.SUBMITTED, SalesOrder.Status.REJECTED]:
            return Response({"detail": "仅提交/驳回状态可审核通过"}, status=400)
        # 价格计算 & 促销匹配
        apply_promotions_for_order(order)  # 内部会重新计算 total_amount
        order.status = SalesOrder.Status.APPROVED
        order.save(update_fields=["status", "total_amount", "updated_at"])
        return Response({"detail": "审核通过", "total_amount": str(order.total_amount)})

    @transaction.atomic
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        order = self.get_object()
        if order.status not in [SalesOrder.Status.SUBMITTED, SalesOrder.Status.APPROVED]:
            return Response({"detail": "仅提交/已审核状态可驳回"}, status=400)
        order.status = SalesOrder.Status.REJECTED
        order.save(update_fields=["status", "updated_at"])
        return Response({"detail": "已驳回"})

    # —— 批量审核/反审核 ——
    @transaction.atomic
    @action(detail=False, methods=["post"])
    def batch_approve(self, request):
        ids = request.data.get("ids", [])
        qs = self.get_queryset().filter(id__in=ids, status__in=[SalesOrder.Status.SUBMITTED, SalesOrder.Status.REJECTED])
        ok = []
        for o in qs:
            apply_promotions_for_order(o)
            o.status = SalesOrder.Status.APPROVED
            o.save(update_fields=["status", "total_amount", "updated_at"])
            ok.append(o.id)
        return Response({"approved": ok, "total": len(ids)})

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def batch_reject(self, request):
        ids = request.data.get("ids", [])
        qs = self.get_queryset().filter(id__in=ids, status__in=[SalesOrder.Status.SUBMITTED, SalesOrder.Status.APPROVED])
        ok = []
        for o in qs:
            o.status = SalesOrder.Status.REJECTED
            o.save(update_fields=["status", "updated_at"])
            ok.append(o.id)
        return Response({"rejected": ok, "total": len(ids)})

class SalesOrderLineViewSet(BaseModelViewSet):
    queryset = SalesOrderLine.objects.all().select_related("order", "product")
    serializer_class = SalesOrderLineSerializer
    filterset_fields = ("owner", "order", "product")
    search_fields = ("order__id", "product__name")

class VisitPlanViewSet(SimpleViewSet):
    queryset = VisitPlan.objects.all()
    serializer_class = VisitPlanSerializer
    filterset_fields = ("owner", "salesperson", "status", "planned_date")

class AttendanceRecordViewSet(SimpleViewSet):
    queryset = AttendanceRecord.objects.all()
    serializer_class = AttendanceRecordSerializer
    filterset_fields = ("owner", "salesperson", "record_type")

class VisitRecordViewSet(SimpleViewSet):
    queryset = VisitRecord.objects.all()
    serializer_class = VisitRecordSerializer
    filterset_fields = ("owner", "salesperson", "customer")

class GPSTrackPointViewSet(SimpleViewSet):
    queryset = GPSTrackPoint.objects.all()
    serializer_class = GPSTrackPointSerializer
    filterset_fields = ("owner", "visit")

class PhotoTypeViewSet(SimpleViewSet):
    queryset = PhotoType.objects.all()
    serializer_class = PhotoTypeSerializer
    filterset_fields = ("owner",)

class VisitPhotoViewSet(SimpleViewSet):
    queryset = VisitPhoto.objects.all()
    serializer_class = VisitPhotoSerializer
    filterset_fields = ("owner", "visit", "photo_type")

class CreditPolicyViewSet(SimpleViewSet):
    queryset = CreditPolicy.objects.all()
    serializer_class = CreditPolicySerializer
    filterset_fields = ("owner", "customer", "salesperson")

class ARLedgerViewSet(SimpleViewSet):
    queryset = ARLedger.objects.all()
    serializer_class = ARLedgerSerializer
    filterset_fields = ("owner", "customer")

class ExpenseAdvanceViewSet(SimpleViewSet):
    queryset = ExpenseAdvance.objects.all()
    serializer_class = ExpenseAdvanceSerializer
    filterset_fields = ("owner", "customer", "status")

class ExpenseWriteOffViewSet(SimpleViewSet):
    queryset = ExpenseWriteOff.objects.all()
    serializer_class = ExpenseWriteOffSerializer
    filterset_fields = ("owner", "advance")

class MerchandisingPlanViewSet(SimpleViewSet):
    queryset = MerchandisingPlan.objects.all()
    serializer_class = MerchandisingPlanSerializer
    filterset_fields = ("owner", "customer")

class MerchandisingAgreementViewSet(SimpleViewSet):
    queryset = MerchandisingAgreement.objects.all()
    serializer_class = MerchandisingAgreementSerializer
    filterset_fields = ("owner", "plan")

class MerchandisingAuditViewSet(SimpleViewSet):
    queryset = MerchandisingAudit.objects.all()
    serializer_class = MerchandisingAuditSerializer
    filterset_fields = ("owner", "plan", "result")

class RebatePayoutViewSet(SimpleViewSet):
    queryset = RebatePayout.objects.all()
    serializer_class = RebatePayoutSerializer
    filterset_fields = ("owner", "customer", "status")
