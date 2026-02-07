# api/views.py
from rest_framework import viewsets, permissions, decorators, response, status
from allapp.tasking.models import WmsTask, WmsTaskLine, TaskScanLog
from allapp.tasking import services as task_services
from allapp.inventory import services as inv_services

class TaskViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = WmsTask.objects.all()
    serializer_class = TaskSerializer  # 你已有或补一个

    @decorators.action(methods=["get"], detail=True)
    def lines(self, request, pk=None):
        qs = WmsTaskLine.objects.filter(task_id=pk)
        return response.Response(TaskLineSerializer(qs, many=True).data)

    @decorators.action(methods=["post"], detail=True)
    def scan(self, request, pk=None):
        # 幂等：fp 来自前端（PDA 广播+时间戳+条码）
        payload = request.data
        TaskScanLog.objects.create(
            task_id=pk,
            barcode=payload.get("barcode"),
            qty=payload.get("qty") or 0,
            fp=payload.get("fp"),
            created_by=request.user,
        )
        return response.Response({"ok": True})

    @decorators.action(methods=["post"], detail=True)
    def post(self, request, pk=None):
        task = self.get_object()
        res = task_services.post_task(task_id=task.id, by_user=request.user)  # 你已有
        return response.Response({"posted": True, "journal": res.journal_id})
