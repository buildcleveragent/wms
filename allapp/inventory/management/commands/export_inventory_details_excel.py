from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from openpyxl import Workbook

from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Warehouse


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _date_value(value: Any) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _datetime_value(value: Any) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _decimal_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class Command(BaseCommand):
    help = "按货主导出库存现存量明细 Excel"

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner",
            required=True,
            help="货主 id/code/name，例如：名创、MC、5",
        )
        parser.add_argument(
            "--file",
            default="inventory_details_export.xlsx",
            help="导出的 Excel 文件路径，默认 inventory_details_export.xlsx",
        )
        parser.add_argument(
            "--sheet",
            default="库存明细",
            help="工作表名称，默认 库存明细",
        )
        parser.add_argument(
            "--warehouse",
            help="可选：按仓库 id/code/name 过滤",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="包含 is_active=False 的库存行。默认只导出启用库存行。",
        )
        parser.add_argument(
            "--include-deleted",
            action="store_true",
            help="包含已软删除库存行。默认只导出未删除库存行。",
        )
        parser.add_argument(
            "--positive-only",
            action="store_true",
            help="只导出账面库存大于 0 的库存行。",
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

    def get_warehouse(self, value: str) -> Warehouse:
        key = _norm_str(value)
        if not key:
            raise CommandError("--warehouse 不能为空")

        warehouse = None
        if key.isdigit():
            warehouse = Warehouse.all_objects.filter(id=int(key)).first()

        if warehouse is None:
            warehouse = Warehouse.all_objects.filter(code__iexact=key).first()

        if warehouse is None:
            warehouse = Warehouse.all_objects.filter(name__iexact=key).first()

        if warehouse is None:
            matches = list(Warehouse.all_objects.filter(name__icontains=key).order_by("id")[:5])
            if len(matches) == 1:
                warehouse = matches[0]
            elif len(matches) > 1:
                choices = ", ".join(f"{w.id}/{w.code}/{w.name}" for w in matches)
                raise CommandError(f"找到多个匹配仓库，请改用 id 或 code：{choices}")

        if warehouse is None:
            raise CommandError(f"找不到仓库：{key}")

        return warehouse

    def handle(self, *args, **options):
        owner = self.get_owner(options["owner"])
        warehouse = self.get_warehouse(options["warehouse"]) if options.get("warehouse") else None
        output_file = Path(options["file"]).expanduser()
        sheet_name = options["sheet"]
        include_inactive = bool(options["include_inactive"])
        include_deleted = bool(options["include_deleted"])
        positive_only = bool(options["positive_only"])

        if not output_file.parent.exists():
            output_file.parent.mkdir(parents=True, exist_ok=True)

        manager = InventoryDetail.all_objects if include_deleted else InventoryDetail.objects
        queryset = manager.filter(owner=owner).select_related(
            "owner",
            "warehouse",
            "subwarehouse",
            "location",
            "product",
            "product__base_uom",
        )

        if warehouse:
            queryset = queryset.filter(warehouse=warehouse)

        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        if positive_only:
            queryset = queryset.filter(onhand_qty__gt=0)

        queryset = queryset.order_by(
            "warehouse__code",
            "subwarehouse__code",
            "location__code",
            "product__code",
            "batch_no",
            "expiry_date",
            "serial_no",
            "id",
        )

        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name

        ws.append(
            [
                "库存明细ID",
                "货主代码",
                "货主名称",
                "仓库编号",
                "仓库名称",
                "子仓编号",
                "子仓名称",
                "区域类型",
                "库位编码",
                "库位名称",
                "货主商品编码",
                "仓库SKU编码",
                "商品名称",
                "规格",
                "标准贸易条码",
                "批次号",
                "生产日期",
                "有效期至",
                "序列号",
                "基本单位",
                "账面库存",
                "可用数量",
                "已分配数量",
                "锁定数量",
                "损坏数量",
                "启用状态",
                "已删除",
                "创建时间",
                "更新时间",
            ]
        )

        count = 0
        total_onhand = 0
        total_available = 0

        for detail in queryset.iterator():
            product = detail.product
            warehouse_obj = detail.warehouse
            subwarehouse = detail.subwarehouse
            location = detail.location

            ws.append(
                [
                    detail.id,
                    owner.code,
                    owner.name,
                    warehouse_obj.code if warehouse_obj else "",
                    warehouse_obj.name if warehouse_obj else "",
                    subwarehouse.code if subwarehouse else "",
                    subwarehouse.name if subwarehouse else "",
                    detail.get_zone_type_display(),
                    location.code if location else "",
                    location.name if location else "",
                    product.code if product else "",
                    product.sku if product else "",
                    product.name if product else "",
                    product.spec if product and product.spec else "",
                    product.gtin if product and product.gtin else "",
                    detail.batch_no or "",
                    _date_value(detail.production_date),
                    _date_value(detail.expiry_date),
                    detail.serial_no or "",
                    detail.base_unit or "",
                    _decimal_value(detail.onhand_qty),
                    _decimal_value(detail.available_qty),
                    _decimal_value(detail.allocated_qty),
                    _decimal_value(detail.locked_qty),
                    _decimal_value(detail.damaged_qty),
                    _yes_no(detail.is_active),
                    _yes_no(detail.is_deleted),
                    _datetime_value(detail.created_at),
                    _datetime_value(detail.updated_at),
                ]
            )
            count += 1
            total_onhand += detail.onhand_qty
            total_available += detail.available_qty

        wb.save(output_file)

        self.stdout.write(self.style.SUCCESS("==== 库存明细导出完成 ===="))
        self.stdout.write(f"owner          : {owner.id} / {owner.code} / {owner.name}")
        if warehouse:
            self.stdout.write(f"warehouse      : {warehouse.id} / {warehouse.code} / {warehouse.name}")
        self.stdout.write(f"file           : {output_file}")
        self.stdout.write(f"sheet          : {sheet_name}")
        self.stdout.write(f"count          : {count}")
        self.stdout.write(f"total_onhand   : {total_onhand}")
        self.stdout.write(f"total_available: {total_available}")
        if include_inactive:
            self.stdout.write(self.style.WARNING("已包含停用库存行。"))
        if include_deleted:
            self.stdout.write(self.style.WARNING("已包含软删除库存行。"))
        if positive_only:
            self.stdout.write(self.style.WARNING("仅导出了账面库存大于 0 的库存行。"))
