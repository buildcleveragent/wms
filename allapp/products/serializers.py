# apps/products/serializers.py  可直接覆盖版
from rest_framework import serializers
from .models import Product, ProductPackage  # ProductUom 未使用可移除

class ProductPackageBriefSerializer(serializers.ModelSerializer):
    uom_code = serializers.CharField(source="uom.code", read_only=True)
    uom_name = serializers.CharField(source="uom.name", read_only=True)

    class Meta:
        model = ProductPackage
        fields = [
            "id",
            "uom", "uom_code", "uom_name",
            "qty_in_base",
            "barcode",
            "length_cm", "width_cm", "height_cm",
            "gross_weight_kg", "volume_m3", "volume_auto",
            "is_pickable", "is_stock_uom",
            "is_inventory_default", "is_purchase_default", "is_sales_default",
            "sort_order",
        ]
        # 体积(m3)通常由长宽高自动计算，只读即可；审计字段也只读
        read_only_fields = ("id", "volume_m3", "created_at", "updated_at")


class ProductSerializer(serializers.ModelSerializer):
    owner_code = serializers.CharField(source="owner.code", read_only=True)
    base_uom_code = serializers.CharField(source="base_uom.code", read_only=True)
    packages = ProductPackageBriefSerializer(many=True, read_only=True)
    product_image = serializers.SerializerMethodField()

    def get_product_image(self, obj):
        if obj.product_image:
            return obj.product_image.url
        return None

    class Meta:
        model = Product
        fields = [
            "id",
            "owner", "owner_code",
            "code", "sku", "external_code", "name", "spec", "description",
            "category", "brand",
            "gtin", "unit_barcode", "carton_barcode",
            "base_uom", "base_uom_code",
            "pick_policy", "break_box_allowed", "min_pick_multiple",
            "replenish_min", "replenish_uom",
            "volume", "weight",
            "min_stock", "max_stock", "product_image",
            "serial_control", "batch_control", "expiry_control",
            "expiry_basis", "shelf_life_days", "inbound_valid_days", "expiry_warning_days",
            "fefo_required", "mix_lot_allowed", "mix_expiry_allowed",
            "origin_country",
            "is_active",
            "extra",
            "packages", "price", "min_price", "max_discount",
        ]
        read_only_fields = ("id", "created_at", "updated_at")
