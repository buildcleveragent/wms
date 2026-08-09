# apps/products/serializers.py  可直接覆盖版
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from allapp.accounts.access import AccessScope
from .models import (
    PRODUCT_IDENTIFIER_FIELDS,
    Product,
    ProductBarcode,
    ProductExternalIdentifier,
    ProductIdentifierRegistry,
    ProductPackage,
    normalize_product_identifier,
)
from .identifier_services import (
    IdentifierConcurrencyError,
    add_external_identifier,
    add_product_barcode,
    set_barcode_primary,
    set_external_primary,
)
from .permissions import can_manage_all_owner_products

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
            "is_active",
            "sort_order",
        ]
        # 体积(m3)通常由长宽高自动计算，只读即可；审计字段也只读
        read_only_fields = ("id", "volume_m3", "created_at", "updated_at")


class ProductIdentifierConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "商品标识发生并发冲突，请刷新后重试。"
    default_code = "product_identifier_conflict"


class ProductSerializer(serializers.ModelSerializer):
    owner_code = serializers.CharField(source="owner.code", read_only=True)
    base_uom_code = serializers.CharField(source="base_uom.code", read_only=True)
    packages = ProductPackageBriefSerializer(many=True, read_only=True)
    carton_package_detail = ProductPackageBriefSerializer(
        source="carton_package", read_only=True
    )
    product_image = serializers.SerializerMethodField()

    def get_product_image(self, obj):
        if obj.product_image:
            return obj.product_image.url
        return None

    def to_internal_value(self, data):
        if hasattr(data, "copy"):
            data = data.copy()
        request = self.context.get("request")
        user = getattr(request, "user", None)
        authed_non_superuser = (
            user
            and getattr(user, "is_authenticated", False)
            and not getattr(user, "is_superuser", False)
        )
        if authed_non_superuser:
            can_manage_all = can_manage_all_owner_products(user)
            owner_id = AccessScope.for_user(user).single_owner_id
            if self.instance is None:
                if owner_id and (not can_manage_all or "owner" not in data):
                    data["owner"] = owner_id
            elif not can_manage_all:
                data["owner"] = self.instance.owner_id
        elif self.instance is None and "owner" not in data:
            owner_id = AccessScope.for_user(user).single_owner_id if user else None
            if owner_id:
                data["owner"] = owner_id
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        category = attrs.get(
            "category",
            self.instance.category if self.instance is not None else None,
        )
        if self.instance is None and category is None:
            raise serializers.ValidationError(
                {"category": "新建商品时至少需要选择一个大类。"}
            )
        if (
            self.instance is not None
            and self.instance.category_id
            and "category" in attrs
            and category is None
        ):
            raise serializers.ValidationError(
                {"category": "已分类商品不能清空分类。"}
            )
        if category is not None and not category.has_active_path():
            raise serializers.ValidationError(
                {"category": "商品只能选择分类链全部启用的分类。"}
            )

        carton_barcode = attrs.get(
            "carton_barcode",
            self.instance.carton_barcode if self.instance is not None else None,
        )
        carton_package = attrs.get(
            "carton_package",
            self.instance.carton_package if self.instance is not None else None,
        )
        binding_changed = self.instance is None or (
            normalize_product_identifier(carton_barcode)
            != normalize_product_identifier(self.instance.carton_barcode)
            or getattr(carton_package, "pk", None) != self.instance.carton_package_id
        )
        if binding_changed and bool(carton_barcode) != bool(carton_package):
            raise serializers.ValidationError(
                {"carton_package": "箱码和箱码对应包装层级必须同时设置。"}
            )
        if self.instance is None and carton_barcode:
            raise serializers.ValidationError(
                {"carton_package": "请先创建商品和包装层级，再通过更新接口绑定箱码。"}
            )
        if binding_changed and carton_package is not None:
            if carton_package.product_id != self.instance.pk:
                raise serializers.ValidationError(
                    {"carton_package": "箱码对应包装层级必须属于当前商品。"}
                )
            if carton_package.is_deleted or not carton_package.is_active:
                raise serializers.ValidationError(
                    {"carton_package": "箱码对应包装层级必须启用且未删除。"}
                )
        if self.instance is not None:
            for field in ("gtin", "unit_barcode", "carton_barcode", "external_code"):
                if field in attrs and not normalize_product_identifier(attrs[field]):
                    raise serializers.ValidationError(
                        {field: "不能通过清空主值退役标识；请使用条码或外部标识维护接口执行退役。"}
                    )
            if "owner" in attrs and attrs["owner"].pk != self.instance.owner_id:
                raise serializers.ValidationError({"owner": "商品货主创建后不可修改。"})
            if "code" in attrs and normalize_product_identifier(attrs["code"]) != normalize_product_identifier(self.instance.code):
                raise serializers.ValidationError({"code": "货主商品编码创建后不可修改。"})

        owner = attrs.get(
            "owner", self.instance.owner if self.instance is not None else None
        )
        if owner is not None:
            errors = {}
            for field in PRODUCT_IDENTIFIER_FIELDS:
                value = attrs.get(
                    field,
                    getattr(self.instance, field, None)
                    if self.instance is not None
                    else None,
                )
                normalized = normalize_product_identifier(value)
                if not normalized:
                    continue
                conflicts = ProductIdentifierRegistry.objects.filter(
                    owner_id=owner.pk,
                    normalized_value=normalized,
                )
                if self.instance is not None:
                    conflicts = conflicts.exclude(product_id=self.instance.pk)
                conflict = conflicts.select_related("product").first()
                if conflict:
                    product = conflict.product
                    deleted = "（已软删除）" if product.is_deleted else ""
                    source = f"商品 {product.code}-{product.name}{deleted}"
                    errors[field] = (
                        f"该货主下标识“{normalized}”已被{source}占用。"
                    )
            if errors:
                raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", None) or exc.messages
            ) from exc
        except IdentifierConcurrencyError as exc:
            raise ProductIdentifierConflict(str(exc)) from exc
        except IntegrityError as exc:
            raise ProductIdentifierConflict() from exc

    @transaction.atomic
    def update(self, instance, validated_data):
        legacy = {
            field: validated_data.pop(field)
            for field in ("gtin", "unit_barcode", "carton_barcode", "external_code")
            if field in validated_data
        }
        carton_package = validated_data.pop("carton_package", instance.carton_package)
        try:
            instance = super().update(instance, validated_data)
            for field, value in legacy.items():
                if normalize_product_identifier(value) == normalize_product_identifier(getattr(instance, field)) and not (field == "carton_barcode" and carton_package.pk != instance.carton_package_id):
                    continue
                if field == "external_code":
                    existing = ProductExternalIdentifier.all_objects.filter(
                        product=instance, source_system="LEGACY",
                        normalized_value=normalize_product_identifier(value),
                    ).first()
                    if existing:
                        set_external_primary(existing)
                    else:
                        add_external_identifier(
                            product=instance, source_system="LEGACY",
                            external_code=value, is_primary=True,
                        )
                    continue
                barcode_type = {
                    "gtin": ProductBarcode.BarcodeType.GTIN,
                    "unit_barcode": ProductBarcode.BarcodeType.UNIT,
                    "carton_barcode": ProductBarcode.BarcodeType.CARTON,
                }[field]
                package = carton_package if field == "carton_barcode" else None
                existing = ProductBarcode.all_objects.filter(
                    product=instance, barcode_type=barcode_type, package=package,
                    normalized_value=normalize_product_identifier(value),
                ).first()
                if existing:
                    set_barcode_primary(existing)
                else:
                    add_product_barcode(
                        product=instance, barcode=value, barcode_type=barcode_type,
                        package=package, is_primary=True,
                    )
            return Product.objects.get(pk=instance.pk)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", None) or exc.messages
            ) from exc
        except IdentifierConcurrencyError as exc:
            raise ProductIdentifierConflict(str(exc)) from exc
        except IntegrityError as exc:
            raise ProductIdentifierConflict() from exc

    class Meta:
        model = Product
        fields = [
            "id",
            "owner", "owner_code",
            "code", "sku", "external_code", "name", "spec", "description",
            "category", "brand",
            "gtin", "unit_barcode", "carton_barcode",
            "carton_package", "carton_package_detail",
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
        read_only_fields = ("id", "sku", "created_at", "updated_at")
        extra_kwargs = {
            "owner": {"required": False},
        }


class ProductBarcodeSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    package_uom_code = serializers.CharField(source="package.uom.code", read_only=True)

    class Meta:
        model = ProductBarcode
        fields = (
            "id", "owner", "product", "product_code", "barcode",
            "normalized_value", "barcode_type", "package", "package_uom_code",
            "qty_in_base", "is_primary", "valid_from", "valid_to", "is_active",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "owner", "normalized_value", "qty_in_base", "created_at", "updated_at")

    def create(self, validated_data):
        try:
            return add_product_barcode(**validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", None) or exc.messages) from exc
        except IdentifierConcurrencyError as exc:
            raise ProductIdentifierConflict(str(exc)) from exc

    def update(self, instance, validated_data):
        allowed = {"valid_from", "valid_to", "is_active"}
        if set(validated_data) - allowed:
            raise serializers.ValidationError("条码身份字段创建后不可修改。")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.full_clean()
        instance._identifier_service_write = True
        try:
            instance.save(update_fields=[*validated_data, "updated_at"])
        finally:
            delattr(instance, "_identifier_service_write")
        return instance


class ProductExternalIdentifierSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)

    class Meta:
        model = ProductExternalIdentifier
        fields = (
            "id", "owner", "product", "product_code", "source_system",
            "external_code", "normalized_value", "is_primary", "valid_from",
            "valid_to", "is_active", "created_at", "updated_at",
        )
        read_only_fields = ("id", "owner", "normalized_value", "created_at", "updated_at")

    def create(self, validated_data):
        try:
            return add_external_identifier(**validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", None) or exc.messages) from exc
        except IdentifierConcurrencyError as exc:
            raise ProductIdentifierConflict(str(exc)) from exc

    def update(self, instance, validated_data):
        allowed = {"valid_from", "valid_to", "is_active"}
        if set(validated_data) - allowed:
            raise serializers.ValidationError("外部标识身份字段创建后不可修改。")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.full_clean()
        instance._identifier_service_write = True
        try:
            instance.save(update_fields=[*validated_data, "updated_at"])
        finally:
            delattr(instance, "_identifier_service_write")
        return instance
