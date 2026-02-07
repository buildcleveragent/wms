# allapp/outbound/views.py  或  allapp/outbound/api_views.py
from django.apps import apps
from datetime import datetime
from django.db.models import Q, Sum, Prefetch
import logging
from django.db.models import F
from rest_framework.exceptions import ValidationError
from ..products.models import ProductPackage
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError
from django.utils import timezone



logger = logging.getLogger(__name__)
from django.utils import timezone
from rest_framework import viewsets, mixins, status
from rest_framework.pagination import PageNumberPagination

from .models import OutboundOrder
from . import services as ob_services  # 你现有的出库业务服务（分配等）
from .serializers import (OutboundOrderCreateSerializer, OutboundOrderReadSerializer)
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from allapp.tasking.models import WmsTask, WmsTaskLine
from allapp.tasking import services as task_services
from allapp.tasking.services import _run_posting_handler, adjust_pick_line_qty


def _q3(x) -> Decimal:
    # 你项目里已有的数量标准化函数
    return _q3_impl(x)

class DefaultPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

class ProductViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination

    def list(self, request, *args, **kwargs):
        Product   = apps.get_model("products", "Product")
        InvDetail = apps.get_model("inventory", "InventoryDetail")

        owner_id = getattr(request.user, "owner_id", None)
        if not owner_id:
            owner_id = request.query_params.get("owner")
            print("owner_id:",owner_id)
            if not owner_id:
                # return self.get_paginated_response([])
                print("not owner_id:")
                return Response([])

        # ✅ 只预取“当前查询命中的产品”的包装，并连带 uom，限制字段，避免 N+1
        pkg_qs = (ProductPackage.objects
                  .select_related("uom")
                  .only("id", "product_id", "uom_id", "qty_in_base", "barcode",
                        "length_cm", "width_cm", "height_cm",
                        "gross_weight_kg", "volume_m3",
                        "is_purchase_default", "sort_order",
                        "uom__name", "uom__code"))

        qs = (Product.objects
              .filter(owner_id=owner_id)
              .select_related("base_uom", "replenish_uom",)
              .only("id", "owner_id", "code", "name", "sku", "spec","product_image","gtin","min_price","max_discount",
                    "base_uom__code","base_uom__name", "replenish_uom__code", "replenish_uom__name","replenish_uom_id")
              .order_by("id"))

        q = request.query_params.get("search")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(code__icontains=q) |
                Q(sku__icontains=q) |
                Q(gtin__icontains=q) |
                Q(unit_barcode__icontains=q) |
                Q(carton_barcode__icontains=q)
            )

        page = self.paginate_queryset(qs)
        if not page:
            return self.get_paginated_response([])

        pid_list = [p.id for p in page]
        wh_id = request.query_params.get("warehouse_id")
        inv_filter = {"owner_id": owner_id, "product_id__in": pid_list}
        if wh_id:
            inv_filter["warehouse_id"] = int(wh_id)

        rows = (InvDetail.objects
                .filter(**inv_filter)
                .values("product_id")
                .annotate(avail=Sum("available_qty")))
        avail_map = {r["product_id"]: r["avail"] for r in rows}

        def carton_info(p):
            unit_code = getattr(getattr(p, "replenish_uom", None), "code", None)
            conv = None
            if unit_code and hasattr(p, "packages"):
                pkg = p.packages.filter(uom_id=p.replenish_uom_id).only("id", "base_qty", "multiplier").first()
                if pkg is not None:
                    conv = getattr(pkg, "base_qty", None) or getattr(pkg, "multiplier", None)
            return unit_code, conv

        def default_sales_uom(p):
            # 查找 product 的所有 package，返回 is_sales_default 为 True 的 UOM 的名称
            default_package = p.packages.filter(is_sales_default=True).first()
            if default_package:
                return default_package.uom.name,default_package.qty_in_base
            return None


        def product_packaging(p):
            """获取商品所有包装信息，使用 ProductPackage 来表示包装"""
            packaging = []
            for pkg in p.packages.all():  # 使用 p.packages 获取商品的所有包装信息
                packaging.append({
                    'id': pkg.id,  # 包装的 ID
                    'uom_type': pkg.uom.name,  # 获取包装单位名称
                    'quantity_in_base': pkg.qty_in_base,  # 获取换算数量
                    'barcode': pkg.barcode,  # 获取条码
                    'length_cm': pkg.length_cm,  # 获取包装尺寸
                    'width_cm': pkg.width_cm,
                    'height_cm': pkg.height_cm,
                    'gross_weight_kg': pkg.gross_weight_kg,  # 获取毛重
                    'volume_m3': pkg.volume_m3,  # 获取体积
                })
            return packaging

        # === 在服务端拼出 _unitOptions / _selectedUnitIndex（最小新增逻辑） ===
        def build_unit_options(p, packaging):
            base_name = (getattr(getattr(p, "base_uom", None), "name", None) or
                         getattr(getattr(p, "base_uom", None), "code", None))
            opts = []
            if base_name:
                opts.append({
                    "key": "BASE",
                    "kind": "base",
                    "label": base_name,
                    "multiplier": 1,
                    "package_id": None,
                    "barcode": None,
                })
            for row in packaging:
                # 跳过与基本单位 1:1 的冗余项
                if row["quantity_in_base"] == 1 and row["uom_type"] == base_name:
                    continue
                opts.append({
                    "key": row["id"],
                    "kind": "package",
                    "label": row["uom_type"],
                    "multiplier": row["quantity_in_base"],
                    "package_id": row["id"],
                    "barcode": row["barcode"],
                })
            return opts

        def default_selected_index(packaging, unit_opts):
            # 优先选择 is_sales_default 的包装；否则 0（通常为基本单位）
            sales_pkg_id = next((r["id"] for r in packaging if r.get("is_sales_default")), None)
            if sales_pkg_id is not None:
                for i, o in enumerate(unit_opts):
                    if o["package_id"] == sales_pkg_id:
                        return i
            return 0


        data = []
        for p in page:
            packaging = product_packaging(p)
            unit_opts = build_unit_options(p, packaging)  # ← 基本单位 + 包装
            sel_idx = default_selected_index(packaging, unit_opts)
            sel_unit = unit_opts[sel_idx] if unit_opts else None


            carton_unit, carton_conv = carton_info(p)

            if default_sales_uom(p):
              aux_uom_name,aux_qty_in_base=default_sales_uom(p)
            else:
              aux_uom_name=None
              aux_qty_in_base=None

            product_image_url = None
            if p.product_image:
                print("ProductViewSet ProductViewSet")
                product_image_url = request.build_absolute_uri(p.product_image.url)
                # product_image_url = "http://192.168.1.6:8001"+p.product_image.url  # 获取图片的 URL 地址

            if avail_map.get(p.id, 0)>0:
                data.append({
                    "id": p.id,
                    "sku": p.sku or p.code or "",
                    "name": p.name or "",
                    "spec": p.spec,
                    "base_unit": getattr(getattr(p, "base_uom", None), "code", None),
                    "base_unit_name": getattr(getattr(p, "base_uom", None), "name", None),
                    "carton_unit": carton_unit,
                    "carton_conv": carton_conv,
                    "available": avail_map.get(p.id, 0),
                    "price": getattr(p, "price", None) or getattr(p, "sale_price", None) or 0,
                    "product_image_url":product_image_url,
                    "gtin":p.gtin,
                    "aux_uom_name":aux_uom_name,
                    "aux_qty_in_base":aux_qty_in_base,
                    "max_discount": p.max_discount ,
                    "product_min_price": p.min_price,
                    "unitOptions": unit_opts,
                    "selectedUnitIndex": sel_idx,
                    "base_quantity": 0,
                })
        print("data", data)
        logger.debug("ProductViewSet data ",data)
        return self.get_paginated_response(data)

class CustomerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination     # 你项目里已有的分页类
    filter_backends = []
    queryset = apps.get_model("baseinfo", "Customer").objects.none()

    def get_queryset(self):
        Customer = apps.get_model("baseinfo", "Customer")
        user = self.request.user

        qs = Customer.objects.filter(salesperson=user)
        if getattr(user, "owner_id", None):
            qs = qs.filter(owner_id=user.owner_id)

        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

        return qs.order_by("id")

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        data = [{"id": c.id, "code": c.code, "name": c.name} for c in page]
        return self.get_paginated_response(data)

class OwnerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination     # 你项目里已有的分页类
    filter_backends = []
    queryset = apps.get_model("baseinfo", "Owner").objects.none()

    def get_queryset(self):
        Owner = apps.get_model("baseinfo", "Owner")
        user = self.request.user

        qs = Owner.objects.all()
        # if getattr(user, "owner_id", None):
        #     qs = qs.filter(owner_id=user.owner_id)

        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

        return qs.order_by("id")

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        data = [{"id": c.id, "code": c.code, "name": c.name} for c in page]
        return self.get_paginated_response(data)

class SupplierViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination     # 你项目里已有的分页类
    filter_backends = []
    queryset = apps.get_model("baseinfo", "Supplier").objects.none()

    def get_queryset(self):
        Supplier = apps.get_model("baseinfo", "Supplier")
        user = self.request.user

        qs = Supplier.objects.all()
        # if getattr(user, "owner_id", None):
        #     qs = qs.filter(owner_id=user.owner_id)

        q = self.request.query_params.get("search")
        o = self.request.query_params.get("owner")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

        if o:
            qs = qs.filter(Q(owner_id=o))
        else:

            raise ValidationError({"detail": "owner 参数是必需的"})

        return qs.order_by("id")

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        data = [{"id": c.id, "code": c.code, "name": c.name} for c in page]
        return self.get_paginated_response(data)

class ReceiveProductViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination

    def list(self, request, *args, **kwargs):
        Product   = apps.get_model("products", "Product")
        ProductPackage = apps.get_model("products", "ProductPackage")

        owner_id = request.query_params.get("owner")
        print("owner_id:",owner_id)
        if not owner_id:
            # return self.get_paginated_response([])
            print("not owner_id:")
            return Response([])

        # 预取 packages + uom，减少 N+1
        # pkg_qs = (ProductPackage.objects
        #           .select_related("uom")
        #           .only("id","product_id","uom_id","qty_in_base","barcode",
        #                 "is_sales_default","sort_order",
        #                 "uom__code","uom__name"))

        # ✅ 只预取“当前查询命中的产品”的包装，并连带 uom，限制字段，避免 N+1
        pkg_qs = (ProductPackage.objects
                  .select_related("uom")
                  .only("id", "product_id", "uom_id", "qty_in_base", "barcode",
                        "length_cm", "width_cm", "height_cm",
                        "gross_weight_kg", "volume_m3",
                        "is_purchase_default", "sort_order",
                        "uom__name", "uom__code"))


        # qs = (Product.objects
        #       .filter(owner_id=owner_id)
        #       .select_related("base_uom", "replenish_uom",)
        #       .only("id", "owner_id", "code", "name", "sku", "spec","product_image","gtin","min_price","max_discount",
        #             "base_uom__code","base_uom__name", "replenish_uom__code", "replenish_uom__name","replenish_uom_id")
        #       .order_by("id"))

        qs = (Product.objects
              .filter(owner_id=owner_id)
              .select_related("base_uom")
              .prefetch_related(Prefetch("packages", queryset=pkg_qs))
              .only("id", "owner_id", "code", "name", "sku", "spec", "product_image", "gtin",
                    "min_price", "max_discount", "base_uom__name", "base_uom__code")
              .order_by("id"))


        q = request.query_params.get("search")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(code__icontains=q) |
                Q(sku__icontains=q) |
                Q(gtin__icontains=q) |
                Q(unit_barcode__icontains=q) |
                Q(carton_barcode__icontains=q)
            ).distinct()



        page = self.paginate_queryset(qs)
        if not page:
            return self.get_paginated_response([])

        def carton_info(p):
            unit_code = getattr(getattr(p, "replenish_uom", None), "code", None)
            conv = None
            if unit_code and hasattr(p, "packages"):
                pkg = p.packages.filter(uom_id=p.replenish_uom_id).only("id", "base_qty", "multiplier").first()
                if pkg is not None:
                    conv = getattr(pkg, "base_qty", None) or getattr(pkg, "multiplier", None)
            return unit_code, conv

        def default_sales_uom(p):
            # 查找 product 的所有 package，返回 is_sales_default 为 True 的 UOM 的名称
            default_package = p.packages.filter(is_sales_default=True).first()
            if default_package:
                return default_package.uom.name,default_package.qty_in_base
            return None

        def product_packaging(p):
            """获取商品所有包装信息，使用 ProductPackage 来表示包装"""
            packaging = []
            for pkg in p.packages.all():  # 使用 p.packages 获取商品的所有包装信息
                packaging.append({
                    'id': pkg.id,  # 包装的 ID
                    'uom_type': pkg.uom.name,  # 获取包装单位名称
                    'quantity_in_base': pkg.qty_in_base,  # 获取换算数量
                    'barcode': pkg.barcode,  # 获取条码
                    'length_cm': pkg.length_cm,  # 获取包装尺寸
                    'width_cm': pkg.width_cm,
                    'height_cm': pkg.height_cm,
                    'gross_weight_kg': pkg.gross_weight_kg,  # 获取毛重
                    'volume_m3': pkg.volume_m3,  # 获取体积
                })
            return packaging

        # === 在服务端拼出 _unitOptions / _selectedUnitIndex（最小新增逻辑） ===
        def build_unit_options(p, packaging):
            base_name = (getattr(getattr(p, "base_uom", None), "name", None) or
                         getattr(getattr(p, "base_uom", None), "code", None))
            opts = []
            if base_name:
                opts.append({
                    "key": "BASE",
                    "kind": "base",
                    "label": base_name,
                    "multiplier": 1,
                    "package_id": None,
                    "barcode": None,
                })
            for row in packaging:
                # 跳过与基本单位 1:1 的冗余项
                if row["quantity_in_base"] == 1 and row["uom_type"] == base_name:
                    continue
                opts.append({
                    "key": row["id"],
                    "kind": "package",
                    "label": row["uom_type"],
                    "multiplier": row["quantity_in_base"],
                    "package_id": row["id"],
                    "barcode": row["barcode"],
                })
            return opts

        def default_selected_index(packaging, unit_opts):
            # 优先选择 is_sales_default 的包装；否则 0（通常为基本单位）
            sales_pkg_id = next((r["id"] for r in packaging if r.get("is_sales_default")), None)
            if sales_pkg_id is not None:
                for i, o in enumerate(unit_opts):
                    if o["package_id"] == sales_pkg_id:
                        return i
            return 0


        data = []
        for p in page:
            # product_packaging_info = product_packaging(p)
            packaging = product_packaging(p)
            unit_opts = build_unit_options(p, packaging)  # ← 基本单位 + 包装
            sel_idx = default_selected_index(packaging, unit_opts)
            sel_unit = unit_opts[sel_idx] if unit_opts else None

            carton_unit, carton_conv = carton_info(p)

            if default_sales_uom(p):
              aux_uom_name,aux_qty_in_base=default_sales_uom(p)
            else:
              aux_uom_name=None
              aux_qty_in_base=None

            product_image_url = None
            if p.product_image:
                print("ProductViewSet ProductViewSet")
                product_image_url = request.build_absolute_uri(p.product_image.url)
                # product_image_url = "http://192.168.1.6:8001"+p.product_image.url  # 获取图片的 URL 地址


            data.append({
                "id": p.id,
                "sku": p.sku or p.code or "",
                "name": p.name or "",
                "spec": p.spec,
                "base_unit": getattr(getattr(p, "base_uom", None), "code", None),
                "base_unit_name": getattr(getattr(p, "base_uom", None), "name", None),
                "carton_unit": carton_unit,
                "carton_conv": carton_conv,
                "price": getattr(p, "price", None) or getattr(p, "sale_price", None) or 0,
                "product_image_url":product_image_url,
                "gtin":p.gtin,
                "aux_uom_name":aux_uom_name,
                "aux_qty_in_base":aux_qty_in_base,
                "max_discount": p.max_discount ,
                "product_min_price": p.min_price,
                "packaging": packaging,  # 包装信息返回，包括 id

                # === 新增：用于前端单选/回填到购物车
                "unitOptions": unit_opts,
                "selectedUnitIndex": sel_idx,
                "base_quantity":0,
                # "selectedUnit": sel_unit,  # 若不想冗余，可不下发这个，前端用 index 取
            })
        print("data", data)
        # logger.debug("ReceiveProductViewSet data ",data)
        logger.debug("ReceiveProductViewSet data %s", data)
        return self.get_paginated_response(data)

class OutboundOrderViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = OutboundOrder.objects.all().order_by("-biz_date", "-id")
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination

    def get_serializer_class(self):
        if self.action == "create":
            return OutboundOrderCreateSerializer
        return OutboundOrderReadSerializer

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        q = request.query_params.get("search")
        owner_id = request.query_params.get("owner_id")
        warehouse_id = request.query_params.get("warehouse_id")
        submit_status = request.query_params.get("submit_status")
        approval_status = request.query_params.get("approval_status")

        if q:
            qs = qs.filter(
                Q(order_no__icontains=q) |
                Q(customer__name__icontains=q) | Q(customer__code__icontains=q) |
                Q(supplier__name__icontains=q)
            )
        if owner_id:      qs = qs.filter(owner_id=owner_id)
        if warehouse_id:  qs = qs.filter(warehouse_id=warehouse_id)
        if submit_status: qs = qs.filter(submit_status=submit_status)
        if approval_status: qs = qs.filter(approval_status=approval_status)

        page = self.paginate_queryset(qs)
        ser = OutboundOrderReadSerializer(page, many=True)
        return self.get_paginated_response(ser.data)

    def create(self, request, *args, **kwargs):
        ser = OutboundOrderCreateSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        order = ser.save()
        return Response(OutboundOrderReadSerializer(order).data, status=status.HTTP_201_CREATED)

    # 提交：DRAFT -> SUBMITTED
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        order = self.get_object()
        if getattr(order, "submit_status", None) != "DRAFT":
            return Response({"detail": "仅 DRAFT 可提交"}, status=400)
        order.submit_status = "SUBMITTED"
        order.save(update_fields=["submit_status"])
        return Response(OutboundOrderReadSerializer(order).data)

    # 货主管理员审核并尝试分配库存
    @action(detail=True, methods=["post"], url_path="owner-approve")
    def owner_approve(self, request, pk=None):
        order = self.get_object()
        # 状态名称以你的模型为准（这里给出常见约定）
        if getattr(order, "approval_status", None) not in ("OWNER_PENDING", "OWNER_REJECTED"):
            return Response({"detail": "当前状态不可进行货主管理员审核"}, status=400)

        order.approval_status = "OWNER_APPROVED"
        if hasattr(order, "approved_by_ownermanager"):
            order.approved_by_ownermanager = request.user
        if hasattr(order, "approved_at_ownermanager"):
            order.approved_at_ownermanager = timezone.now()
        order.save()

        # 分配库存 available->allocated（你现有服务）
        print("owner_approve order=",order)
        try:
            ob_services.allocate_inventory(order, by_user=request.user, allow_backorder=True)
        except Exception as e:
            # 分配失败不回滚审核（按需调整）
            return Response({"detail": f"审核通过，但分配库存失败：{e}"}, status=202)

        return Response(OutboundOrderReadSerializer(order).data)


# allapp/outbound/views.py 末尾附近增加



class PickTaskSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = WmsTask
        fields = [
            "id",
            "task_no",
            "task_type",
            "status",
            "owner_id",
            "owner_name",
            "warehouse_id",
            "warehouse_name",
            "remark",
            "review_status",
        ]

class PickTaskLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    from_loc_code = serializers.CharField(source="from_location.code", read_only=True)
    to_loc_code = serializers.CharField(source="to_location.code", read_only=True)

    class Meta:
        model = WmsTaskLine
        fields = [
            "id",
            "task_id",
            "product_id",
            "product_sku",
            "product_name",
            "from_location_id",
            "from_loc_code",
            "to_location_id",
            "to_loc_code",
            "qty_plan",
            "qty_done",
            "status",
        ]


class PickTaskViewSet(viewsets.ReadOnlyModelViewSet):
    """
    PDA 拣货任务接口：
      - GET  /api/outbound/pda/pick-tasks/             列表
      - GET  /api/outbound/pda/pick-tasks/<id>/        任务头
      - GET  /api/outbound/pda/pick-tasks/<id>/lines/  行
      - POST /api/outbound/pda/pick-tasks/<id>/scan/   扫码拣货
      - POST /api/outbound/pda/pick-tasks/<id>/create-review-task/ complete拣货
      - POST /api/outbound/pda/pick-tasks/<id>/post/   完成并过账
    """
    permission_classes = [IsAuthenticated]
    serializer_class = PickTaskSerializer

    def get_queryset(self):
        qs = WmsTask.objects.filter(
            task_type=WmsTask.TaskType.PICK,
        ).order_by("-id")

        action = getattr(self, "action", None)

        # 1) status 过滤
        status_list = self.request.query_params.getlist("status")
        if status_list:
            qs = qs.filter(status__in=status_list)
        else:
            # 只在 list 时应用“默认进行中状态”的过滤
            if action == "list":
                qs = qs.filter(
                    status__in=[
                        WmsTask.Status.RELEASED,
                        WmsTask.Status.IN_PROGRESS,
                        WmsTask.Status.RESERVED,
                    ]
                )

        # 2) review_status 过滤（你列表已经传了 ?review_status=PENDING）
        review_status_list = self.request.query_params.getlist("review_status")
        if review_status_list:
            qs = qs.filter(review_status__in=review_status_list)

        # 3) for_review=1 时，进一步强制“已完成 + 待审核”
        #    （可选，但很直观）
        for_review = self.request.query_params.get("for_review")
        if for_review in ("1", "true", "True"):
            qs = qs.filter(
                status=WmsTask.Status.COMPLETED,
                review_status=WmsTask.ReviewStatus.PENDING,
            ).exclude(picked_by=self.request.user)

        return qs

    @action(methods=["get"], detail=True)
    def lines(self, request, pk=None):
        print("PickTaskViewSet lines pk=", pk)
        lines = (
            WmsTaskLine.objects
            .filter(task_id=pk)
            .select_related("product", "from_location", "to_location")
            .order_by("id")
        )
        data = PickTaskLineSerializer(lines, many=True).data
        print("PickTaskViewSet  lines data=",data)
        return Response(data)

    @action(methods=["post"], detail=True)
    def scan(self, request, pk=None):
        """
        请求体：
          { "barcode": "...", "qty": 1 }

        内部调用统一的 scan_task()，根据任务类型 = PICK 处理。
        """
        payload = request.data or {}
        barcode = (payload.get("barcode") or "").strip()
        qty = payload.get("qty") or 1
        location_id = payload.get("location_id") or None

        if not barcode:
            return Response({"detail": "缺少条码"}, status=400)

        res = task_services.scan_task(
            task_id=int(pk),
            barcode=barcode,
            qty=qty,
            location_id=location_id,
            by_user=request.user,
            client_seq=payload.get("client_seq"),
        )

        # scan_task 返回里至少有 line_id / qty_done
        line_id = res.get("line_id")
        if line_id:
            line = (
                WmsTaskLine.objects
                .filter(id=line_id)
                .only("id", "qty_plan", "qty_done", "status")
                .first()
            )
            if line:
                res["line"] = {
                    "id": line.id,
                    "qty_plan": line.qty_plan,
                    "qty_done": line.qty_done,
                    "status": line.status,
                }

        return Response(res)

# === 新增：拣货完成 → 提交复核 =========================================
    @action(methods=["post"], detail=True, url_path="create-review-task")
    @transaction.atomic
    def create_review_task(self, request, pk=None):
        print("create-review-task 123,PK=",pk)
        """
        拣货完成，不直接过账：
          - 校验所有明细 qty_done >= qty_plan
          - 状态流转：
              status         → COMPLETED
              review_status  → PENDING
              posting_status → NOT_READY
          - 记录 picked_by = 当前用户（拣货人）
        """
        task = (
            WmsTask.objects
            .select_for_update()
            .get(id=pk, task_type=WmsTask.TaskType.PICK)
        )
        print("create-review-task 222,PK=", pk)
        # 1）必须是进行中或已发布等“可完成”的状态
        if task.status not in (
            WmsTask.Status.RELEASED,
            WmsTask.Status.IN_PROGRESS,
            WmsTask.Status.RESERVED,
        ):
            raise ValidationError(f"当前任务状态为 {task.status}，不能提交复核。")
        print("create-review-task 333,PK=", pk)
        # 2）检查是否还有未拣完的明细
        has_undone = WmsTaskLine.objects.filter(
            task_id=task.id,
            qty_done__lt=F("qty_plan"),
        ).exists()
        print("create-review-task aabbcc,PK=", pk)
        if has_undone:
            print("create-review-task has_undone,has_undone=", has_undone)
            raise ValidationError("还有未拣完的明细，不能提交复核。")
        print("create-review-task 444,PK=", pk)
        # 3）流转到“已完成，待审核”
        task.status = WmsTask.Status.COMPLETED
        task.review_status = WmsTask.ReviewStatus.PENDING
        task.updated_at=datetime.now()
        # 过账还没到，不要改 posting_status（保持 NOT_READY）
        # 记录拣货人（如果你加了 picked_by 字段）
        if task.picked_by_id is None:
            task.picked_by = request.user

        task.finished_at = task.finished_at or timezone.now()
        print("create-review-task 555,PK=", pk)
        task.save(update_fields=[
            "status",
            "review_status",
            "finished_at",
            "picked_by",
            "updated_at",
        ])
        print("create-review-task 666,PK=", pk)
        return Response({
            "task_id": task.id,
            "status": task.status,
            "review_status": task.review_status,
            "posting_status": task.posting_status,
            "message": "拣货已完成，已提交复核。",
        })

    # === 修改：复核通过 + 过账 ==============================================
    @action(methods=["post"], detail=True)
    @transaction.atomic
    def post(self, request, pk=None):
        """
        复核通过并过账：
          - 仅允许在：
              status=COMPLETED & review_status=PENDING
          - 当前登录用户 != 拣货人（picked_by）
          - 设置：
              review_status = APPROVED
              approved_by   = 当前用户
              approved_at   = now
              posting_status = PENDING
          - 然后调用 _run_posting_handler 真正过账
        """
        task = (
            WmsTask.objects
            .select_for_update()
            .get(id=pk, task_type=WmsTask.TaskType.PICK)
        )
        print("post pk=",pk)
        # 1）必须是“已完成，待审核”
        if task.status != WmsTask.Status.COMPLETED:
            raise ValidationError("任务未处于已完成状态，不能过账。")
        if task.review_status != WmsTask.ReviewStatus.PENDING:
            raise ValidationError("当前任务不在待审核状态，不能过账。")
        print("禁止拣货人自己复核自己", pk)
        # 2）禁止拣货人自己复核自己
        picker = getattr(task, "picked_by", None)
        if picker and picker.id == request.user.id:
            raise ValidationError("拣货人不能作为本任务的复核人。")
        print("设置审核通过 & 准备过账", pk)
        # 3）设置审核通过 & 准备过账
        task.review_status = WmsTask.ReviewStatus.APPROVED
        task.approved_by = request.user
        task.approved_at = timezone.now()
        task.approval_note = (task.approval_note or "") + " PDA拣货复核通过"

        task.posting_status = WmsTask.PostingStatus.PENDING
        task.save(update_fields=[
            "review_status",
            "approved_by",
            "approved_at",
            "approval_note",
            "posting_status",
        ])

        # 4）调用统一过账逻辑
        result = _run_posting_handler(
            task_id=int(pk),
            by_user=request.user,
            note="PDA拣货复核通过并过账",
        )
        # 这里假设 _run_posting_handler 内部会把 posting_status / posted_by / posted_at 更新好

        return Response({
            "task_id": int(pk),
            "posted": True,
            **(result or {}),
        })

    @action(methods=["post"], detail=True, url_path="adjust-line-qty")
    def adjust_line_qty(self, request, pk=None):
        """
        PDA 手工调整拣货行数量：
          - 请求体：{ line_id, final_qty_done, client_seq? }
          - 调用 tasking.services.adjust_pick_line_qty
        """
        print("889 adjust_line_qty adjust_line_qty adjust_line_qtypk=",pk)
        task_id = int(pk)
        line_id = request.data.get("line_id")
        final_qty_done = request.data.get("final_qty_done")
        client_seq = request.data.get("client_seq")

        if not line_id:
            return Response(
                {"detail": "缺少 line_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if final_qty_done is None:
            return Response(
                {"detail": "缺少 final_qty_done"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            res = adjust_pick_line_qty(
                task_id=task_id,
                line_id=int(line_id),
                final_qty=final_qty_done,
                by_user=request.user,
                client_seq=client_seq,
            )
        except ValidationError as e:
            # 和其它接口一样，抛 Django/DRF 的 ValidationError
            raise e

        return Response(res)
