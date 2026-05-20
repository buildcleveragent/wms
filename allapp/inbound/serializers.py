# allapp/inbound/serializers.py
from rest_framework import serializers

# class ReceiveWithoutOrderItemSerializer(serializers.Serializer):
#     product_id = serializers.IntegerField(required=True)
#     qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)
#
#
#     def validate_qty(self, v):
#         if v <= 0:
#             raise serializers.ValidationError("qty 必须 > 0")
#         return v


# class ReceiveWithoutOrderItemSerializer(serializers.Serializer):
#     product_id = serializers.IntegerField(required=True)
#     qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)
#     lot_no = serializers.CharField(required=False, allow_blank=True, default="")
#     mfg_date = serializers.DateField(required=False, allow_null=True)
#     exp_date = serializers.DateField(required=False, allow_null=True)
#     batch_no = serializers.CharField(required=False, allow_blank=True, default="")
#     batch_number = serializers.CharField(required=False, allow_blank=True, default="")
#     production_date = serializers.DateField(required=False, allow_null=True)
#     expiry_date = serializers.DateField(required=False, allow_null=True)
#
#     def validate_qty(self, v):
#         if v <= 0:
#             raise serializers.ValidationError("qty 必须 > 0")
#         return v
#
#     def validate(self, attrs):
#         attrs["lot_no"] = attrs.get("lot_no") or attrs.get("batch_no") or attrs.get("batch_number") or ""
#         attrs["mfg_date"] = attrs.get("mfg_date") or attrs.get("production_date")
#         attrs["exp_date"] = attrs.get("exp_date") or attrs.get("expiry_date")
#         attrs.pop("batch_no", None)
#         attrs.pop("batch_number", None)
#         attrs.pop("production_date", None)
#         attrs.pop("expiry_date", None)
#
#         mfg_date = attrs.get("mfg_date")
#         exp_date = attrs.get("exp_date")
#         if mfg_date and exp_date and exp_date < mfg_date:
#             raise serializers.ValidationError({"exp_date": "有效期不得早于生产日期"})
#         return attrs


class ReceiveWithoutOrderItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)
    lot_no = serializers.CharField(required=False, allow_blank=True, default="")
    mfg_date = serializers.DateField(required=False, allow_null=True)
    exp_date = serializers.DateField(required=False, allow_null=True)
    batch_no = serializers.CharField(required=False, allow_blank=True, default="")
    batch_number = serializers.CharField(required=False, allow_blank=True, default="")
    production_date = serializers.DateField(required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)
    lotNo = serializers.CharField(required=False, allow_blank=True, default="")
    batchNumber = serializers.CharField(required=False, allow_blank=True, default="")
    mfgDate = serializers.DateField(required=False, allow_null=True)
    expDate = serializers.DateField(required=False, allow_null=True)
    productionDate = serializers.DateField(required=False, allow_null=True)
    expiryDate = serializers.DateField(required=False, allow_null=True)

    def validate_qty(self, v):
        if v <= 0:
            raise serializers.ValidationError("qty 必须 > 0")
        return v

    def validate(self, attrs):
        attrs["lot_no"] = (
            attrs.get("lot_no")
            or attrs.get("lotNo")
            or attrs.get("batch_no")
            or attrs.get("batch_number")
            or attrs.get("batchNumber")
            or ""
        )
        attrs["mfg_date"] = (
            attrs.get("mfg_date")
            or attrs.get("mfgDate")
            or attrs.get("production_date")
            or attrs.get("productionDate")
        )
        attrs["exp_date"] = (
            attrs.get("exp_date")
            or attrs.get("expDate")
            or attrs.get("expiry_date")
            or attrs.get("expiryDate")
        )
        for alias in (
            "batch_no",
            "batch_number",
            "production_date",
            "expiry_date",
            "lotNo",
            "batchNumber",
            "mfgDate",
            "expDate",
            "productionDate",
            "expiryDate",
        ):
            attrs.pop(alias, None)

        mfg_date = attrs.get("mfg_date")
        exp_date = attrs.get("exp_date")
        if mfg_date and exp_date and exp_date < mfg_date:
            raise serializers.ValidationError({"exp_date": "有效期不得早于生产日期"})
        return attrs


class ReceiveWithoutOrderPayloadSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=True)
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    location_id = serializers.IntegerField(required=False, allow_null=True)
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    items = ReceiveWithoutOrderItemSerializer(many=True, required=True)
