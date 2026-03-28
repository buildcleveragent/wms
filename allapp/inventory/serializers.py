
from rest_framework import serializers
from .models import InventorySummary


class OwnerInventorySummarySerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_spec = serializers.CharField(source="product.spec", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

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
        ]



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