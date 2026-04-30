
from rest_framework import serializers
from .models import InventorySummary
from rest_framework import serializers
from allapp.core.formatters import format_product_qty
#
# class OwnerInventorySummarySerializer(serializers.ModelSerializer):
#     product_id = serializers.IntegerField(source="product.id", read_only=True)
#     product_code = serializers.CharField(source="product.code", read_only=True)
#     product_name = serializers.CharField(source="product.name", read_only=True)
#     product_spec = serializers.CharField(source="product.spec", read_only=True)
#     product_sku = serializers.CharField(source="product.sku", read_only=True)
#
#     class Meta:
#         model = InventorySummary
#         fields = [
#             "id",
#             "product_id",
#             "product_code",
#             "product_name",
#             "product_spec",
#             "product_sku",
#             "base_unit",
#             "onhand_qty",
#             "available_qty",
#             "allocated_qty",
#             "locked_qty",
#             "damaged_qty",
#         ]

class OwnerInventorySummarySerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_spec = serializers.CharField(source="product.spec", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    onhand_qty_display = serializers.SerializerMethodField()
    available_qty_display = serializers.SerializerMethodField()
    allocated_qty_display = serializers.SerializerMethodField()
    locked_qty_display = serializers.SerializerMethodField()
    damaged_qty_display = serializers.SerializerMethodField()

    class Meta:
        model = InventorySummary
        fields = [
            "id",
            "product_id",
            "product_code",
            "product_name",
            "product_spec",
            "product_sku",
            "base_unit",

            "onhand_qty",
            "available_qty",
            "allocated_qty",
            "locked_qty",
            "damaged_qty",

            "onhand_qty_display",
            "available_qty_display",
            "allocated_qty_display",
            "locked_qty_display",
            "damaged_qty_display",
        ]

    def get_onhand_qty_display(self, obj):
        return format_product_qty(obj.onhand_qty, obj.product)

    def get_available_qty_display(self, obj):
        return format_product_qty(obj.available_qty, obj.product)

    def get_allocated_qty_display(self, obj):
        return format_product_qty(obj.allocated_qty, obj.product)

    def get_locked_qty_display(self, obj):
        return format_product_qty(obj.locked_qty, obj.product)

    def get_damaged_qty_display(self, obj):
        return format_product_qty(obj.damaged_qty, obj.product)


from rest_framework import serializers


class CompanyInventoryWarehouseSummarySerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField()
    warehouse_name = serializers.CharField()
    owner_id = serializers.IntegerField()
    owner_name = serializers.CharField()
    product_id = serializers.IntegerField()
    product_code = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    product_name = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    product_spec = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    product_sku = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    base_unit = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    onhand_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    available_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    allocated_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    locked_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    damaged_qty = serializers.DecimalField(max_digits=18, decimal_places=4)


class CompanyInventoryAllSummarySerializer(serializers.Serializer):
    owner_id = serializers.IntegerField()
    owner_name = serializers.CharField()
    product_id = serializers.IntegerField()
    product_code = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    product_name = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    product_spec = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    product_sku = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    base_unit = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    onhand_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    available_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    allocated_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    locked_qty = serializers.DecimalField(max_digits=18, decimal_places=4)
    damaged_qty = serializers.DecimalField(max_digits=18, decimal_places=4)