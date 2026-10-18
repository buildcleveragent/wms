"""HTML and PDF endpoints for warehouse reports."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string

from allapp.accounts.audit import record_audit_event
from allapp.outbound.authz import strict_pick_queryset
from allapp.tasking.models import WmsTask

from .dispatch_note_builder import (
    DispatchNoteDataError,
    DispatchNoteLimitError,
    build_dispatch_note,
)
from .models import ReportSnapshot
from .services import snapshot_dispatch_note


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


def _safe_dispatch_note(task, *, max_lines=None):
    try:
        return build_dispatch_note(task.id, max_lines=max_lines), None
    except DispatchNoteLimitError as exc:
        return None, HttpResponse(str(exc), status=422)
    except DispatchNoteDataError as exc:
        return None, HttpResponse(str(exc), status=409)


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

    use_snapshot = request.GET.get("use") == "snap"
    save = request.GET.get("save") == "1"

    if use_snapshot:
        snapshot = (
            ReportSnapshot.objects.filter(
                src_model="WmsTask",
                src_id=task.id,
                doc_type="DISPATCH_NOTE",
            )
            .order_by("-id")
            .first()
        )
        if not snapshot:
            return HttpResponse("No snapshot", status=404)
        html = snapshot.html or render_to_string(
            "reports/dispatch_note.html", {"note": snapshot.payload}
        )
        return HttpResponse(html)

    note, error_response = _safe_dispatch_note(task)
    if error_response:
        return error_response
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
    from .pdf_rendering import PdfUnavailable, render_pdf, rendering_slot

    task = get_object_or_404(_dispatch_task_queryset(request.user), pk=task_id)
    record_audit_event(
        action="outbound.dispatch.print",
        module="outbound",
        request=request,
        obj=task,
        metadata={"format": "pdf"},
    )
    try:
        with rendering_slot() as slot:
            note, error_response = _safe_dispatch_note(task, max_lines=settings.PDF_MAX_LINES)
            if error_response:
                return error_response
            html = render_to_string("reports/dispatch_note.html", {"note": note})
            pdf_bytes = render_pdf(html, slot=slot)
    except PdfUnavailable:
        response = HttpResponse("PDF 暂时无法生成，请稍后重试。", status=503)
        response["Retry-After"] = "5"
        return response
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f"inline; filename=dispatch_{task_id}.pdf"
    return response
