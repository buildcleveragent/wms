"""Confirmed, signed per-user digest windows; polling never acknowledges reading."""

import hashlib
import json
from datetime import datetime

from django.core import signing
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .boss_activity import activity_page, date_bounds, local_today, sources
from .models import BossReadProgress

SALT = "boss.digest.v1"


def binding(scope):
    return {
        "user": scope.user.pk,
        "owner": scope.owner_id,
        "warehouse": scope.warehouse_id,
        "access": scope.access.as_dict(),
    }


def scope_key(scope):
    return hashlib.sha256(
        json.dumps(binding(scope), sort_keys=True, default=str).encode()
    ).hexdigest()


def decode_window(scope, token):
    try:
        data = signing.loads(token, salt=SALT, max_age=86400)
        if data["binding"] != binding(scope):
            raise ValueError()
        start, end = datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
        if start > end or end > timezone.now():
            raise ValueError()
        return start, end
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError({"token": "摘要已失效，请刷新后重试。"}) from None


def digest(scope):
    progress = BossReadProgress.objects.filter(user=scope.user, scope_key=scope_key(scope)).first()
    start = progress.confirmed_at if progress else date_bounds(local_today(), local_today())[0]
    end = timezone.now()
    rows = []
    for source in sources(scope):
        if source.key not in {"order", "pos", "inventory", "receipt", "alert"}:
            continue
        qs = source.qs.filter(**{source.timestamp + "__gte": start, source.timestamp + "__lt": end})
        if source.key == "pos":
            for action, label in [
                ("CHECKOUT", "POS销售完成"),
                ("RETURN", "POS退货"),
                ("VOID", "POS作废"),
            ]:
                rows.append(
                    {
                        "key": action,
                        "category": "pos",
                        "action": action,
                        "label": label,
                        "count": qs.filter(action=action).count(),
                    }
                )
        else:
            rows.append(
                {
                    "key": source.key,
                    "category": source.category,
                    "label": {
                        "order": "出库正式提交",
                        "inventory": "非POS库存流水",
                        "receipt": "回款及冲销",
                        "alert": "异常变化",
                    }[source.key],
                    "count": qs.count(),
                }
            )
    from .boss_periods import metric_sources

    by_key = {row["key"]: row for row in rows}
    keys = {
        "pos_sales": "CHECKOUT",
        "pos_returns": "RETURN",
        "pos_voids": "VOID",
        "receipts": "receipt",
    }
    for metric in metric_sources(scope, start.date(), end.date()):
        if metric.key not in keys:
            continue
        qs = metric.qs.filter(
            **{metric.timestamp + "__gte": start, metric.timestamp + "__lt": end}
        ).order_by()
        by_key[keys[metric.key]]["money"] = [
            {"currency": item[metric.currency], "amount": item["amount"]}
            for item in qs.values(metric.currency)
            .annotate(amount=Sum(metric.money))
            .order_by(metric.currency)
        ]
    token = signing.dumps(
        {"binding": binding(scope), "start": start.isoformat(), "end": end.isoformat()},
        salt=SALT,
        compress=True,
    )
    return {
        "from": start,
        "to": end,
        "first_visit": progress is None,
        "token": token,
        "results": rows,
        "notice": "按业务记录统计。POS的关联库存不重复计入业务笔数，实际数量及金额见对应明细。",
    }


def confirm(scope, token):
    _, end = decode_window(scope, token)
    with transaction.atomic():
        row, _ = BossReadProgress.objects.get_or_create(
            user=scope.user, scope_key=scope_key(scope), defaults={"confirmed_at": end}
        )
        row = BossReadProgress.objects.select_for_update().get(pk=row.pk)
        if end > row.confirmed_at:
            row.confirmed_at = end
            row.save(update_fields=["confirmed_at", "updated_at"])
    return {"confirmed_at": row.confirmed_at}


def digest_details(scope, token, category="", action="", cursor="", limit=20):
    start, end = decode_window(scope, token)
    return activity_page(
        scope,
        date_from=start.date(),
        date_to=end.date(),
        category=category,
        occurred_from=start,
        occurred_until=end,
        action=action,
        cursor=cursor,
        limit=limit,
    )
