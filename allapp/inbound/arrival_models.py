"""Append-only evidence and command identity for the existing inbound documents."""

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage
from django.db import models


def private_arrival_storage():
    location = Path(
        getattr(settings, "PRIVATE_ARRIVAL_ROOT", settings.BASE_DIR / "private_arrivals")
    ).resolve()
    for name in ("MEDIA_ROOT", "STATIC_ROOT"):
        public = getattr(settings, name, None)
        if public and (
            location == Path(public).resolve() or Path(public).resolve() in location.parents
        ):
            raise ImproperlyConfigured("入库照片必须位于公开媒体和静态目录之外。")
    return FileSystemStorage(
        location=location, file_permissions_mode=0o600, directory_permissions_mode=0o700
    )


def arrival_photo_path(instance, filename):
    return f"{instance.line.receipt.order_id}/{uuid4().hex}.jpg"


class ArrivalCommand(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_id = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "request_id"], name="arrival_actor_request")
        ]


class ArrivalEvent(models.Model):
    order = models.ForeignKey(
        "inbound.InboundOrder", on_delete=models.PROTECT, related_name="arrival_events"
    )
    receipt = models.ForeignKey("inbound.InboundReceipt", on_delete=models.PROTECT, null=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    reason = models.CharField(max_length=500, blank=True)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class ArrivalPhoto(models.Model):
    line = models.ForeignKey(
        "inbound.InboundReceiptLine", on_delete=models.PROTECT, related_name="arrival_photos"
    )
    file = models.FileField(storage=private_arrival_storage, upload_to=arrival_photo_path)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    sealed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
