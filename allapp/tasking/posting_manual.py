# allapp/tasking/services/manual_post.py
from django.utils import timezone
from allapp.tasking.models import TaskScanLog

def add_manual_scans(task, items, user=None) -> int:
    """
    items: [{product_id, qty_base_delta?, qty_base?, location_id?, task_line_id?,
             lot_no?, mfg_date?, exp_date?, label_key?}, ...]
    """
    logs = []
    now = timezone.now()
    for it in items:
        logs.append(TaskScanLog(
            task_id=task.id,
            task_line_id=it.get("task_line_id"),
            product_id=it["product_id"],
            location_id=it.get("location_id"),
            qty_base_delta=it.get("qty_base_delta"),
            qty_base=it.get("qty_base"),
            lot_no=(it.get("lot_no") or "").strip().upper(),
            mfg_date=it.get("mfg_date"),
            exp_date=it.get("exp_date"),
            label_key=(it.get("label_key") or "").strip().upper(),
            method="MANUAL",
            source="WEB",
            by_user=user,
        ))
    TaskScanLog.objects.bulk_create(logs, ignore_conflicts=False)
    return len(logs)
