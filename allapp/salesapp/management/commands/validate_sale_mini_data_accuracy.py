import json
from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Sum
from django.utils import timezone

from allapp.inventory.models import InventoryDetail, InventorySummary
from allapp.salesapp.models import (
    SaleMiniCartItem,
    SaleMiniCoupon,
    SaleMiniOrderAdjustment,
    SaleMiniOrderMapping,
    SaleMiniPayment,
    SaleMiniRefund,
    SaleProductConfig,
)

MONEY_QUANT = Decimal("0.01")


class IssueCollector:
    def __init__(self, limit):
        self.limit = max(int(limit or 0), 1)
        self.total = 0
        self.items = []
        self.by_code = {}

    def add(self, code, message, **context):
        self.total += 1
        self.by_code[code] = self.by_code.get(code, 0) + 1
        if len(self.items) >= self.limit:
            return
        self.items.append(
            {
                "code": code,
                "message": message,
                "context": context,
            }
        )


def money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def cents(value):
    return int(
        (Decimal(value or 0) * Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


class Command(BaseCommand):
    help = "Validate sale-mini mall data accuracy invariants without modifying data."

    def add_arguments(self, parser):
        parser.add_argument("--owner-id", type=int, default=None)
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument(
            "--fail-on-issues",
            action="store_true",
            help="Exit with non-zero status when any issue is found.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print a JSON report instead of human-readable lines.",
        )

    def handle(self, *args, **options):
        owner_id = options["owner_id"]
        issues = IssueCollector(options["limit"])

        self._check_product_configs(issues, owner_id)
        self._check_cart_items(issues, owner_id)
        self._check_order_mappings(issues, owner_id)
        self._check_payments_and_refunds(issues, owner_id)
        self._check_coupons_and_adjustments(issues, owner_id)
        self._check_inventory_non_negative(issues, owner_id)

        report = {
            "ok": issues.total == 0,
            "issue_count": issues.total,
            "issue_count_by_code": issues.by_code,
            "sample_issues": issues.items,
        }
        if options["json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            status = "ok" if report["ok"] else "issues_found"
            self.stdout.write(
                f"sale_mini_data_accuracy={status} issue_count={issues.total}"
            )
            for code, count in sorted(issues.by_code.items()):
                self.stdout.write(f"{code}={count}")
            for issue in issues.items:
                self.stdout.write(
                    "{code}: {message} {context}".format(
                        code=issue["code"],
                        message=issue["message"],
                        context=json.dumps(
                            issue["context"], ensure_ascii=False, sort_keys=True
                        ),
                    )
                )

        if issues.total and options["fail_on_issues"]:
            raise CommandError(f"sale-mini data accuracy found {issues.total} issue(s)")

    def _configs(self, owner_id):
        qs = SaleProductConfig.objects.all()
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        return qs

    def _mappings(self, owner_id):
        qs = SaleMiniOrderMapping.objects.select_related(
            "outbound_order", "customer", "owner"
        )
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        return qs

    def _check_product_configs(self, issues, owner_id):
        qs = self._configs(owner_id)
        for row in qs.exclude(owner_id=F("product__owner_id")).values(
            "id", "owner_id", "product_id", "product__owner_id"
        ):
            issues.add(
                "sale_config_owner_mismatch",
                "SaleProductConfig owner must match product owner.",
                config_id=row["id"],
                owner_id=row["owner_id"],
                product_id=row["product_id"],
                product_owner_id=row["product__owner_id"],
            )

        public_qs = qs.filter(is_active=True, is_listed=True)
        for row in public_qs.filter(owner__is_active=False).values("id", "owner_id"):
            issues.add(
                "listed_config_inactive_owner",
                "Listed sale config points to an inactive owner.",
                config_id=row["id"],
                owner_id=row["owner_id"],
            )
        for row in public_qs.filter(product__is_active=False).values(
            "id", "product_id"
        ):
            issues.add(
                "listed_config_inactive_product",
                "Listed sale config points to an inactive product.",
                config_id=row["id"],
                product_id=row["product_id"],
            )

    def _check_cart_items(self, issues, owner_id):
        qs = SaleMiniCartItem.objects.filter(is_active=True).select_related(
            "cart", "product", "product__base_uom"
        )
        if owner_id:
            qs = qs.filter(cart__owner_id=owner_id)
        config_keys = set(
            SaleProductConfig.objects.filter(
                is_active=True,
                is_listed=True,
                owner_id__in=qs.values_list("cart__owner_id", flat=True),
                product_id__in=qs.values_list("product_id", flat=True),
            ).values_list("owner_id", "product_id")
        )
        for item in qs:
            if item.cart.owner_id != item.product.owner_id:
                issues.add(
                    "cart_owner_product_mismatch",
                    "Cart item product owner must match cart owner.",
                    cart_id=item.cart_id,
                    item_id=item.id,
                    cart_owner_id=item.cart.owner_id,
                    product_id=item.product_id,
                    product_owner_id=item.product.owner_id,
                )
            if (item.cart.owner_id, item.product_id) not in config_keys:
                issues.add(
                    "cart_item_unlisted_product",
                    "Cart item product is no longer listed for this owner.",
                    cart_id=item.cart_id,
                    item_id=item.id,
                    owner_id=item.cart.owner_id,
                    product_id=item.product_id,
                )
            if item.qty <= 0:
                issues.add(
                    "cart_item_non_positive_qty",
                    "Active cart item quantity must be positive.",
                    cart_id=item.cart_id,
                    item_id=item.id,
                    qty=str(item.qty),
                )

    def _check_order_mappings(self, issues, owner_id):
        for mapping in self._mappings(owner_id):
            order = mapping.outbound_order
            if mapping.owner_id != order.owner_id:
                issues.add(
                    "mapping_owner_mismatch",
                    "SaleMiniOrderMapping owner must match outbound order owner.",
                    mapping_id=mapping.id,
                    order_id=order.id,
                    mapping_owner_id=mapping.owner_id,
                    order_owner_id=order.owner_id,
                )
            if mapping.customer_id != order.customer_id:
                issues.add(
                    "mapping_customer_mismatch",
                    "SaleMiniOrderMapping customer must match outbound order customer.",
                    mapping_id=mapping.id,
                    order_id=order.id,
                    mapping_customer_id=mapping.customer_id,
                    order_customer_id=order.customer_id,
                )
            line_total = money(
                order.lines.filter(is_deleted=False).aggregate(
                    total=Sum("final_line_amount")
                )["total"]
            )
            if money(mapping.goods_amount) != line_total:
                issues.add(
                    "goods_amount_mismatch",
                    "Mapping goods amount must equal outbound line final amounts.",
                    mapping_id=mapping.id,
                    order_id=order.id,
                    mapping_goods_amount=str(money(mapping.goods_amount)),
                    outbound_line_total=str(line_total),
                )
            adjustment_total = money(
                mapping.adjustments.exclude(
                    status=SaleMiniOrderAdjustment.Status.PREVIEW
                ).aggregate(total=Sum("amount"))["total"]
            )
            expected_payable = money(mapping.goods_amount) + adjustment_total
            if money(mapping.payable_amount) != expected_payable:
                issues.add(
                    "payable_amount_mismatch",
                    "Payable amount must equal goods amount plus recorded adjustments.",
                    mapping_id=mapping.id,
                    goods_amount=str(money(mapping.goods_amount)),
                    adjustment_total=str(adjustment_total),
                    payable_amount=str(money(mapping.payable_amount)),
                    expected_payable=str(expected_payable),
                )
            if (
                mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.UNPAID
                and mapping.pay_deadline_at
                and mapping.pay_deadline_at <= timezone.now()
            ):
                issues.add(
                    "expired_unpaid_order",
                    "Unpaid WeChat order has passed pay deadline and should be expired.",
                    mapping_id=mapping.id,
                    order_id=order.id,
                    pay_deadline_at=mapping.pay_deadline_at.isoformat(),
                )

    def _check_payments_and_refunds(self, issues, owner_id):
        payments = SaleMiniPayment.objects.select_related(
            "mapping", "owner", "customer"
        )
        if owner_id:
            payments = payments.filter(owner_id=owner_id)
        for payment in payments:
            if payment.owner_id != payment.mapping.owner_id:
                issues.add(
                    "payment_owner_mismatch",
                    "Payment owner must match order mapping owner.",
                    payment_id=payment.id,
                    mapping_id=payment.mapping_id,
                )
            if payment.customer_id != payment.mapping.customer_id:
                issues.add(
                    "payment_customer_mismatch",
                    "Payment customer must match order mapping customer.",
                    payment_id=payment.id,
                    mapping_id=payment.mapping_id,
                )
            if payment.amount_cents != cents(payment.amount):
                issues.add(
                    "payment_cent_mismatch",
                    "Payment cents must match payment amount.",
                    payment_id=payment.id,
                    amount=str(payment.amount),
                    amount_cents=payment.amount_cents,
                    expected_cents=cents(payment.amount),
                )
            if (
                payment.status == SaleMiniPayment.Status.PAID
                and not payment.transaction_id
            ):
                issues.add(
                    "paid_payment_missing_transaction",
                    "Paid payment should have a WeChat transaction id.",
                    payment_id=payment.id,
                    mapping_id=payment.mapping_id,
                )

        refunds = SaleMiniRefund.objects.select_related("payment", "payment__mapping")
        if owner_id:
            refunds = refunds.filter(owner_id=owner_id)
        for refund in refunds:
            payment = refund.payment
            if refund.owner_id != payment.owner_id:
                issues.add(
                    "refund_owner_mismatch",
                    "Refund owner must match payment owner.",
                    refund_id=refund.id,
                    payment_id=payment.id,
                )
            if refund.amount_cents != cents(refund.amount):
                issues.add(
                    "refund_cent_mismatch",
                    "Refund cents must match refund amount.",
                    refund_id=refund.id,
                    amount=str(refund.amount),
                    amount_cents=refund.amount_cents,
                    expected_cents=cents(refund.amount),
                )
            if refund.amount_cents > refund.total_amount_cents:
                issues.add(
                    "refund_exceeds_payment_total",
                    "Refund amount cannot exceed original payment total.",
                    refund_id=refund.id,
                    amount_cents=refund.amount_cents,
                    total_amount_cents=refund.total_amount_cents,
                )
            if refund.status == SaleMiniRefund.Status.SUCCESS and not refund.success_at:
                issues.add(
                    "successful_refund_missing_success_at",
                    "Successful refund should have a success timestamp.",
                    refund_id=refund.id,
                    payment_id=payment.id,
                )

    def _check_coupons_and_adjustments(self, issues, owner_id):
        coupons = SaleMiniCoupon.objects.select_related(
            "locked_mapping", "used_mapping"
        )
        if owner_id:
            coupons = coupons.filter(owner_id=owner_id)
        for coupon in coupons:
            if (
                coupon.status == SaleMiniCoupon.Status.LOCKED
                and not coupon.locked_mapping_id
            ):
                issues.add(
                    "locked_coupon_missing_mapping",
                    "Locked coupon must reference the locked order mapping.",
                    coupon_id=coupon.id,
                    coupon_no=coupon.coupon_no,
                )
            if (
                coupon.status == SaleMiniCoupon.Status.USED
                and not coupon.used_mapping_id
            ):
                issues.add(
                    "used_coupon_missing_mapping",
                    "Used coupon must reference the used order mapping.",
                    coupon_id=coupon.id,
                    coupon_no=coupon.coupon_no,
                )
            if (
                coupon.locked_mapping_id
                and coupon.locked_mapping.owner_id != coupon.owner_id
            ):
                issues.add(
                    "coupon_locked_owner_mismatch",
                    "Coupon locked mapping owner must match coupon owner.",
                    coupon_id=coupon.id,
                    mapping_id=coupon.locked_mapping_id,
                )
            if (
                coupon.used_mapping_id
                and coupon.used_mapping.owner_id != coupon.owner_id
            ):
                issues.add(
                    "coupon_used_owner_mismatch",
                    "Coupon used mapping owner must match coupon owner.",
                    coupon_id=coupon.id,
                    mapping_id=coupon.used_mapping_id,
                )

        adjustments = SaleMiniOrderAdjustment.objects.filter(
            mapping__isnull=False
        ).select_related("mapping")
        if owner_id:
            adjustments = adjustments.filter(owner_id=owner_id)
        for adjustment in adjustments:
            if adjustment.owner_id != adjustment.mapping.owner_id:
                issues.add(
                    "adjustment_owner_mismatch",
                    "Adjustment owner must match order mapping owner.",
                    adjustment_id=adjustment.id,
                    mapping_id=adjustment.mapping_id,
                )
            if adjustment.customer_id != adjustment.mapping.customer_id:
                issues.add(
                    "adjustment_customer_mismatch",
                    "Adjustment customer must match order mapping customer.",
                    adjustment_id=adjustment.id,
                    mapping_id=adjustment.mapping_id,
                )

    def _check_inventory_non_negative(self, issues, owner_id):
        detail_qs = InventoryDetail.objects.filter(is_active=True)
        summary_qs = InventorySummary.objects.filter(is_active=True)
        if owner_id:
            detail_qs = detail_qs.filter(owner_id=owner_id)
            summary_qs = summary_qs.filter(owner_id=owner_id)
        for detail in detail_qs.filter(available_qty__lt=0).only(
            "id",
            "owner_id",
            "product_id",
            "warehouse_id",
            "available_qty",
        ):
            issues.add(
                "negative_inventory_detail_available",
                "Inventory detail available quantity must not be negative.",
                inventory_detail_id=detail.id,
                owner_id=detail.owner_id,
                product_id=detail.product_id,
                warehouse_id=detail.warehouse_id,
                available_qty=str(detail.available_qty),
            )
        for summary in summary_qs.filter(available_qty__lt=0).only(
            "id",
            "owner_id",
            "product_id",
            "available_qty",
        ):
            issues.add(
                "negative_inventory_summary_available",
                "Inventory summary available quantity must not be negative.",
                inventory_summary_id=summary.id,
                owner_id=summary.owner_id,
                product_id=summary.product_id,
                available_qty=str(summary.available_qty),
            )
