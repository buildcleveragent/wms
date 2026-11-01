"""Capture legacy text before product master data is renamed."""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from allapp.core.product_identity import SNAPSHOT_MODELS
from allapp.products.models import Product


class Command(BaseCommand):
    help = "预检历史商品快照；--apply 分批补录。已有快照永不覆盖。"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--owner", help="仅处理指定货主编码")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if not 1 <= batch_size <= 2000:
            raise CommandError("batch-size 必须在 1 到 2000 之间。")
        for app_label, model_name in SNAPSHOT_MODELS:
            model = apps.get_model(app_label, model_name)
            fields = {field.name for field in model._meta.concrete_fields}
            related = ["product__brand"] + [
                field
                for field in ("original_line", "outbound_order_line", "sale_line", "order_line")
                if field in fields
            ]
            pending = model._base_manager.filter(product_snapshot={})
            if options["owner"]:
                pending = pending.filter(product__owner__code=options["owner"])
            total = pending.count()
            self.stdout.write(f"{app_label}.{model_name}: {total} 条待补录")
            if not options["apply"]:
                continue
            while True:
                ids = list(pending.order_by("pk").values_list("pk", flat=True)[:batch_size])
                if not ids:
                    break
                with transaction.atomic():
                    product_ids = model._base_manager.filter(pk__in=ids).values("product_id")
                    list(
                        Product.all_objects.select_for_update()
                        .filter(pk__in=product_ids)
                        .order_by("pk")
                    )
                    rows = list(
                        pending.select_for_update()
                        .filter(pk__in=ids)
                        .order_by("pk")
                        .prefetch_related(*related)
                    )
                    for row in rows:
                        row.capture_product_snapshot()
                        if not row.product_snapshot.get("name"):
                            raise CommandError(
                                f"{app_label}.{model_name} #{row.pk} 无法确认商品名称。"
                            )
                    model._base_manager.bulk_update(
                        rows, ["product_snapshot"], batch_size=batch_size
                    )
        self.stdout.write("快照补录完成。" if options["apply"] else "仅预检，未修改数据。")
