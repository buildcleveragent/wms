# allapp/inbound/serializers.py
from rest_framework import serializers

class ReceiveWithoutOrderItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)

    def validate_qty(self, v):
        if v <= 0:
            raise serializers.ValidationError("qty 必须 > 0")
        return v

class ReceiveWithoutOrderPayloadSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=True)
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    location_id = serializers.IntegerField(required=False, allow_null=True)
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    items = ReceiveWithoutOrderItemSerializer(many=True, required=True)
