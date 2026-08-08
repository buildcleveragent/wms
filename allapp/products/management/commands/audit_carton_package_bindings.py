from django.core.management.base import BaseCommand, CommandError

from allapp.products.models import Product


class Command(BaseCommand):
    help = "检查商品箱码是否绑定到本商品启用且未删除的包装层级。"

    def handle(self, *args, **options):
        invalid = []
        products = Product.all_objects.select_related("owner", "carton_package").filter(
            carton_barcode__isnull=False
        ).exclude(carton_barcode="")
        for product in products.iterator():
            package = product.carton_package
            if package is None:
                reason = "未绑定包装层级"
            elif package.product_id != product.pk:
                reason = f"包装层级 {package.pk} 属于商品 {package.product_id}"
            elif package.is_deleted:
                reason = f"包装层级 {package.pk} 已删除"
            elif not package.is_active:
                reason = f"包装层级 {package.pk} 已停用"
            else:
                continue
            invalid.append(
                f"货主={product.owner.code} 商品={product.code} 箱码={product.carton_barcode}：{reason}"
            )

        if invalid:
            detail = "\n".join(invalid[:100])
            extra = "" if len(invalid) <= 100 else f"\n另有 {len(invalid) - 100} 条未显示。"
            raise CommandError(f"发现 {len(invalid)} 条无效箱码包装绑定：\n{detail}{extra}")
        self.stdout.write(self.style.SUCCESS("商品箱码包装绑定检查通过。"))
