"""Product descriptions for current catalogues and immutable document snapshots."""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction

SNAPSHOT_MODELS = (
    ("outbound", "OutboundOrderLine"),
    ("salesapp", "SalesOrderLine"),
    ("pos", "PosSaleLine"),
    ("pos", "PosReturnLine"),
    ("inbound", "InboundOrderLine"),
    ("inbound", "InboundReceiptLine"),
    ("inbound", "CustomerReturnLine"),
    ("outbound", "ReturnLine"),
)


def combine_brand_model(brand, model):
    return "·".join(str(value).strip() for value in (brand, model) if str(value or "").strip())


def product_identity(product):
    """Return plain text suitable for both API output and document storage."""
    if product is None:
        return {"name": "", "spec": "", "brand_model": ""}
    return {
        "name": product.name or "",
        "spec": product.spec or "",
        "brand_model": product.brand_model,
    }


def identity_display_name(identity):
    return " · ".join(identity.get(key, "") for key in ("name", "brand_model") if identity.get(key))


def require_product_snapshots(product_ids):
    """Reject renames until every related legacy document has been captured."""
    for app_label, model_name in SNAPSHOT_MODELS:
        model = apps.get_model(app_label, model_name)
        if model._base_manager.filter(product_id__in=product_ids, product_snapshot={}).exists():
            raise ValidationError(
                "商品关联的历史单据尚未补齐名称快照；请先运行 backfill_product_snapshots --apply。"
            )


class ProductIdentitySnapshotMixin(models.Model):
    product_snapshot = models.JSONField("商品资料快照", default=dict, blank=True, editable=False)

    class Meta:
        abstract = True

    def capture_product_snapshot(self, *, preserve_legacy=True):
        """Capture once; source documents take precedence over current master data."""
        if self.product_snapshot:
            return
        source = None
        for field in ("original_line", "outbound_order_line", "sale_line", "order_line"):
            if getattr(self, f"{field}_id", None):
                source = getattr(self, field)
                break
        if source and source.product_id != self.product_id:
            source = None
        identity = (
            dict(source.product_snapshot)
            if source and source.product_snapshot
            else product_identity(self.product if self.product_id else None)
        )
        # Existing receipt/physical return text is itself historical evidence.
        if preserve_legacy and (not self._state.adding or source is None):
            if getattr(self, "product_name", ""):
                identity["name"] = self.product_name
            if "spec" in {f.name for f in self._meta.concrete_fields}:
                identity["spec"] = self.spec or ""
        self.product_snapshot = identity

    @property
    def frozen_product_name(self):
        if self.product_snapshot:
            return self.product_snapshot.get("name", "")
        return getattr(self, "product_name", "") or (self.product.name if self.product_id else "")

    @property
    def frozen_product_spec(self):
        if self.product_snapshot:
            return self.product_snapshot.get("spec", "")
        if "spec" in {field.name for field in self._meta.concrete_fields}:
            return self.spec or ""
        return self.product.spec or "" if self.product_id else ""

    @property
    def frozen_brand_model(self):
        if self.product_snapshot:
            return self.product_snapshot.get("brand_model", "")
        return self.product.brand_model if self.product_id else ""

    @property
    def product_display_name(self):
        return identity_display_name(
            {"name": self.frozen_product_name, "brand_model": self.frozen_brand_model}
        )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.product_id:
                product_model = apps.get_model("products", "Product")
                self.product = (
                    product_model.all_objects.select_for_update()
                    .select_related("brand")
                    .get(pk=self.product_id)
                )
            original = None
            if self.pk and not self._state.adding:
                original = type(self)._base_manager.select_for_update().filter(pk=self.pk).first()
            if (
                original
                and kwargs.get("update_fields") is not None
                and not {"product", "product_id"} & set(kwargs["update_fields"])
            ):
                self.product = original.product
            replaced = original is not None and original.product_id != self.product_id
            if original and not replaced:
                self.product_snapshot = original.product_snapshot
            elif replaced:
                self.product_snapshot = {}
            self.capture_product_snapshot(preserve_legacy=not replaced)
            # Keep the existing receipt/return columns aligned with their snapshot.
            extra_fields = {"product_snapshot"}
            names = {f.name for f in self._meta.concrete_fields}
            for field, key in (("product_name", "name"), ("spec", "spec")):
                if field in names:
                    setattr(self, field, self.product_snapshot[key])
                    extra_fields.add(field)
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | extra_fields
            return super().save(*args, **kwargs)
