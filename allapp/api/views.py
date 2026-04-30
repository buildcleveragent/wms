# api/views.py
from rest_framework import viewsets, permissions, decorators, response, status
from allapp.tasking.models import WmsTask, WmsTaskLine, TaskScanLog
from allapp.tasking import services as task_services


from pathlib import Path
from django.conf import settings
from django.http import FileResponse, Http404
from datetime import datetime

from django.shortcuts import render
from datetime import datetime

def download_page(request):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    # 给 APK 一个带时间戳的下载地址（避免同名不弹提示）
    apk_url = f"/api/v1/bv2/bv2_{ts}.apk"
    return render(request, "download.html", {"apk_url": apk_url})


def download_bv2(request, filename):
    if not filename.endswith(".apk"):
        filename = f"{filename}.apk"

    file_path = Path(settings.BASE_DIR) / "media" / "bv2.apk"

    if not file_path.exists():
        raise Http404("文件不存在")

    response = FileResponse(
        open(file_path, "rb"),
        as_attachment=True,
        filename=filename,
        content_type="application/vnd.android.package-archive",
    )
    response["Content-Length"] = str(file_path.stat().st_size)
    response["Accept-Ranges"] = "bytes"
    return response

# def download_bv2(request):
#     file_path = Path(settings.BASE_DIR) / "media" / "bv2.apk"
#
#     if not file_path.exists():
#         raise Http404("文件不存在")
#
#     file_size = file_path.stat().st_size
#     f = open(file_path, "rb")
#     timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
#
#     response = FileResponse(
#         f,
#         as_attachment=True,
#         filename = f"bv2_{timestamp}.apk",
#         content_type="application/vnd.android.package-archive",
#     )
#
#     response["Content-Length"] = str(file_size)
#     response["Content-Disposition"] = 'attachment; filename="bv2.apk"'
#     response["Accept-Ranges"] = "bytes"
#
#     return response


# def download_bv2(request):
#
#     file_path = Path(settings.BASE_DIR) / "media" / "bv2.apk"
#
#     if not file_path.exists():
#         raise Http404("文件不存在")
#
#     # return FileResponse(
#     #     open(file_path, "rb"),
#     #     as_attachment=True,
#     #     filename="bv2.apk",
#
#     response = FileResponse(open(file_path, "rb"), content_type="application/vnd.android.package-archive")
#     response["Content-Disposition"] = 'inline; filename="bv2.apk"'
#
#     return response
#

# class TaskViewSet(viewsets.ReadOnlyModelViewSet):
#     permission_classes = [permissions.IsAuthenticated]
#     queryset = WmsTask.objects.all()
#     serializer_class = TaskSerializer  # 你已有或补一个
#
#     @decorators.action(methods=["get"], detail=True)
#     def lines(self, request, pk=None):
#         qs = WmsTaskLine.objects.filter(task_id=pk)
#         return response.Response(TaskLineSerializer(qs, many=True).data)
#
#     @decorators.action(methods=["post"], detail=True)
#     def scan(self, request, pk=None):
#         # 幂等：fp 来自前端（PDA 广播+时间戳+条码）
#         payload = request.data
#         TaskScanLog.objects.create(
#             task_id=pk,
#             barcode=payload.get("barcode"),
#             qty=payload.get("qty") or 0,
#             fp=payload.get("fp"),
#             created_by=request.user,
#         )
#         return response.Response({"ok": True})
#
#     @decorators.action(methods=["post"], detail=True)
#     def post(self, request, pk=None):
#         task = self.get_object()
#         res = task_services.post_task(task_id=task.id, by_user=request.user)  # 你已有
#         return response.Response({"posted": True, "journal": res.journal_id})
