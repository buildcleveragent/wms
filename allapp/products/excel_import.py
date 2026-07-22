from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework.exceptions import PermissionDenied

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import Owner

from .models import Brand, Product, ProductCategory, ProductPackage, ProductUom
from .permissions import can_manage_all_owner_products


MAX_IMPORT_FILE_SIZE = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 1000
MAX_XLSX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_XLSX_ENTRIES = 300
IMPORT_SHEET_NAME = "商品导入"
TEMPLATE_VERSION = "2"


HEADERS = (
    "货主编码",
    "商品编号",
    "SKU编码",
    "商品名称",
    "规格",
    "分类编码",
    "品牌编码",
    "基本单位编码",
    "GTIN",
    "零码",
    "箱码",
    "外部系统编码",
    "默认价格",
    "最低价格",
    "最高折扣%",
    "重量kg",
    "体积m³",
    "净含量g",
    "厂家",
    "材质",
    "描述",
    "最低库存",
    "最高库存",
    "序列号管理",
    "批次管理",
    "启用",
    "保质期管理",
    "效期基准",
    "保质期天数",
    "入库有效天数",
    "效期预警天数",
    "FEFO",
    "包装单位编码",
    "包装换算数量",
    "包装条码",
    "采购默认",
    "销售默认",
)

REQUIRED_HEADERS = frozenset(
    {"货主编码", "商品编号", "商品名称", "分类编码", "基本单位编码"}
)
TEXT_HEADERS = frozenset(
    {
        "货主编码",
        "商品编号",
        "SKU编码",
        "分类编码",
        "品牌编码",
        "基本单位编码",
        "GTIN",
        "零码",
        "箱码",
        "外部系统编码",
        "包装单位编码",
        "包装条码",
    }
)

MODEL_FIELD_LABELS = {
    "owner": "货主编码",
    "code": "商品编号",
    "sku": "SKU编码",
    "name": "商品名称",
    "spec": "规格",
    "category": "分类编码",
    "brand": "品牌编码",
    "base_uom": "基本单位编码",
    "gtin": "GTIN",
    "unit_barcode": "零码",
    "carton_barcode": "箱码",
    "external_code": "外部系统编码",
    "price": "默认价格",
    "min_price": "最低价格",
    "max_discount": "最高折扣%",
    "weight": "重量kg",
    "volume": "体积m³",
    "net_content": "净含量g",
    "vender": "厂家",
    "material_quality": "材质",
    "description": "描述",
    "min_stock": "最低库存",
    "max_stock": "最高库存",
    "serial_control": "序列号管理",
    "batch_control": "批次管理",
    "is_active": "启用",
    "expiry_control": "保质期管理",
    "expiry_basis": "效期基准",
    "shelf_life_days": "保质期天数",
    "inbound_valid_days": "入库有效天数",
    "expiry_warning_days": "效期预警天数",
    "fefo_required": "FEFO",
    "uom": "包装单位编码",
    "qty_in_base": "包装换算数量",
    "barcode": "包装条码",
    "is_purchase_default": "采购默认",
    "is_sales_default": "销售默认",
    "__all__": "整行",
}


class ProductImportFileError(ValueError):
    pass


class ProductImportConflictError(ProductImportFileError):
    pass


@dataclass(frozen=True)
class ProductImportAccess:
    allowed_owner_ids: frozenset[int] | None

    def allows_owner(self, owner_id: int) -> bool:
        return self.allowed_owner_ids is None or owner_id in self.allowed_owner_ids


@dataclass
class ParsedProductRow:
    row_number: int
    product: Product
    package_data: dict[str, Any] | None


def resolve_product_import_access(user) -> ProductImportAccess:
    if not user or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("请先登录。")
    if not (
        getattr(user, "is_superuser", False) or user.has_perm("products.add_product")
    ):
        raise PermissionDenied("当前账号没有新增商品权限。")

    scope = AccessScope.for_user(user)
    if getattr(user, "is_superuser", False):
        return ProductImportAccess(allowed_owner_ids=None)
    if not scope.is_valid:
        raise PermissionDenied("当前账号没有有效的角色数据范围。")
    if can_manage_all_owner_products(user):
        return ProductImportAccess(allowed_owner_ids=None)
    if len(scope.owner_ids) == 1:
        owner_id = next(iter(scope.owner_ids))
        return ProductImportAccess(allowed_owner_ids=frozenset({owner_id}))
    raise PermissionDenied("当前账号没有单一货主范围或跨货主管理权限。")


def can_import_products(user) -> bool:
    try:
        resolve_product_import_access(user)
    except PermissionDenied:
        return False
    return True


def _owner_queryset_for_access(access: ProductImportAccess):
    qs = Owner.objects.filter(is_active=True).order_by("code")
    if access.allowed_owner_ids is not None:
        qs = qs.filter(pk__in=access.allowed_owner_ids)
    return qs


def build_product_import_template(user) -> bytes:
    access = resolve_product_import_access(user)
    owners = list(_owner_queryset_for_access(access).values_list("code", "name"))
    uoms = list(
        ProductUom.objects.filter(is_active=True)
        .order_by("code")
        .values_list("code", "name")
    )
    categories = list(
        ProductCategory.objects.filter(is_active=True)
        .order_by("code")
        .values_list("code", "name")
    )
    brands = list(
        Brand.objects.filter(is_active=True)
        .order_by("code")
        .values_list("code", "name")
    )

    workbook = Workbook()
    instructions = workbook.active
    instructions.title = "填写说明"
    data_sheet = workbook.create_sheet(IMPORT_SHEET_NAME)
    refs = workbook.create_sheet("基础资料")
    meta = workbook.create_sheet("_meta")

    _write_instruction_sheet(instructions)
    _write_import_sheet(data_sheet)
    _write_reference_sheet(refs, owners, uoms, categories, brands)
    _write_meta_sheet(meta)
    _add_template_validations(workbook, data_sheet, owners, uoms, categories, brands)
    meta.sheet_state = "hidden"
    workbook.active = workbook.sheetnames.index(IMPORT_SHEET_NAME)

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _write_instruction_sheet(sheet) -> None:
    sheet.append(["商品批量导入模板", f"模板版本：{TEMPLATE_VERSION}"])
    sheet.append(
        [
            "使用步骤",
            _joined_text("下载模板 → 填写“商品导入” → ", "在 wmspda 上传导入。"),  # noqa: E501
        ]
    )
    sheet.append(
        ["必填字段", "货主编码、商品编号、商品名称、分类编码、基本单位编码。"]
    )
    sheet.append(
        ["货主规则", "货主编码必须填写，且只能填写“基础资料”中当前账号有权使用的编码。"]
    )
    sheet.append(
        [
            "SKU规则",
            "SKU编码无需填写，系统按“货主编码-货主下一个SKU序号”自动生成。",
        ]
    )
    sheet.append(
        [
            "布尔值",
            _joined_text(
                "填写 是/否、1/0、true/false；批次、序列号和保质期管理默认否，",
                "启用默认是。",
            ),
        ]
    )
    sheet.append(
        [
            "效期规则",
            _joined_text("保质期管理留空默认否；", "启用时效期基准可填 MFG/INBOUND。"),  # noqa: E501
        ]
    )
    sheet.append(
        [
            "包装规则",
            _joined_text(
                "包装单位与包装换算数量必须同时填写；",
                "第一版每个商品只支持一层包装。",
            ),
        ]
    )
    sheet.append(
        [
            "重复规则",
            _joined_text(
                "商品编号、条码或外部系统编码与已有商品冲突，",
                "或 Excel 内部重复，都会使整批不写入。",
            ),
        ]
    )
    sheet.append(
        [
            "注意",
            _joined_text(
                "编码和条码请按文本填写以保留前导零；",
                "不要在业务单元格使用公式。",
            ),
        ]
    )
    sheet.append([])
    sheet.append(["示例（仅说明，不会导入）"])
    sheet.append(
        [
            "货主编码",
            "商品编号",
            "SKU编码",
            "商品名称",
            "基本单位编码",
            "批次管理",
            "保质期管理",
        ]
    )
    sheet.append(
        ["OWNER-001", "SKU-001", "SKU-001", "示例商品", "PCS", "否", "否"]
    )
    sheet["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    sheet["A1"].fill = PatternFill("solid", fgColor="2563EB")
    sheet["B1"].fill = PatternFill("solid", fgColor="2563EB")
    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 88
    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _write_import_sheet(sheet) -> None:
    sheet.append(list(HEADERS))
    header_fill = PatternFill("solid", fgColor="1D4ED8")
    required_fill = PatternFill("solid", fgColor="DC2626")
    for index, header in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = required_fill if header in REQUIRED_HEADERS else header_fill
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        width = (
            18
            if header in {"商品名称", "描述"}
            else max(12, min(len(header) * 2 + 4, 18))
        )
        sheet.column_dimensions[get_column_letter(index)].width = width
        if header in TEXT_HEADERS:
            sheet.column_dimensions[get_column_letter(index)].number_format = "@"
            for row_number in range(2, MAX_IMPORT_ROWS + 2):
                sheet.cell(row=row_number, column=index).number_format = "@"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}1"
    sheet.row_dimensions[1].height = 32


def _write_reference_sheet(sheet, owners, uoms, categories, brands) -> None:
    blocks = (
        (1, "货主编码", "货主名称", owners),
        (4, "单位编码", "单位名称", uoms),
        (7, "分类编码", "分类名称", categories),
        (10, "品牌编码", "品牌名称", brands),
    )
    for start_col, code_header, name_header, rows in blocks:
        sheet.cell(row=1, column=start_col, value=code_header)
        sheet.cell(row=1, column=start_col + 1, value=name_header)
        for column in (start_col, start_col + 1):
            sheet.cell(row=1, column=column).font = Font(color="FFFFFF", bold=True)
            sheet.cell(row=1, column=column).fill = PatternFill(
                "solid", fgColor="475569"
            )
            sheet.column_dimensions[get_column_letter(column)].width = 22
        for row_number, (code, name) in enumerate(rows, start=2):
            sheet.cell(row=row_number, column=start_col, value=code)
            sheet.cell(row=row_number, column=start_col + 1, value=name)
            sheet.cell(row=row_number, column=start_col).number_format = "@"
    sheet.freeze_panes = "A2"


def _write_meta_sheet(sheet) -> None:
    sheet.append(["schema", "product_import"])
    sheet.append(["version", TEMPLATE_VERSION])
    sheet.append(["max_rows", MAX_IMPORT_ROWS])


def _add_template_validations(
    workbook, data_sheet, owners, uoms, categories, brands
) -> None:
    end_row = MAX_IMPORT_ROWS + 1

    def add_list(header: str, formula: str) -> None:
        column = get_column_letter(HEADERS.index(header) + 1)
        validation = DataValidation(type="list", formula1=formula, allow_blank=True)
        validation.error = "请选择下拉列表中的有效值。"
        validation.errorTitle = "无效值"
        data_sheet.add_data_validation(validation)
        validation.add(f"{column}2:{column}{end_row}")

    def add_named_range(name: str, column: str, count: int) -> None:
        workbook.defined_names.add(
            DefinedName(name, attr_text=f"'基础资料'!${column}$2:${column}${count + 1}")
        )

    if owners:
        add_named_range("ProductImportOwnerCodes", "A", len(owners))
        add_list("货主编码", "ProductImportOwnerCodes")
    if uoms:
        add_named_range("ProductImportUomCodes", "D", len(uoms))
        add_list("基本单位编码", "ProductImportUomCodes")
        add_list("包装单位编码", "ProductImportUomCodes")
    if categories:
        add_named_range("ProductImportCategoryCodes", "G", len(categories))
        add_list("分类编码", "ProductImportCategoryCodes")
    if brands:
        add_named_range("ProductImportBrandCodes", "J", len(brands))
        add_list("品牌编码", "ProductImportBrandCodes")
    for header in (
        "序列号管理",
        "批次管理",
        "启用",
        "保质期管理",
        "FEFO",
        "采购默认",
        "销售默认",
    ):
        add_list(header, '"是,否"')
    add_list("效期基准", '"MFG,INBOUND"')


class ProductExcelImporter:
    def __init__(self, *, user, request=None):
        self.user = user
        self.request = request
        self.access = resolve_product_import_access(user)
        self.errors: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.total_rows = 0
        self._owner_cache: dict[str, Owner | None] = {}
        self._uom_cache: dict[str, ProductUom | None] = {}
        self._category_cache: dict[str, ProductCategory | None] = {}
        self._brand_cache: dict[str, Brand | None] = {}

    def import_file(self, uploaded_file) -> dict[str, Any]:
        workbook = self._load_workbook(uploaded_file)
        rows = self._parse_workbook(workbook)
        if self.errors:
            return self._result(created=[])

        try:
            created = self._persist(rows, getattr(uploaded_file, "name", ""))
        except IntegrityError as exc:
            message = _joined_text(
                "导入期间发生唯一性冲突，整批已回滚；",
                "请重新下载数据或检查并发导入。",
            )
            raise ProductImportConflictError(message) from exc
        except DjangoValidationError as exc:
            raise ProductImportFileError(
                f"导入校验失败，整批已回滚：{_validation_text(exc)}"
            ) from exc
        return self._result(created=created)

    def _load_workbook(self, uploaded_file):
        name = Path(getattr(uploaded_file, "name", "") or "").name
        if Path(name).suffix.lower() != ".xlsx":
            raise ProductImportFileError("仅支持 .xlsx 格式的 Excel 文件。")
        size = getattr(uploaded_file, "size", None)
        if size is not None and size > MAX_IMPORT_FILE_SIZE:
            raise ProductImportFileError("Excel 文件不能超过 5 MB。")
        data = uploaded_file.read(MAX_IMPORT_FILE_SIZE + 1)
        if len(data) > MAX_IMPORT_FILE_SIZE:
            raise ProductImportFileError("Excel 文件不能超过 5 MB。")
        if not data:
            raise ProductImportFileError("Excel 文件为空。")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_XLSX_ENTRIES:
                    raise ProductImportFileError("Excel 文件结构异常。")
                if (
                    sum(entry.file_size for entry in entries)
                    > MAX_XLSX_UNCOMPRESSED_SIZE
                ):
                    raise ProductImportFileError("Excel 解压后内容过大。")
                if archive.testzip() is not None:
                    raise ProductImportFileError("Excel 文件已损坏。")
        except ProductImportFileError:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            message = "无法解析 Excel 文件，请使用系统模板重新保存。"
            raise ProductImportFileError(message) from exc
        try:
            return load_workbook(io.BytesIO(data), data_only=False, read_only=False)
        except Exception as exc:
            raise ProductImportFileError(f"无法打开 Excel：{exc}") from exc

    def _parse_workbook(self, workbook) -> list[ParsedProductRow]:
        if IMPORT_SHEET_NAME not in workbook.sheetnames:
            raise ProductImportFileError(f"Excel 中缺少“{IMPORT_SHEET_NAME}”工作表。")
        sheet = workbook[IMPORT_SHEET_NAME]
        header_cells = list(sheet.iter_rows(min_row=1, max_row=1, values_only=False))[0]
        raw_headers = [_text(cell.value) for cell in header_cells]
        nonempty_headers = [header for header in raw_headers if header]
        duplicates = sorted(
            {
                header
                for header in nonempty_headers
                if nonempty_headers.count(header) > 1
            }
        )
        if duplicates:
            raise ProductImportFileError(f"Excel 表头重复：{', '.join(duplicates)}。")
        unknown = [header for header in nonempty_headers if header not in HEADERS]
        if unknown:
            message = f"Excel 包含不支持的表头：{', '.join(unknown)}。"
            raise ProductImportFileError(message)
        missing = [
            header for header in REQUIRED_HEADERS if header not in nonempty_headers
        ]
        if missing:
            message = f"Excel 缺少必要表头：{', '.join(sorted(missing))}。"
            raise ProductImportFileError(message)

        column_by_header = {
            header: index + 1 for index, header in enumerate(raw_headers) if header
        }
        raw_rows: list[tuple[int, dict[str, Any]]] = []
        for row_number in range(2, sheet.max_row + 1):
            values = {
                header: sheet.cell(row_number, column).value
                for header, column in column_by_header.items()
            }
            if not any(_text(value) for value in values.values()):
                continue
            self.total_rows += 1
            if self.total_rows > MAX_IMPORT_ROWS:
                raise ProductImportFileError(f"一次最多导入 {MAX_IMPORT_ROWS} 条商品。")
            formula_header = next(
                (
                    header
                    for header, column in column_by_header.items()
                    if sheet.cell(row_number, column).data_type == "f"
                ),
                None,
            )
            if formula_header:
                self._error(row_number, formula_header, "业务单元格不能使用公式。")
                continue
            raw_rows.append((row_number, values))
        if self.total_rows == 0:
            raise ProductImportFileError("“商品导入”工作表没有数据行。")

        parsed: list[ParsedProductRow] = []
        seen: dict[tuple[object, str, str], int] = {}
        for row_number, values in raw_rows:
            row = self._parse_row(row_number, values)
            self._check_file_duplicates(row_number, values, seen)
            if row is not None:
                parsed.append(row)
        return parsed

    def _parse_row(
        self, row_number: int, values: dict[str, Any]
    ) -> ParsedProductRow | None:
        before_error_count = len(self.errors)
        owner = self._resolve_owner(row_number, values.get("货主编码"))
        code = self._required_code(
            row_number,
            "商品编号",
            values.get("商品编号"),
            max_length=50,
        )
        if owner is None or not code:
            return None

        existing = Product.all_objects.filter(owner=owner, code__iexact=code).first()
        if existing:
            message = (
                "商品编号命中已软删除商品，请恢复旧商品或更换编号；整批不会写入。"
                if existing.is_deleted
                else "商品编号已存在；整批不会写入。"
            )
            self._error(row_number, "商品编号", message)
            return None

        name = self._required_text(
            row_number,
            "商品名称",
            values.get("商品名称"),
            max_length=200,
        )
        base_uom = self._resolve_uom(
            row_number, "基本单位编码", values.get("基本单位编码"), required=True
        )
        if not name or base_uom is None:
            return None

        category = self._resolve_category(row_number, values.get("分类编码"))
        brand = self._resolve_brand(row_number, values.get("品牌编码"))
        package_uom_value = values.get("包装单位编码")
        package_uom = self._resolve_uom(
            row_number,
            "包装单位编码",
            package_uom_value,
            required=False,
        )

        product = Product(
            owner=owner,
            code=code,
            sku="",
            name=name,
            spec=self._optional_text(
                row_number, "规格", values.get("规格"), max_length=200
            ),
            category=category,
            brand=brand,
            base_uom=base_uom,
            gtin=self._optional_text(
                row_number, "GTIN", values.get("GTIN"), max_length=20
            ),
            unit_barcode=self._optional_text(
                row_number,
                "零码",
                values.get("零码"),
                max_length=50,
            ),
            carton_barcode=self._optional_text(
                row_number, "箱码", values.get("箱码"), max_length=50
            ),
            external_code=self._optional_text(
                row_number, "外部系统编码", values.get("外部系统编码"), max_length=50
            ),
            price=self._decimal(
                row_number, "默认价格", values.get("默认价格"), minimum=Decimal("0")
            ),
            min_price=self._decimal(
                row_number, "最低价格", values.get("最低价格"), minimum=Decimal("0")
            ),
            max_discount=self._decimal(
                row_number,
                "最高折扣%",
                values.get("最高折扣%"),
                minimum=Decimal("0"),
                maximum=Decimal("100"),
            ),
            weight=self._decimal(
                row_number,
                "重量kg",
                values.get("重量kg"),
                minimum=Decimal("0"),
            ),
            volume=self._decimal(
                row_number,
                "体积m³",
                values.get("体积m³"),
                minimum=Decimal("0"),
            ),
            net_content=self._decimal(
                row_number, "净含量g", values.get("净含量g"), minimum=Decimal("0")
            ),
            vender=self._optional_text(
                row_number, "厂家", values.get("厂家"), max_length=50
            ),
            material_quality=self._optional_text(
                row_number, "材质", values.get("材质"), max_length=20
            ),
            description=self._optional_text(row_number, "描述", values.get("描述")),
            min_stock=self._decimal(
                row_number, "最低库存", values.get("最低库存"), minimum=Decimal("0")
            ),
            max_stock=self._decimal(
                row_number, "最高库存", values.get("最高库存"), minimum=Decimal("0")
            ),
            serial_control=self._boolean(
                row_number,
                "序列号管理",
                values.get("序列号管理"),
                False,
            ),
            batch_control=self._boolean(
                row_number,
                "批次管理",
                values.get("批次管理"),
                False,
            ),
            is_active=self._boolean(row_number, "启用", values.get("启用"), True),
            created_by=self.user,
            updated_by=self.user,
        )
        expiry_control = self._boolean(
            row_number,
            "保质期管理",
            values.get("保质期管理"),
            False,
        )
        product.expiry_control = expiry_control
        if expiry_control:
            basis = (_text(values.get("效期基准")) or "MFG").upper()
            if basis not in {"MFG", "INBOUND"}:
                self._error(row_number, "效期基准", "只能填写 MFG 或 INBOUND。")
            product.expiry_basis = basis
            product.shelf_life_days = self._integer(
                row_number, "保质期天数", values.get("保质期天数"), minimum=1
            )
            product.inbound_valid_days = self._integer(
                row_number, "入库有效天数", values.get("入库有效天数"), minimum=1
            )
            product.expiry_warning_days = self._integer(
                row_number, "效期预警天数", values.get("效期预警天数"), minimum=1
            )
            product.fefo_required = self._boolean(
                row_number, "FEFO", values.get("FEFO"), True
            )
        else:
            product.expiry_basis = None
            product.shelf_life_days = None
            product.inbound_valid_days = None
            product.expiry_warning_days = None
            product.fefo_required = False

        package_qty_value = values.get("包装换算数量")
        package_qty = self._integer(
            row_number,
            "包装换算数量",
            package_qty_value,
            minimum=1,
        )
        package_barcode = self._optional_text(
            row_number, "包装条码", values.get("包装条码"), max_length=50
        )
        package_data = None
        package_requested = (
            bool(_text(package_uom_value))
            or bool(_text(package_qty_value))
            or package_barcode is not None
            or bool(_text(values.get("采购默认")))
            or bool(_text(values.get("销售默认")))
        )
        if package_requested:
            if package_uom is None and not _text(package_uom_value):
                self._error(
                    row_number,
                    "包装单位编码",
                    "填写包装信息时包装单位不能为空。",
                )
            if package_qty is None and not _text(package_qty_value):
                self._error(
                    row_number,
                    "包装换算数量",
                    "填写包装信息时换算数量不能为空。",
                )
            if package_uom is not None and package_qty is not None:
                if package_uom.pk == base_uom.pk and package_qty != 1:
                    self._error(
                        row_number,
                        "包装换算数量",
                        "包装单位与基本单位相同时，换算数量必须为 1。",
                    )
                package_data = {
                    "uom": package_uom,
                    "qty_in_base": package_qty,
                    "barcode": package_barcode,
                    "is_pickable": True,
                    "is_purchase_default": self._nullable_boolean(
                        row_number, "采购默认", values.get("采购默认")
                    ),
                    "is_sales_default": self._nullable_boolean(
                        row_number, "销售默认", values.get("销售默认")
                    ),
                }

        if len(self.errors) == before_error_count:
            try:
                product.full_clean()
            except DjangoValidationError as exc:
                self._add_validation_errors(row_number, exc)
        if len(self.errors) == before_error_count and package_data:
            package = ProductPackage(product=product, **package_data)
            try:
                package.full_clean(
                    exclude={"product"},
                )
            except DjangoValidationError as exc:
                self._add_validation_errors(row_number, exc)
        if len(self.errors) != before_error_count:
            return None
        return ParsedProductRow(
            row_number=row_number, product=product, package_data=package_data
        )

    def _resolve_owner(self, row_number: int, value) -> Owner | None:
        code = _text(value).upper()
        if not code:
            self._error(row_number, "货主编码", "不能为空。")
            return None
        if code not in self._owner_cache:
            self._owner_cache[code] = Owner.objects.filter(
                code__iexact=code, is_active=True
            ).first()
        owner = self._owner_cache[code]
        if owner is None:
            self._error(row_number, "货主编码", f"找不到启用的货主：{code}。")
            return None
        if not self.access.allows_owner(owner.pk):
            self._error(row_number, "货主编码", "当前账号无权为该货主导入商品。")
            return None
        return owner

    def _resolve_uom(self, row_number, field, value, *, required):
        code = _text(value).upper()
        if not code:
            if required:
                self._error(row_number, field, "不能为空。")
            return None
        if code not in self._uom_cache:
            self._uom_cache[code] = ProductUom.objects.filter(
                code__iexact=code, is_active=True
            ).first()
        uom = self._uom_cache[code]
        if uom is None:
            self._error(row_number, field, f"找不到启用的单位：{code}。")
        return uom

    def _resolve_category(self, row_number, value):
        code = _text(value).upper()
        if not code:
            self._error(row_number, "分类编码", "不能为空；新商品至少需要选择一个大类。")
            return None
        if code not in self._category_cache:
            self._category_cache[code] = ProductCategory.objects.filter(
                code__iexact=code, is_active=True
            ).first()
        category = self._category_cache[code]
        if category is None:
            self._error(row_number, "分类编码", f"找不到启用的分类：{code}。")
        elif not category.has_active_path():
            self._error(row_number, "分类编码", f"分类链存在停用分类：{code}。")
            return None
        return category

    def _resolve_brand(self, row_number, value):
        code = _text(value).upper()
        if not code:
            return None
        if code not in self._brand_cache:
            self._brand_cache[code] = Brand.objects.filter(
                code__iexact=code, is_active=True
            ).first()
        brand = self._brand_cache[code]
        if brand is None:
            self._error(row_number, "品牌编码", f"找不到启用的品牌：{code}。")
        return brand

    def _required_code(self, row, field, value, *, max_length):
        code = self._optional_code(row, field, value, max_length=max_length)
        if not code:
            self._error(row, field, "不能为空。")
        return code

    def _optional_code(self, row, field, value, *, max_length):
        result = _text(value).upper()
        if len(result) > max_length:
            self._error(row, field, f"长度不能超过 {max_length} 个字符。")
            return None
        return result or None

    def _required_text(self, row, field, value, *, max_length):
        result = self._optional_text(row, field, value, max_length=max_length)
        if not result:
            self._error(row, field, "不能为空。")
        return result

    def _optional_text(self, row, field, value, *, max_length=None):
        result = _text(value)
        if max_length is not None and len(result) > max_length:
            self._error(row, field, f"长度不能超过 {max_length} 个字符。")
            return None
        return result or None

    def _decimal(self, row, field, value, *, minimum=None, maximum=None):
        text = _text(value)
        if not text:
            return None
        try:
            result = Decimal(text)
        except (InvalidOperation, ValueError):
            self._error(row, field, "必须是有效数字。")
            return None
        if not result.is_finite():
            self._error(row, field, "必须是有限数字。")
            return None
        if minimum is not None and result < minimum:
            self._error(row, field, f"不能小于 {minimum}。")
        if maximum is not None and result > maximum:
            self._error(row, field, f"不能大于 {maximum}。")
        return result

    def _integer(self, row, field, value, *, minimum=None):
        text = _text(value)
        if not text:
            return None
        try:
            decimal_value = Decimal(text)
            if decimal_value != decimal_value.to_integral_value():
                raise InvalidOperation
            result = int(decimal_value)
        except (InvalidOperation, ValueError, OverflowError):
            self._error(row, field, "必须是整数。")
            return None
        if minimum is not None and result < minimum:
            self._error(row, field, f"不能小于 {minimum}。")
        return result

    def _boolean(self, row, field, value, default):
        result = self._nullable_boolean(row, field, value)
        return default if result is None else result

    def _nullable_boolean(self, row, field, value):
        text = _text(value).lower()
        if not text:
            return None
        if text in {"是", "1", "true", "t", "yes", "y", "启用", "开启"}:
            return True
        if text in {"否", "0", "false", "f", "no", "n", "停用", "关闭"}:
            return False
        self._error(row, field, "只能填写 是/否、1/0 或 true/false。")
        return None

    def _check_file_duplicates(self, row_number: int, row_values, seen) -> None:
        owner_code = _text(row_values.get("货主编码")).upper()
        owner = self._owner_cache.get(owner_code) if owner_code else None
        owner_key: object = (
            owner.pk
            if owner is not None
            else owner_code or "missing-owner"
        )
        code = _text(row_values.get("商品编号")).upper()
        identifiers = (
            ("商品编号", code),
            ("GTIN", _text(row_values.get("GTIN"))),
            ("零码", _text(row_values.get("零码"))),
            ("箱码", _text(row_values.get("箱码"))),
            ("外部系统编码", _text(row_values.get("外部系统编码"))),
        )
        for field, value in identifiers:
            if not value:
                continue
            key = (owner_key, field, str(value).upper())
            previous = seen.get(key)
            if previous:
                self._error(row_number, field, f"与第 {previous} 行重复。")
            else:
                seen[key] = row_number

    def _add_validation_errors(self, row_number, exc: DjangoValidationError) -> None:
        if hasattr(exc, "message_dict"):
            for field, messages in exc.message_dict.items():
                label = MODEL_FIELD_LABELS.get(field, field)
                for message in messages:
                    self._error(row_number, label, str(message))
            return
        for message in exc.messages:
            self._error(row_number, "整行", str(message))

    def _error(self, row, field, message):
        self.errors.append({"row": row, "field": field, "message": message})

    def _persist(
        self, rows: list[ParsedProductRow], filename: str
    ) -> list[dict[str, Any]]:
        created = []
        with transaction.atomic():
            for row in rows:
                product = row.product
                product.full_clean()
                product.save()
                if row.package_data:
                    package = ProductPackage(
                        product=product,
                        created_by=self.user,
                        updated_by=self.user,
                        **row.package_data,
                    )
                    package.full_clean()
                    package.save()
                created.append(
                    {
                        "row": row.row_number,
                        "product_id": product.pk,
                        "owner_code": product.owner.code,
                        "code": product.code,
                        "name": product.name,
                    }
                )
            owner_ids = sorted({row.product.owner_id for row in rows})
            record_audit_event(
                action="products.import_excel",
                module="products",
                request=self.request,
                user=self.user,
                owner_id=owner_ids[0] if len(owner_ids) == 1 else None,
                succeeded=True,
                after={"product_ids": [item["product_id"] for item in created]},
                metadata={
                    "filename": Path(filename).name,
                    "total_rows": self.total_rows,
                    "created_count": len(created),
                    "skipped_count": len(self.skipped),
                },
            )
        return created

    def _result(self, *, created):
        return {
            "total_rows": self.total_rows,
            "created_count": len(created),
            "skipped_count": len(self.skipped),
            "error_count": len(self.errors),
            "created": created,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value)).strip()
    return str(value).strip()


def _joined_text(*parts: str) -> str:
    return "".join(parts)


def _validation_text(exc: DjangoValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return "; ".join(
            f"{MODEL_FIELD_LABELS.get(field, field)}: "
            f"{', '.join(str(message) for message in messages)}"
            for field, messages in exc.message_dict.items()
        )
    return "; ".join(str(message) for message in exc.messages)
