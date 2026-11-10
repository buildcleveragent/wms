"""Global Boss check scheduling; page polling never invokes business scans."""

import json
import logging
import time
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from allapp.accounts.audit import record_audit_event
from allapp.core.models import SystemSetting

from .models import BossCheckState

logger = logging.getLogger(__name__)
NAMESPACE = "boss"
KEY = "automation_settings"
RUN_KEY = "BOSS_SCHEDULE_RUN"
WORKER_KEY = "BOSS_SCHEDULE_WORKER"
DEFAULTS = {"enabled": False, "interval_minutes": 5, "enabled_at": None}


def validate(values):
    if type(values.get("enabled")) is not bool:
        raise ValidationError({"enabled": "必须为布尔值。"})
    minutes = values.get("interval_minutes")
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValidationError({"interval_minutes": "请输入 1～1440 的整数分钟。"})
    return values


def configuration():
    value = SystemSetting.get_value(NAMESPACE, KEY, default=None)
    if value is None:
        return dict(DEFAULTS)
    if not isinstance(value, dict):
        raise ValidationError("自动检查配置无效，请由超级用户修正。")
    return validate({**DEFAULTS, **value})


@transaction.atomic
def save_configuration(request):
    supplied = request.data
    if not isinstance(supplied, dict) or set(supplied) - {"enabled", "interval_minutes"}:
        raise ValidationError("仅支持 enabled 和 interval_minutes。")
    row, _ = SystemSetting.objects.select_for_update().get_or_create(
        namespace=NAMESPACE,
        key=KEY,
        defaults={"name": "Boss 后台自动检查", "value_type": "json", "value": json.dumps(DEFAULTS)},
    )
    before = configuration()
    after = validate({**before, **supplied})
    if after["enabled"] and not before["enabled"]:
        after["enabled_at"] = timezone.now().isoformat()
    row.value = json.dumps(after)
    row.value_type = "json"
    row.is_active = True
    row.client_visible = False
    row.save()
    record_audit_event(
        action="boss.automation.configure",
        module="reports",
        request=request,
        obj=row,
        before=before,
        after=after,
    )
    return after


def next_check(config, run):
    if not config["enabled"]:
        return None
    enabled_at = parse_datetime(config.get("enabled_at") or "")
    finished = run.checked_at if run else None
    if not finished or (enabled_at and enabled_at > finished):
        return enabled_at or timezone.now()
    return finished + timedelta(minutes=config["interval_minutes"])


def automation_status():
    config = configuration()
    now = timezone.now()
    states = {s.key: s for s in BossCheckState.objects.filter(key__in=[RUN_KEY, WORKER_KEY])}
    run, worker = states.get(RUN_KEY), states.get(WORKER_KEY)
    due = next_check(config, run)
    running = False
    if run and run.attempted_at and (not run.checked_at or run.attempted_at > run.checked_at):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT IS_USED_LOCK(%s)", ["boss-deadlines:" + connection.settings_dict["NAME"]]
            )
            running = cursor.fetchone()[0] is not None
    heartbeat = worker.checked_at if worker else None
    if not config["enabled"]:
        state = "disabled"
    elif running:
        state = "delayed" if run.attempted_at < now - timedelta(minutes=2) else "running"
    elif not heartbeat or heartbeat < now - timedelta(minutes=2):
        state = "worker_unavailable"
    elif run and (
        run.failed
        or (run.attempted_at and (not run.checked_at or run.attempted_at > run.checked_at))
    ):
        state = "failed"
    elif due and due < now - timedelta(minutes=2):
        state = "delayed"
    elif (
        not run
        or not run.checked_at
        or (config.get("enabled_at") and parse_datetime(config["enabled_at"]) > run.checked_at)
    ):
        state = "waiting"
    else:
        state = "healthy"
    return {
        "enabled": config["enabled"],
        "interval_minutes": config["interval_minutes"],
        "state": state,
        "running": running,
        "next_check_at": None if running else due,
        "last_finished_at": run.checked_at if run else None,
        "worker_seen_at": heartbeat,
    }


def run_scheduled_check(*, force=False):
    """Check eligibility while holding the same lock as legacy/manual check callers."""
    from .boss_attention import check_deadlines

    lock = "boss-deadlines:" + connection.settings_dict["NAME"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, 0)", [lock])
        acquired = cursor.fetchone()[0] == 1
    if not acquired:
        return {"skipped": "busy"}
    started = time.monotonic()
    try:
        config = configuration()
        run = BossCheckState.objects.filter(key=RUN_KEY).first()
        due = next_check(config, run)
        now = timezone.now()
        if not force and not config["enabled"]:
            return {"skipped": "disabled"}
        if not force and due > now:
            return {"skipped": "not_due"}
        BossCheckState.objects.update_or_create(key=RUN_KEY, defaults={"attempted_at": now})
        failed = True
        result = {}
        try:
            result = check_deadlines(lock_held=True)
            failed = False
            return result
        finally:
            # This scheduling row records completion, including failed cycles. Rule rows
            # retain their actual last SUCCESSFUL check and never recover on failure.
            BossCheckState.objects.filter(key=RUN_KEY).update(
                checked_at=timezone.now(), failed=failed
            )
            logger.info(
                "Boss checks finished duration_seconds=%.3f failed=%s counts=%s",
                time.monotonic() - started,
                failed,
                result,
            )
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", [lock])


def heartbeat():
    BossCheckState.objects.update_or_create(
        key=WORKER_KEY, defaults={"checked_at": timezone.now(), "failed": False}
    )
