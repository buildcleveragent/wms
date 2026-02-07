from rest_framework import serializers
from .models import (
    BizOrg, Salesperson, Channel, CustomerChannel,
    CustomerProductPolicy, ChannelProductPolicy,
    PriceGroup, PriceList, PriceItem, CustomerSpecialPrice, PriceMemory,
    Promotion, PromotionGiftItem, PromotionDiscountStep, PromotionSpecialPrice,
    VisitPlan, AttendanceRecord, VisitRecord, GPSTrackPoint, PhotoType, VisitPhoto,
    SalesOrder, SalesOrderLine, CreditPolicy, ARLedger,
    ExpenseAdvance, ExpenseWriteOff, MerchandisingPlan, MerchandisingAgreement, MerchandisingAudit, RebatePayout,
)
from allapp.products.models import ProductSKU

class BaseModelSerializer(serializers.ModelSerializer):
    class Meta:
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

class BizOrgSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = BizOrg

class SalespersonSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = Salesperson

class ChannelSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = Channel

class CustomerChannelSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = CustomerChannel

class CustomerProductPolicySerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = CustomerProductPolicy

class ChannelProductPolicySerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = ChannelProductPolicy

class PriceGroupSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = PriceGroup

class PriceItemSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = PriceItem

class PriceListSerializer(BaseModelSerializer):
    items = PriceItemSerializer(many=True, read_only=True)
    class Meta(BaseModelSerializer.Meta):
        model = PriceList

class CustomerSpecialPriceSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = CustomerSpecialPrice

class PriceMemorySerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = PriceMemory

class PromotionGiftItemSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = PromotionGiftItem

class PromotionDiscountStepSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = PromotionDiscountStep

class PromotionSpecialPriceSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = PromotionSpecialPrice

class PromotionSerializer(BaseModelSerializer):
    gift_items = PromotionGiftItemSerializer(many=True, read_only=True)
    discount_steps = PromotionDiscountStepSerializer(many=True, read_only=True)
    special_prices = PromotionSpecialPriceSerializer(many=True, read_only=True)
    class Meta(BaseModelSerializer.Meta):
        model = Promotion

# —— 订单与行，含基础校验 ——
class SalesOrderLineSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = SalesOrderLine

    def validate(self, attrs):
        # 基础校验：最小订量、倍数、订货单位一致性（服务内封装）
        from .services.validation import validate_order_line_rules
        validate_order_line_rules(attrs.get("owner"), self.initial_data.get("customer") or self.context.get("customer"),
                                  attrs.get("product"), attrs.get("order_uom"), attrs.get("qty"))
        return attrs

class SalesOrderSerializer(BaseModelSerializer):
    lines = SalesOrderLineSerializer(many=True, read_only=True)

    class Meta(BaseModelSerializer.Meta):
        model = SalesOrder

    def validate(self, attrs):
        # 信用额校验（可选：提交/审核前校验）
        return attrs

class VisitPlanSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = VisitPlan

class AttendanceRecordSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = AttendanceRecord

class VisitRecordSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = VisitRecord

class GPSTrackPointSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = GPSTrackPoint

class PhotoTypeSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = PhotoType

class VisitPhotoSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = VisitPhoto

class CreditPolicySerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = CreditPolicy

class ARLedgerSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = ARLedger

class ExpenseAdvanceSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = ExpenseAdvance

class ExpenseWriteOffSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = ExpenseWriteOff

class MerchandisingPlanSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = MerchandisingPlan

class MerchandisingAgreementSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = MerchandisingAgreement

class MerchandisingAuditSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = MerchandisingAudit

class RebatePayoutSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model = RebatePayout
