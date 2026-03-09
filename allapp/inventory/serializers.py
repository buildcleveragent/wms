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