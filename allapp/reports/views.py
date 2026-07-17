## 视图 & URL：渲染与导出（支持保存快照）**文件：`allapp/reports/views.py`**
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404

from allapp.accounts.audit import record_audit_event
from allapp.outbound.authz import strict_pick_queryset
from allapp.tasking.models import WmsTask
from .dispatch_note_builder import build_dispatch_note
from .services import snapshot_dispatch_note
from .models import ReportSnapshot

def _dispatch_task_queryset(user):
    can_print = bool(
        user.is_superuser
        or user.has_perm("tasking.view_wmstask")
        or user.has_perm("tasking.claim_task_as_wh_operator")
        or user.has_perm("tasking.taskconfirm_as_wh_manager")
    )
    base = WmsTask.objects.filter(task_type=WmsTask.TaskType.DISPATCH)
    if not can_print:
        return base.none()
    return strict_pick_queryset(base, user)


@login_required
def dispatch_note_html(request, task_id: int):
    task = get_object_or_404(_dispatch_task_queryset(request.user), pk=task_id)
    record_audit_event(
        action="outbound.dispatch.print",
        module="outbound",
        request=request,
        obj=task,
        metadata={"format": "html"},
    )

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
        if not (
            request.user.is_superuser
            or request.user.has_perm("reports.add_reportsnapshot")
            or request.user.has_perm("reports.export_operations")
        ):
            return HttpResponse("Forbidden", status=403)
        snapshot_dispatch_note(task, request.user, save_html=True, finalize=False)
    return HttpResponse(html)


@login_required
def dispatch_note_pdf(request, task_id: int):
    from weasyprint import HTML
    task = get_object_or_404(_dispatch_task_queryset(request.user), pk=task_id)
    record_audit_event(
        action="outbound.dispatch.print",
        module="outbound",
        request=request,
        obj=task,
        metadata={"format": "pdf"},
    )
    note = build_dispatch_note(task.id)
    html = render_to_string("reports/dispatch_note.html", {"note": note})
    pdf_bytes = HTML(string=html).write_pdf()
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f"inline; filename=dispatch_{task_id}.pdf"
    return resp
