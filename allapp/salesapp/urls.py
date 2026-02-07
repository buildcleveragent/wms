from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import viewsets as v

router = DefaultRouter()
router.register(r"biz-orgs", v.BizOrgViewSet)
router.register(r"salespersons", v.SalespersonViewSet)
router.register(r"channels", v.ChannelViewSet)
router.register(r"customer-channels", v.CustomerChannelViewSet)
router.register(r"customer-product-policies", v.CustomerProductPolicyViewSet)
router.register(r"channel-product-policies", v.ChannelProductPolicyViewSet)
router.register(r"price-groups", v.PriceGroupViewSet)
router.register(r"price-lists", v.PriceListViewSet)
router.register(r"price-items", v.PriceItemViewSet)
router.register(r"customer-special-prices", v.CustomerSpecialPriceViewSet)
router.register(r"price-memories", v.PriceMemoryViewSet)
router.register(r"promotions", v.PromotionViewSet)
router.register(r"promotion-gift-items", v.PromotionGiftItemViewSet)
router.register(r"promotion-discount-steps", v.PromotionDiscountStepViewSet)
router.register(r"promotion-special-prices", v.PromotionSpecialPriceViewSet)
router.register(r"sales-orders", v.SalesOrderViewSet)
router.register(r"sales-order-lines", v.SalesOrderLineViewSet)
router.register(r"visit-plans", v.VisitPlanViewSet)
router.register(r"attendance", v.AttendanceRecordViewSet)
router.register(r"visit-records", v.VisitRecordViewSet)
router.register(r"gps-points", v.GPSTrackPointViewSet)
router.register(r"photo-types", v.PhotoTypeViewSet)
router.register(r"visit-photos", v.VisitPhotoViewSet)
router.register(r"credit-policies", v.CreditPolicyViewSet)
router.register(r"ar-ledgers", v.ARLedgerViewSet)
router.register(r"expense-advances", v.ExpenseAdvanceViewSet)
router.register(r"expense-writeoffs", v.ExpenseWriteOffViewSet)
router.register(r"merch-plans", v.MerchandisingPlanViewSet)
router.register(r"merch-agreements", v.MerchandisingAgreementViewSet)
router.register(r"merch-audits", v.MerchandisingAuditViewSet)
router.register(r"rebate-payouts", v.RebatePayoutViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
