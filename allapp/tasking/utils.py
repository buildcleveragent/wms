# allapp/tasking/utils.py
# allapp/tasking/utils.py
from typing import Optional, Any
from django.apps import apps

def get_task_status_via_line(line: Any) -> Optional[str]:
    """
    从任意含有 .task / .task_id 的对象上安全取得任务状态。
    优先用已缓存的 line.task.status；否则用 task_id 轻量查询单列。
    """
    if not line:
        return None

    # 已预取到 task 对象
    task = getattr(line, "task", None)
    if task is not None:
        status = getattr(task, "status", None)
        if status is not None:
            return status

    # 退化为按 task_id 取单列
    task_id = getattr(line, "task_id", None)
    if task_id:
        WmsTask = apps.get_model("tasking", "WmsTask")
        return (WmsTask.objects
                .filter(pk=task_id)
                .values_list("status", flat=True)
                .first())
    return None


def bind_triplet_from(obj):
    m = obj._meta
    return {"bound_app": m.app_label, "bound_model": m.model_name, "bound_pk": obj.pk}
