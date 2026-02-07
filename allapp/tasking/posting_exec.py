# allapp/tasking/posting_exec.py
from django.conf import settings
from django.utils.module_loading import import_string
from allapp.tasking.models import WmsTask, TaskScanLog

def execute_posting_handler(task: WmsTask, note: str = "过账") -> int:
    """
    低层执行器：拉 TaskScanLog -> 调 PostingHandler.handle(...) 完成落账
    返回创建的过账条数。此函数不关心权限/审核/PostingJournal/状态写回。
    """
    scans = list(TaskScanLog.objects.filter(task_id=task.id).order_by("id"))
    handler_cfg = getattr(
        settings,
        "TASKING_POSTING_HANDLER",
        "allapp.tasking.plugins.handlers.DefaultPostingHandler",
    )
    handler = import_string(handler_cfg)() if isinstance(handler_cfg, str) else handler_cfg()
    created = handler.handle(task=task, scans=scans, now=None, batch_no=None, note=note or "")
    return created
