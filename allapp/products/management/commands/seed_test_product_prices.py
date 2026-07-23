from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q

from allapp.products.models import Product

PRICE_BANDS = {
    "01": (Decimal("18.00"), Decimal("168.00"), Decimal("1.00")),
    "02": (Decimal("2.50"), Decimal("18.00"), Decimal("0.50")),
    "03": (Decimal("8.00"), Decimal("128.00"), Decimal("1.00")),
    "04": (Decimal("5.00"), Decimal("88.00"), Decimal("0.50")),
    "05": (Decimal("2.00"), Decimal("35.00"), Decimal("0.50")),
    "06": (Decimal("3.00"), Decimal("68.00"), Decimal("0.50")),
    "07": (Decimal("6.00"), Decimal("99.00"), Decimal("1.00")),
    "08": (Decimal("3.00"), Decimal("45.00"), Decimal("0.50")),
    "09": (Decimal("0.50"), Decimal("20.00"), Decimal("0.50")),
    "TST-99": (Decimal("5.00"), Decimal("60.00"), Decimal("0.50")),
}
DEFAULT_PRICE_BAND = (Decimal("5.00"), Decimal("60.00"), Decimal("0.50"))
PRICE_QUANT = Decimal("0.01")


def generate_test_price(*, owner_id, product_code, root_category_code=""):
    root_code = str(root_category_code or "").strip().upper()
    lower, upper, step = PRICE_BANDS.get(root_code, DEFAULT_PRICE_BAND)
    seed_text = f"{owner_id}|{str(product_code or '').strip().upper()}|{root_code}"
    seed = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8])
    step_count = int((upper - lower) / step)
    price = lower + step * (seed % (step_count + 1))
    return price.quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def root_category(category):
    node = category
    seen = set()
    while node is not None and node.pk not in seen:
        if node.pk is not None:
            seen.add(node.pk)
        if not node.parent_id:
            return node
        node = node.parent
    return None


class Command(BaseCommand):
    help = "预览或为 wms_db 中缺少默认价格的商品生成稳定测试价格"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="实际写入 wms_db；不提供该参数时只输出预览",
        )

    def handle(self, *args, **options):
        database_name = self._validate_database()
        preview = self._build_price_plan(lock=False)
        self._write_preview(database_name, preview)

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("预览完成，数据库未修改；确认后请增加 --apply。")
            )
            return

        with transaction.atomic():
            locked_plan = self._build_price_plan(lock=True)
            updated_count = self._apply_prices(locked_plan["products"])
            if updated_count != locked_plan["missing_count"]:
                raise CommandError(
                    "价格更新数量不守恒："
                    f"预计 {locked_plan['missing_count']}，实际 {updated_count}"
                )
            remaining = Product.objects.filter(
                Q(price__isnull=True) | Q(price__lte=0)
            ).count()
            if remaining:
                raise CommandError(
                    f"执行后仍有 {remaining} 个商品无有效价格，事务已回滚"
                )

        self.stdout.write(
            self.style.SUCCESS(
                "执行完成："
                f"补充价格 {updated_count} 个，"
                f"保留已有正价格 {preview['preserved_count']} 个。"
            )
        )

    def _validate_database(self):
        database_name = str(connection.settings_dict.get("NAME") or "")
        if database_name != "wms_db":
            raise CommandError(
                f"安全检查失败：当前数据库为 {database_name or '<空>'}，"
                "该命令只允许写入 wms_db。"
            )
        return database_name

    def _build_price_plan(self, *, lock):
        queryset = Product.objects.select_related(
            "category",
            "category__parent",
            "category__parent__parent",
        ).order_by("id")
        if lock:
            queryset = queryset.select_for_update()

        total_count = queryset.count()
        products = list(queryset.filter(Q(price__isnull=True) | Q(price__lte=0)))
        grouped_prices = defaultdict(list)
        for product in products:
            root = root_category(product.category)
            root_code = root.code if root else ""
            root_name = root.name if root else "未分类"
            product.price = generate_test_price(
                owner_id=product.owner_id,
                product_code=product.code,
                root_category_code=root_code,
            )
            grouped_prices[root_name].append(product.price)

        return {
            "total_count": total_count,
            "missing_count": len(products),
            "preserved_count": total_count - len(products),
            "products": products,
            "grouped_prices": dict(grouped_prices),
        }

    def _write_preview(self, database_name, plan):
        self.stdout.write(f"数据库：{database_name}")
        self.stdout.write(
            "商品："
            f"总数 {plan['total_count']}，待补价格 {plan['missing_count']}，"
            f"保留已有正价格 {plan['preserved_count']}"
        )
        for root_name, prices in sorted(plan["grouped_prices"].items()):
            average = (sum(prices, Decimal("0")) / len(prices)).quantize(
                PRICE_QUANT,
                rounding=ROUND_HALF_UP,
            )
            self.stdout.write(
                f"  {root_name}：{len(prices)} 个，"
                f"最低 {min(prices):.2f}，最高 {max(prices):.2f}，"
                f"平均 {average:.2f}"
            )

    def _apply_prices(self, products):
        if not products:
            return 0
        return Product.objects.bulk_update(products, ["price"], batch_size=500)
