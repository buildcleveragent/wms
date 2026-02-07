## 视图 & URL：渲染与导出（支持保存快照）**文件：`allapp/reports/views.py`**
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404

from allapp.tasking.models import WmsTask
from .dispatch_note_builder import build_dispatch_note
from .services import snapshot_dispatch_note
from .models import ReportSnapshot

@staff_member_required
def dispatch_note_html(request, task_id: int):
    task = get_object_or_404(WmsTask, pk=task_id)

    use_snap = request.GET.get("use") == "snap"
    save = request.GET.get("save") == "1"

    if use_snap:
        snap = (ReportSnapshot.objects
                .filter(src_model="WmsTask", src_id=task.id, doc_type="DISPATCH_NOTE")
                .order_by("-id").first())
        if not snap:
            return HttpResponse("No snapshot", status=404)
        html = snap.html or render_to_string("reports/dispatch_note.html", {"note": snap.payload})
        return HttpResponse(html)

    note = build_dispatch_note(task.id)
    html = render_to_string("reports/dispatch_note.html", {"note": note})

    if save:
        snapshot_dispatch_note(task, request.user, save_html=True, finalize=False)
    return HttpResponse(html)

@staff_member_required
def dispatch_note_pdf(request, task_id: int):
    from weasyprint import HTML
    task = get_object_or_404(WmsTask, pk=task_id)
    note = build_dispatch_note(task.id)
    html = render_to_string("reports/dispatch_note.html", {"note": note})
    pdf_bytes = HTML(string=html).write_pdf()
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f"inline; filename=dispatch_{task_id}.pdf"
    return resp