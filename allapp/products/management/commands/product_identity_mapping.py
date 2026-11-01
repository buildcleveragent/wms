"""Export, preview and apply explicit product identity mappings without receiving stock."""

import csv
import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from allapp.core.product_identity import require_product_snapshots
from allapp.products.models import Brand, Product

HEADERS = (
    "商品编码",
    "原商品名称",
    "原品牌编码",
    "原型号",
    "原规格",
    "新商品名称",
    "新品牌编码",
    "新型号",
)


def csv_text(value):
    value = str(value or "")
    return "'" + value if value.startswith(("'", "=", "+", "-", "@", "\t", "\r")) else value


def unquote_csv_text(value):
    value = str(value or "")
    return (
        value[1:]
        if value.startswith("'") and value[1:].startswith(("'", "=", "+", "-", "@", "\t", "\r"))
        else value
    )


def identity_row(product):
    return [
        product.name,
        product.brand.code if product.brand_id else "",
        product.model,
        product.spec or "",
    ]


class Command(BaseCommand):
    help = "按货主和商品编码整理通用品名；默认只预检，--apply 才写入。"

    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True, help="货主编码")
        parser.add_argument("--export", help="导出待人工填写的 CSV 映射表")
        parser.add_argument("--input", help="已填写的 CSV 映射表，最多 1000 行")
        parser.add_argument("--report", help="变更预览/结果 JSON 文件（导入时必填）")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if bool(options["export"]) == bool(options["input"]):
            raise CommandError("请选择 --export 或 --input。")
        products = Product.objects.filter(owner__code__iexact=options["owner"]).select_related(
            "brand"
        )
        if not products.exists():
            raise CommandError("该货主不存在可整理的商品。")
        if options["export"]:
            if options["apply"]:
                raise CommandError("--export 不接受 --apply。")
            with Path(options["export"]).open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(HEADERS)
                for product in products.order_by("code"):
                    old = identity_row(product)
                    writer.writerow([csv_text(value) for value in [product.code, *old, *old[:3]]])
            self.stdout.write("已导出映射表；请填写新名称、品牌编码和型号。")
            return
        if not options["report"]:
            raise CommandError("--input 必须提供 --report，保留预览及变更记录。")
        with Path(options["input"]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != HEADERS:
                raise CommandError("映射表表头不正确，请使用 --export 生成。")
            rows = []
            for row in reader:
                rows.append({key: unquote_csv_text(value) for key, value in row.items()})
                if len(rows) > 1000:
                    raise CommandError("一次最多整理 1000 条商品。")
        changes, errors, seen = [], [], set()
        # Reserve the report destination before any business writes.
        with Path(options["report"]).open("x", encoding="utf-8") as report:
            with transaction.atomic():
                for number, row in enumerate(rows, start=2):
                    code = (row.get("商品编码") or "").strip()
                    product = products.select_for_update().filter(code__iexact=code).first()
                    if product is None or product.pk in seen:
                        errors.append({"row": number, "error": "商品不存在或编码重复。"})
                        continue
                    seen.add(product.pk)
                    old = identity_row(product)
                    if old != [row[key] for key in HEADERS[1:5]]:
                        errors.append({"row": number, "error": "原资料已变化，请重新导出核对。"})
                        continue
                    name = (row.get("新商品名称") or "").strip()
                    brand_code = (row.get("新品牌编码") or "").strip()
                    model = (row.get("新型号") or "").strip()
                    brand = (
                        Brand.objects.filter(code__iexact=brand_code, is_active=True).first()
                        if brand_code
                        else None
                    )
                    if (
                        not name
                        or len(name) > 200
                        or len(model) > 200
                        or (brand_code and brand is None)
                    ):
                        errors.append({"row": number, "error": "名称、型号长度或品牌编码无效。"})
                        continue
                    new = [name, brand.code if brand else "", model, product.spec or ""]
                    if old == new:
                        continue
                    try:
                        require_product_snapshots([product.pk])
                        product.name, product.brand, product.model = name, brand, model
                        product.full_clean()
                    except ValidationError as exc:
                        errors.append({"row": number, "error": "; ".join(exc.messages)})
                        continue
                    changes.append({"code": product.code, "before": old, "after": new})
                    if options["apply"]:
                        product.save(update_fields=["name", "brand", "model", "updated_at"])
                if errors or not options["apply"]:
                    transaction.set_rollback(True)
                # A failed report write also rolls back all product changes.
                json.dump(
                    {
                        "owner": options["owner"],
                        "applied": None if options["apply"] and not errors else False,
                        "changes": changes,
                        "errors": errors,
                    },
                    report,
                    ensure_ascii=False,
                    indent=2,
                )
                report.flush()
            if options["apply"] and not errors:
                try:
                    report.seek(0)
                    json.dump(
                        {
                            "owner": options["owner"],
                            "applied": True,
                            "changes": changes,
                            "errors": [],
                        },
                        report,
                        ensure_ascii=False,
                        indent=2,
                    )
                    report.truncate()
                    report.flush()
                except OSError as exc:
                    raise CommandError(
                        "商品已更新，但报告完成标记写入失败；请按报告中的变更核对资料。"
                    ) from exc
        if errors:
            raise CommandError(f"{len(errors)} 条预检错误；整批未写入，请查看报告。")
        self.stdout.write(
            f"{'已更新' if options['apply'] else '预检通过，未写入'}：{len(changes)} 条商品。"
        )
