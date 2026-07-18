import logging

from django.http import HttpResponse, JsonResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import (
    SAFE_METHODS,
    DjangoModelPermissions,
    IsAuthenticated,
)
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response

from .excel_import import ProductImportConflictError, ProductImportFileError
from .models import Product, ProductPackage
from .permissions import can_manage_all_owner_products, can_view_all_owner_products
from .serializers import ProductSerializer
from .views_excel import import_product_file, product_template_response

# ✅ 补上资源导入（若资源缺失则统一给出 501 提示，避免 NameError）
try:
    from .resources import ProductResource
except Exception:  # pragma: no cover
    ProductResource = None


class CSVTemplateRenderer(BaseRenderer):
    """Allow DRF's reserved ``?format=csv`` query parameter for this action."""

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data if isinstance(data, (bytes, str)) else b""


class XLSXTemplateRenderer(BaseRenderer):
    """Allow callers that explicitly request ``?format=xlsx``."""

    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    format = "xlsx"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data if isinstance(data, bytes) else b""


# ===== 多租户隔离：非超管仅看自己 owner =====
class OwnerScopedMixin:
    permission_classes = [IsAuthenticated, DjangoModelPermissions]
    owner_path = "owner"

    def has_all_owner_scope(self):
        user = self.request.user
        if self.request.method in SAFE_METHODS:
            return can_view_all_owner_products(user)
        return can_manage_all_owner_products(user)

    def get_queryset(self):
        qs = super().get_queryset()  # type: ignore[attr-defined]
        user = (
            getattr(self, "request", None).user
            if getattr(self, "request", None)
            else None
        )
        if not user or not user.is_authenticated:
            return qs.none()
        if self.has_all_owner_scope():
            return qs
        owner_id = getattr(user, "owner_id", None)
        return (
            qs.filter(**{f"{self.owner_path}_id": owner_id}) if owner_id else qs.none()
        )

    def perform_create(self, serializer):
        user = self.request.user
        if self.has_all_owner_scope():
            serializer.save()
            return
        serializer.save(owner=getattr(user, "owner", None))

    def perform_update(self, serializer):
        user = self.request.user
        if self.has_all_owner_scope():
            serializer.save()
            return
        serializer.save(owner=getattr(user, "owner", None))


class ProductViewSet(OwnerScopedMixin, viewsets.ModelViewSet):
    queryset = Product.objects.all().select_related("owner")
    serializer_class = ProductSerializer
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
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

    def get_renderers(self):
        # The router applies action-specific renderer classes automatically.
        # Direct ``as_view`` callers (including legacy integrations and tests)
        # do not, so keep the reserved ?format=csv/xlsx parameters working here.
        if getattr(self, "action", None) == "template":
            return [
                JSONRenderer(),
                CSVTemplateRenderer(),
                XLSXTemplateRenderer(),
            ]
        return super().get_renderers()

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

    # 模板下载（默认 Excel；保留 ?format=csv 兼容旧调用方）
    @action(
        methods=["GET"],
        detail=False,
        url_path="template",
        renderer_classes=[JSONRenderer, CSVTemplateRenderer, XLSXTemplateRenderer],
    )
    def template(self, request):
        if request.query_params.get("format", "xlsx").lower() != "csv":
            return product_template_response(request.user)
        headers = [
            "owner_code",  # 货主编码（必要）
            "code",
            "name",
            "spec",
            "unit_barcode",
            "carton_barcode",
            "external_code",
            "base_unit",
            "aux_unit",
            "unit_ratio",
            "volume",
            "weight",
            "aux_volume",
            "aux_weight",
            "min_stock",
            "max_stock",
            "batch_control",
            "expiry_control",
            "shelf_life_days",
            "inbound_valid_days",
            "expiry_warning_days",
            "is_active",
        ]
        content = ",".join(headers) + "\r\n"
        resp = HttpResponse(content, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = (
            'attachment; filename="product_import_template.csv"'
        )
        # Backward-compatible for old tests still reading HttpResponse._headers.
        resp._headers = {  # type: ignore[attr-defined]
            "content-type": ("Content-Type", resp["Content-Type"]),
            "content-disposition": ("Content-Disposition", resp["Content-Disposition"]),
        }
        return resp

    # 导入（统一使用 PDA 的 XLSX 导入服务）
    @action(
        methods=["POST"],
        detail=False,
        url_path="import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_file(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response(
                {"detail": "缺少文件 file"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            result = import_product_file(
                uploaded_file=f,
                user=request.user,
                request=request,
            )
        except ProductImportConflictError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ProductImportFileError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            result,
            status=(
                status.HTTP_400_BAD_REQUEST
                if result["error_count"]
                else status.HTTP_200_OK
            ),
        )

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
        base_unit_code = (
            getattr(getattr(product, "base_uom", None), "code", "") or "PCS"
        )
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


logger = logging.getLogger(__name__)


def get_product_details(request, product_id):
    try:
        # 获取商品
        product = Product.objects.get(id=product_id)

        # 获取商品的基本单位
        base_uom = product.base_uom.name  # 获取商品的基本单位

        # 获取商品所有的包装单位及其换算数量
        product_packages = ProductPackage.objects.filter(
            product=product
        )  # 获取与商品关联的所有包装单位

        # 打包单位列表
        pack_uoms = [
            {
                "uom": package.uom.name,  # 包装单位名称
                "pack_qty": package.qty_in_base,  # 换算数量，使用 qty_in_base 字段
                "unit": package.uom.name,  # 计量单位
            }
            for package in product_packages
        ]
        logger.debug(
            "products.product_details.loaded product_id=%s package_count=%s",
            product.id,
            len(pack_uoms),
        )
        return JsonResponse({"base_uom": base_uom, "pack_uoms": pack_uoms})
    except Product.DoesNotExist:
        return JsonResponse({"error": "Product not found"}, status=404)
