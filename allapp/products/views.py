# allapp/products/views.py  可直接覆盖版
import io
import tablib
from django.db import transaction
from django.http import HttpResponse
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions
from django_filters.rest_framework import DjangoFilterBackend

from .models import Product
from .serializers import ProductSerializer

# ✅ 补上资源导入（若资源缺失则统一给出 501 提示，避免 NameError）
try:
    from .resources import ProductResource
except Exception:  # pragma: no cover
    ProductResource = None


# ===== 多租户隔离：非超管仅看自己 owner =====
class OwnerScopedMixin:
    permission_classes = [IsAuthenticated, DjangoModelPermissions]
    owner_path = "owner"

    def get_queryset(self):
        qs = super().get_queryset()  # type: ignore[attr-defined]
        user = getattr(self, "request", None).user if getattr(self, "request", None) else None
        if not user or not user.is_authenticated:
            return qs.none()
        if user.is_superuser:
            return qs
        owner_id = getattr(user, "owner_id", None)
        return qs.filter(**{f"{self.owner_path}_id": owner_id}) if owner_id else qs.none()

    def perform_create(self, serializer):
        extra = {}
        user = self.request.user
        if "owner" in serializer.fields and not user.is_superuser:
            extra["owner"] = getattr(user, "owner", None)
        serializer.save(**extra)


class ProductViewSet(OwnerScopedMixin, viewsets.ModelViewSet):
    queryset = Product.objects.all().select_related("owner")
    serializer_class = ProductSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "code": ["exact", "icontains"],
        "name": ["icontains"],
        "is_active": ["exact"],
        "batch_control": ["exact"],
        "expiry_control": ["exact"],
    }
    search_fields = ("code", "name", "unit_barcode", "carton_barcode", "external_code")
    ordering_fields = ("owner", "code", "name", "updated_at")
    ordering = ("owner", "code")

    # 批量启用
    @action(methods=["POST"], detail=False, url_path="bulk-activate")
    def bulk_activate(self, request):
        ids = request.data.get("ids", [])
        updated = self.get_queryset().filter(id__in=ids).update(is_active=True)
        return Response({"updated": updated})

    # 批量禁用
    @action(methods=["POST"], detail=False, url_path="bulk-deactivate")
    def bulk_deactivate(self, request):
        ids = request.data.get("ids", [])
        updated = self.get_queryset().filter(id__in=ids).update(is_active=False)
        return Response({"updated": updated})

    # 模板下载（CSV表头）
    @action(methods=["GET"], detail=False, url_path="template")
    def template(self, request):
        headers = [
            "owner_code",  # 货主编码（必要）
            "code", "name", "spec",
            "unit_barcode", "carton_barcode", "external_code",
            "base_unit", "aux_unit", "unit_ratio",
            "volume", "weight", "aux_volume", "aux_weight",
            "min_stock", "max_stock",
            "batch_control", "expiry_control",
            "shelf_life_days", "inbound_valid_days", "expiry_warning_days",
            "is_active",
        ]
        dataset = tablib.Dataset(headers=headers)
        content = dataset.export("csv")
        resp = HttpResponse(content, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="product_import_template.csv"'
        # Backward-compatible for old tests still reading HttpResponse._headers.
        resp._headers = {  # type: ignore[attr-defined]
            "content-type": ("Content-Type", resp["Content-Type"]),
            "content-disposition": ("Content-Disposition", resp["Content-Disposition"]),
        }
        return resp

    # 导入（支持 csv/xls/xlsx）
    @action(methods=["POST"], detail=False, url_path="import")
    def import_file(self, request):
        """
        前端以 multipart/form-data 上传文件，字段名：file
        依赖 .resources.ProductResource 完成字段映射与校验
        """
        if ProductResource is None:
            return Response(
                {"detail": "未找到 ProductResource，请在 allapp/products/resources.py 中定义并确保可导入"},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "缺少文件 file"}, status=status.HTTP_400_BAD_REQUEST)

        ext = (f.name.split(".")[-1] or "").lower()
        data = f.read()
        try:
            dataset = tablib.Dataset().load(data, format=ext)
        except Exception:
            try:
                dataset = tablib.Dataset().load(data.decode("utf-8"), format="csv")
            except Exception as e:
                return Response({"detail": f"无法解析文件: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        resource = ProductResource()
        # 试运行
        result = resource.import_data(dataset, dry_run=True, raise_errors=False)
        if result.has_errors() or result.has_validation_errors():
            errors = []
            for row in getattr(result, "invalid_rows", []):
                errors.append({"row": row.number, "error": str(row.error)})
            for row in getattr(result, "rows", []):
                if getattr(row, "import_type", "") == "error":
                    errors.append({"row": row.number, "error": str(row.error)})
            return Response({"detail": "导入校验失败", "errors": errors}, status=400)

        # 真正导入
        with transaction.atomic():
            resource.import_data(dataset, dry_run=False, raise_errors=True)

        return Response({"imported": len(dataset)})

    # 导出（支持 csv/xls/xlsx）
    @action(methods=["GET"], detail=False, url_path="export")
    def export_file(self, request):
        if ProductResource is None:
            return Response(
                {"detail": "未找到 ProductResource，无法导出"},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        fmt = request.query_params.get("format", "xlsx").lower()
        if fmt not in ("csv", "xls", "xlsx"):
            fmt = "xlsx"

        qs = self.filter_queryset(self.get_queryset())
        resource = ProductResource()
        dataset = resource.export(qs)

        if fmt == "csv":
            content = dataset.export("csv")
            ct = "text/csv; charset=utf-8"
            filename = "products.csv"
            data = content
        elif fmt == "xls":
            data = dataset.export("xls")
            ct = "application/vnd.ms-excel"
            filename = "products.xls"
        else:
            data = dataset.export("xlsx")
            ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = "products.xlsx"

        resp = HttpResponse(data, content_type=ct)
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    # 条码打印（示例：返回简单ZPL文本）
    @action(methods=["GET"], detail=True, url_path="barcode")
    def barcode(self, request, pk=None):
        product = self.get_object()
        # 与产品模型对齐：用 base_uom.code；单位比例可按需从 package 推导，这里先省略
        base_unit_code = getattr(getattr(product, "base_uom", None), "code", "") or "PCS"
        data_code = product.unit_barcode or product.carton_barcode or product.code
        zpl = f"""
^XA
^CI28
^PW600
^LH0,0
^FO40,40^A0N,36,36^FD{product.code} {product.name}^FS
^FO40,100^BCN,100,Y,N,N
^FD{data_code}^FS
^FO40,220^A0N,28,28^FD单位:{base_unit_code}^FS
^XZ
""".strip()
        return Response({"type": "zpl", "content": zpl})


# allapp/products/views.py
from django.http import JsonResponse
from allapp.products.models import Product, ProductPackage


def get_product_details(request, product_id):
    try:
        # 获取商品
        product = Product.objects.get(id=product_id)

        # 获取商品的基本单位
        base_uom = product.base_uom.name  # 获取商品的基本单位

        # 获取商品所有的包装单位及其换算数量
        product_packages = ProductPackage.objects.filter(product=product)  # 获取与商品关联的所有包装单位

        # 打包单位列表
        pack_uoms = [
            {
                "uom": package.uom.name,  # 包装单位名称
                "pack_qty": package.qty_in_base,  # 换算数量，使用 qty_in_base 字段
                "unit": package.uom.name  # 计量单位
            }
            for package in product_packages
        ]
        print("get_product_details get_product_details get_product_details")
        return JsonResponse({
            "base_uom": base_uom,
            "pack_uoms": pack_uoms
        })
    except Product.DoesNotExist:
        return JsonResponse({"error": "Product not found"}, status=404)
