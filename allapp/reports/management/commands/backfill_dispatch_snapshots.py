## 10) 管理命令：历史任务批量补快照 **文件：`allapp/reports/management/commands/backfill_dispatch_snapshots.py`**
from django.core.management.base import BaseCommand
from django.db.models import Q
from allapp.tasking.models import WmsTask
from allapp.reports.services import snapshot_dispatch_note

class Command(BaseCommand):
    help = "为历史 DISPATCH 任务生成报表快照"

    def add_arguments(self, parser):
        parser.add_argument("--final", action="store_true", help="保存为定稿")
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **opts):
        qs = (WmsTask.objects
              .filter(Q(task_type=getattr(WmsTask.TaskType, "DISPATCH", "DISPATCH")))
              .order_by("-id")[:opts["limit"]])
        n = 0
        for t in qs:
            snapshot_dispatch_note(t, by_user=t.created_by, save_html=True, finalize=opts["final"])  # by_user 兜底
            n += 1
        self.stdout.write(self.style.SUCCESS(f"snapshots created: {n}"))