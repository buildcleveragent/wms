from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from openpyxl import Workbook

from allapp.baseinfo.models import Owner
from allapp.products.models import Product


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


class Command(BaseCommand):
    help = "按货主导出商品主档 Excel，输出格式兼容 import_products_excel"

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner",
            required=True,
            help="货主 id/code/name，例如：名创、MINISO、12",
        )
        parser.add_argument(
            "--file",
            default="products_export.xlsx",
            help="导出的 Excel 文件路径，默认 products_export.xlsx",
        )
        parser.add_argument(
            "--sheet",
            default="Sheet1",
            help="工作表名称，默认 Sheet1",
        )
        parser.add_argument(
            "--owner-column",
            choices=("code", "name"),
            default="code",
            help="导出到“货主”列的值，默认 code。生产导入时 code/name 都可识别。",
        )
        parser.add_argument(
            "--include-deleted",
            action="store_true",
            help="包含已软删除商品。默认只导出未删除商品。",
        )

    def get_owner(self, value: str) -> Owner:
        key = _norm_str(value)
        if not key:
            raise CommandError("--owner 不能为空")

        owner = None
        if key.isdigit():
            owner = Owner.all_objects.filter(id=int(key)).first()

        if owner is None:
            owner = Owner.all_objects.filter(code__iexact=key).first()

        if owner is None:
            owner = Owner.all_objects.filter(name__iexact=key).first()

        if owner is None:
            matches = list(Owner.all_objects.filter(name__icontains=key).order_by("id")[:5])
            if len(matches) == 1:
                owner = matches[0]
            elif len(matches) > 1:
                choices = ", ".join(f"{o.id}/{o.code}/{o.name}" for o in matches)
                raise CommandError(f"找到多个匹配货主，请改用 id 或 code：{choices}")

        if owner is None:
            raise CommandError(f"找不到货主：{key}")

        return owner

    def handle(self, *args, **options):
        owner = self.get_owner(options["owner"])
        output_file = Path(options["file"]).expanduser()
        sheet_name = options["sheet"]
        owner_column = options["owner_column"]
        include_deleted = bool(options["include_deleted"])

        if not output_file.parent.exists():
            output_file.parent.mkdir(parents=True, exist_ok=True)

        queryset = Product.all_objects.filter(owner=owner).select_related("owner", "base_uom")
        if not include_deleted:
            queryset = queryset.filter(is_deleted=False)
        queryset = queryset.order_by("code", "id")

        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name

        ws.append(
            [
                "货主",
                "货主商品编码",
                "仓库SKU编码",
                "商品名称",
                "规格",
                "单位",
                "价格",
                "保质期管理",
                "效期基准",
                "保质期天数",
                "入库有效天数",
                "预警天数",
                "FEFO",
            ]
        )

        count = 0
        for product in queryset.iterator():
            owner_value = owner.code if owner_column == "code" else owner.name
            if product.expiry_control:
                expiry_basis = product.expiry_basis or ""
                shelf_life_days = product.shelf_life_days or ""
                inbound_valid_days = product.inbound_valid_days or ""
                expiry_warning_days = product.expiry_warning_days or ""
                fefo_required = product.fefo_required
            else:
                expiry_basis = ""
                shelf_life_days = ""
                inbound_valid_days = ""
                expiry_warning_days = ""
                fefo_required = False

            ws.append(
                [
                    owner_value,
                    product.code,
                    product.sku,
                    product.name,
                    product.spec or "",
                    product.base_uom.code if product.base_uom_id else "",
                    str(product.price) if product.price is not None else "",
                    _yes_no(product.expiry_control),
                    expiry_basis,
                    shelf_life_days,
                    inbound_valid_days,
                    expiry_warning_days,
                    _yes_no(fefo_required),
                ]
            )
            count += 1

        wb.save(output_file)

        self.stdout.write(self.style.SUCCESS("==== 商品主档导出完成 ===="))
        self.stdout.write(f"owner  : {owner.id} / {owner.code} / {owner.name}")
        self.stdout.write(f"file   : {output_file}")
        self.stdout.write(f"sheet  : {sheet_name}")
        self.stdout.write(f"count  : {count}")
        if include_deleted:
            self.stdout.write(self.style.WARNING("已包含软删除商品。"))
