from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from allapp.products.models import Product
from allapp.salesapp.models import SaleProductConfig


class Command(BaseCommand):
    help = (
        "Diagnose and optionally create sale-mini product configs for active products. "
        "Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner-code",
            action="append",
            default=[],
            help="Limit products by owner code. Can be provided multiple times.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write missing SaleProductConfig rows. Without this flag only reports.",
        )
        parser.add_argument(
            "--listed",
            action="store_true",
            help="Mark created configs as listed. Use only after confirming products are saleable.",
        )
        parser.add_argument(
            "--stock-display",
            choices=[
                SaleProductConfig.StockDisplay.STATUS,
                SaleProductConfig.StockDisplay.EXACT,
                SaleProductConfig.StockDisplay.HIDDEN,
            ],
            default=SaleProductConfig.StockDisplay.STATUS,
            help="Stock display mode for created configs.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit created rows. 0 means no limit.",
        )

    def handle(self, *args, **options):
        owner_codes = [code.strip().upper() for code in options["owner_code"] if code]
        apply_changes = options["apply"]
        listed = options["listed"]
        limit = max(int(options["limit"] or 0), 0)

        products = Product.objects.filter(is_active=True, owner__is_active=True)
        if owner_codes:
            products = products.filter(owner__code__in=owner_codes)
        products = products.select_related("owner").order_by("owner__code", "code")

        product_rows = list(products)
        product_ids = [product.id for product in product_rows]
        config_qs = SaleProductConfig.objects.filter(product_id__in=product_ids)
        existing_keys = set(config_qs.values_list("owner_id", "product_id"))
        missing = [
            product
            for product in product_rows
            if (product.owner_id, product.id) not in existing_keys
        ]
        if limit:
            create_rows = missing[:limit]
        else:
            create_rows = missing

        visible_configs = config_qs.filter(
            owner_id=F("product__owner_id"),
            owner__is_active=True,
            product__is_active=True,
            is_active=True,
            is_listed=True,
        ).count()
        mismatched_configs = config_qs.exclude(owner_id=F("product__owner_id")).count()

        self.stdout.write(
            "active_products={products} configs={configs} visible_configs={visible} "
            "missing_configs={missing} mismatched_configs={mismatched}".format(
                products=len(product_rows),
                configs=config_qs.count(),
                visible=visible_configs,
                missing=len(missing),
                mismatched=mismatched_configs,
            )
        )

        for product in missing[:20]:
            self.stdout.write(
                "missing owner={owner} product={code} name={name}".format(
                    owner=product.owner.code,
                    code=product.code,
                    name=product.name,
                )
            )
        if len(missing) > 20:
            self.stdout.write(f"... and {len(missing) - 20} more missing products")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("dry-run only; no rows were changed"))
            return
        if not create_rows:
            self.stdout.write(self.style.SUCCESS("created=0"))
            return
        if listed and not owner_codes:
            raise CommandError(
                "Refusing to list all owners at once. Provide --owner-code when using --listed."
            )

        rows = []
        for product in create_rows:
            sale_price = product.price
            if sale_price is not None:
                sale_price = Decimal(sale_price)
            rows.append(
                SaleProductConfig(
                    owner=product.owner,
                    product=product,
                    sale_price=sale_price,
                    is_listed=listed,
                    stock_display=options["stock_display"],
                    min_order_qty=Decimal("1"),
                    multiple_qty=Decimal("1"),
                )
            )

        with transaction.atomic():
            SaleProductConfig.objects.bulk_create(rows, batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                "created={created} listed={listed}".format(
                    created=len(rows),
                    listed=str(listed).lower(),
                )
            )
        )
