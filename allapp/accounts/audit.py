from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.forms.models import model_to_dict

from .client_ip import get_client_ip
from .models import AuditEvent


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def object_snapshot(obj) -> dict:
    if obj is None:
        return {}
    return model_to_dict(obj)


def _client_ip(request):
    if request is None:
        return None
    return get_client_ip(request)


def record_audit_event(
    *,
    action: str,
    module: str,
    request=None,
    user=None,
    obj=None,
    owner_id=None,
    warehouse_id=None,
    succeeded: bool = True,
    before=None,
    after=None,
    metadata=None,
    using=None,
) -> AuditEvent:
    actor = user or getattr(request, "user", None)
    if not getattr(actor, "is_authenticated", False):
        actor = None
    request_id = (
        getattr(request, "request_id", "")
        or (request.META.get("HTTP_X_REQUEST_ID", "") if request else "")
        or uuid.uuid4().hex
    )
    object_type = obj._meta.label_lower if obj is not None else ""
    object_id = str(getattr(obj, "pk", "") or "")
    owner_id = owner_id or getattr(obj, "owner_id", None)
    warehouse_id = warehouse_id or getattr(obj, "warehouse_id", None)
    event_metadata = dict(metadata or {})
    event_metadata.setdefault("_event_id", uuid.uuid4().hex)
    payload = {
        "username": getattr(actor, "username", "") if actor else "",
        "action": action,
        "module": module,
        "object_type": object_type,
        "object_id": object_id,
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "request_id": request_id,
        "method": (getattr(request, "method", "") or "") if request else "",
        "path": (getattr(request, "path", "") or "") if request else "",
        "succeeded": bool(succeeded),
        "before": before or {},
        "after": after or {},
        "metadata": event_metadata,
    }
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode()
    key = settings.SECRET_KEY.encode()
    event_hash = hmac.new(key, raw, hashlib.sha256).hexdigest()
    manager = AuditEvent.objects.using(using) if using else AuditEvent.objects
    return manager.create(
        actor=actor,
        username=payload["username"],
        action=action,
        module=module,
        object_type=object_type,
        object_id=object_id,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        request_id=request_id,
        ip_address=_client_ip(request),
        method=payload["method"],
        path=payload["path"],
        succeeded=payload["succeeded"],
        before=payload["before"],
        after=payload["after"],
        metadata=payload["metadata"],
        event_hash=event_hash,
    )
