"""Scoped operator pages for inventory close, evidence review and sign-off."""

import hashlib
import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from allapp.accounts.access import AccessScope
from allapp.inventory import daily_close
from allapp.inventory import daily_close_review as reviews
from allapp.inventory.models import (
    InventoryClosePilot,
    InventoryCloseScope,
    InventoryDailyClose,
    InventorySnapshotDaily,
)
from allapp.locations.models import Location, Warehouse
from allapp.products.models import Product

from .forms_daily_close import (
    ACTION_LABELS,
    REFERENCE_LABELS,
    CloseFilters,
    EvidenceForm,
    FinanceApprovalForm,
    FinanceReviewApprovalForm,
    ManualRunForm,
    NoteForm,
    OpeningForm,
    PilotForm,
)

ACTIONS = {
    "submit": "仓管补证",
    "approve": "财务签核",
    "return": "退回仓管",
    "opening": "提交可信期初",
    "approve-opening": "签核期初",
    "return-opening": "退回期初",
}


def _scope(request):
    scope = AccessScope.for_request(request)
    if not reviews.can_view(request.user, scope=scope):
        raise PermissionDenied
    return scope


def _configurable(scope, user):
    return scope.is_global or (
        bool(scope.warehouse_ids) and user.has_perm("inventory.change_inventoryclosepilot")
    )


def _can_run(scope, user):
    return scope.is_global or (
        scope.single_warehouse_id is not None
        and reviews.can_review(user, scope.single_warehouse_id)
    )


def _run_form(scope, data=None):
    form = ManualRunForm(data, prefix="run")
    bindings = daily_close.effective_bindings().select_related("owner", "warehouse")
    if not scope.is_global:
        bindings = bindings.filter(warehouse_id=scope.single_warehouse_id)
    form.fields["binding"].queryset = bindings.order_by("warehouse__code", "owner__name")
    return form


def _pilot_form(scope, data=None):
    form = PilotForm(data, prefix="pilot")
    form.fields["warehouse"].queryset = scope.filter_queryset(
        Warehouse.objects.order_by("code"), None, "pk"
    )
    return form


def _scope_close(user, pk):
    return get_object_or_404(
        reviews.visible_closes(user).select_related("owner", "warehouse"), pk=pk
    )


def _choices(queryset, key, label):
    pairs = queryset.order_by(label, key).values_list(key, label).distinct()
    return [("", "全部"), *pairs]


def _list_page(request, scope, *, pilot_form=None, run_form=None, status=200):
    qs = reviews.visible_closes(request.user)
    filters = CloseFilters(request.GET)
    filters.fields["warehouse"].choices = _choices(qs, "warehouse_id", "warehouse__name")
    filters.fields["owner"].choices = _choices(qs, "owner_id", "owner__name")
    for name in ["quality", "review_state"]:
        filters.fields[name].choices = [
            ("", "全部"),
            *InventoryDailyClose._meta.get_field(name).choices,
        ]
    if filters.is_valid():
        for field, lookup in (
            ("warehouse", "warehouse_id"),
            ("owner", "owner_id"),
            ("start", "business_date__gte"),
            ("end", "business_date__lte"),
            ("quality", "quality"),
            ("review_state", "review_state"),
        ):
            if filters.cleaned_data[field]:
                qs = qs.filter(**{lookup: filters.cleaned_data[field]})
    else:
        qs = qs.none()
    page = Paginator(
        qs.select_related("owner", "warehouse")
        .defer("details", "issues", "totals")
        .order_by("-business_date", "warehouse_id", "owner_id"),
        40,
    ).get_page(request.GET.get("page"))
    can_configure = _configurable(scope, request.user)
    can_run = _can_run(scope, request.user)
    pilots = (
        scope.filter_queryset(
            InventoryClosePilot.objects.select_related("warehouse").order_by("warehouse__code"),
            None,
            "warehouse_id",
        )
        if can_run or can_configure
        else ()
    )
    return render(
        request,
        "console/daily_close/list.html",
        {
            "filters": filters,
            "page": page,
            "pilots": pilots,
            "pilot_form": (pilot_form or _pilot_form(scope)) if can_configure else None,
            "run_form": (run_form or _run_form(scope)) if can_run else None,
        },
        status=status,
    )


@login_required
@require_GET
def close_list(request):
    return _list_page(request, _scope(request))


@login_required
@require_POST
def configure_pilot(request):
    scope = _scope(request)
    if not _configurable(scope, request.user):
        raise PermissionDenied
    form = _pilot_form(scope, request.POST)
    if form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            warehouse = Warehouse.objects.select_for_update().get(pk=data["warehouse"].pk)
            pilot = (
                InventoryClosePilot.objects.select_for_update().filter(warehouse=warehouse).first()
            )
            if (
                pilot
                and pilot.start_date != data["start_date"]
                and (
                    InventoryDailyClose.objects.filter(warehouse=warehouse).exists()
                    or InventoryCloseScope.objects.filter(pilot=pilot).exists()
                )
            ):
                form.add_error("start_date", "已有日结或调度记录，须保留原首个业务日。")
            else:
                InventoryClosePilot.objects.update_or_create(
                    warehouse=warehouse,
                    defaults={"start_date": data["start_date"], "enabled": data["enabled"]},
                )
                messages.success(request, "已保存试点配置。")
                return redirect("console:inventory_daily_close_list")
    return _list_page(request, scope, pilot_form=form, status=400)


@login_required
@require_POST
def manual_run(request):
    scope = _scope(request)
    if not _can_run(scope, request.user):
        raise PermissionDenied
    form = _run_form(scope, request.POST)
    if form.is_valid():
        binding = form.cleaned_data["binding"]
        try:
            close = daily_close.run_daily_close(
                owner_id=binding.owner_id,
                warehouse_id=binding.warehouse_id,
                business_date=form.cleaned_data["business_date"],
            )
        except (ValueError, ValidationError) as exc:
            form.add_error(
                None, "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            )
        else:
            return redirect("console:inventory_daily_close_detail", pk=close.pk)
    return _list_page(request, scope, run_form=form, status=400)


@login_required
@require_POST
def retry_close(request, pk):
    close = _scope_close(request.user, pk)
    if not (
        AccessScope.for_request(request).is_global
        or reviews.can_review(request.user, close.warehouse_id)
    ):
        raise PermissionDenied
    try:
        daily_close.run_daily_close(
            owner_id=close.owner_id,
            warehouse_id=close.warehouse_id,
            business_date=close.business_date,
        )
    except (ValueError, ValidationError) as exc:
        messages.error(
            request, "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        )
    else:
        messages.success(request, "已重新核查该业务日。")
    return redirect("console:inventory_daily_close_detail", pk=pk)


def _actions(user, close, opening, pilot):
    actions = []
    if not pilot or not pilot.enabled:
        return actions
    baseline_day = close.business_date >= pilot.start_date - timedelta(
        days=1
    ) and close.quality in {"MISSING", "APPROXIMATE"}
    if reviews.can_review(user, close.warehouse_id):
        if close.review_state == "WAREHOUSE" and (not opening or opening.status != "PENDING"):
            actions.append("submit")
        stale_opening = (
            opening
            and opening.status == "APPROVED"
            and any(issue.get("issue") == "opening_changed" for issue in close.issues)
        )
        if (
            baseline_day
            and close.review_state != "FINANCE"
            and (not opening or opening.status == "RETURNED" or stale_opening)
        ):
            actions.append("opening")
    if reviews.can_sign(user, close.warehouse_id):
        if close.review_state == "FINANCE" and (not opening or opening.status != "PENDING"):
            last = close.reviews.filter(action="WAREHOUSE_SUBMIT").order_by("-pk").first()
            if last and last.actor_id != user.pk:
                actions.extend(["approve", "return"])
        if opening and opening.status == "PENDING" and opening.submitted_by_id != user.pk:
            if baseline_day and close.review_state == "FINANCE":
                actions.append("approve-opening")
            actions.append("return-opening")
    return actions


def _line_page(close, rows, number):
    page = Paginator(rows or [], 40).get_page(number)
    products = {
        pk: f"{code} · {name}"
        for pk, code, name in Product.all_objects.filter(
            owner_id=close.owner_id, pk__in={row.get("product_id") for row in page}
        ).values_list("pk", "code", "name")
    }
    locations = dict(
        Location.objects.filter(
            warehouse_id=close.warehouse_id, pk__in={row.get("location_id") for row in page}
        ).values_list("pk", "code")
    )
    sources = dict(InventorySnapshotDaily.Source.choices)
    page.object_list = [
        dict(
            row,
            source_label=sources.get(row.get("snapshot_source"), row.get("snapshot_source")),
            product_label=products.get(row.get("product_id")),
            location_label=locations.get(row.get("location_id")),
        )
        for row in page
    ]
    return page


def _opening_and_pilot(close):
    opening = close.opening_versions.defer("rows").order_by("-version").first()
    pilot = InventoryClosePilot.objects.filter(warehouse_id=close.warehouse_id).first()
    return opening, pilot


def _label_event(event, internal):
    event.action_label = ACTION_LABELS.get(event.action, event.action)
    refs = event.references if internal else []
    if internal and event.action == "RECHECK":
        try:
            changes = json.loads(event.note)
        except (TypeError, ValueError):
            changes = {}
        previous = changes.get("原差异") if isinstance(changes, dict) else None
        current = changes.get("本次核查") if isinstance(changes, dict) else None
        if isinstance(previous, list) and isinstance(current, list):
            event.change_summary = f"差异项从 {len(previous)} 项更新为 {len(current)} 项。"
            event.previous_issues, event.current_issues = previous, current
    event.reference_labels = [
        f"{REFERENCE_LABELS.get(ref['type'], ref['type'])} {ref.get('number') or ref.get('id')}"
        for ref in refs
        if isinstance(ref, dict) and "type" in ref
    ]
    return event


@login_required
@require_GET
def close_detail(request, pk):
    close = _scope_close(request.user, pk)
    opening, pilot = _opening_and_pilot(close)
    scope = AccessScope.for_request(request)
    internal = scope.is_global or bool(scope.warehouse_ids)
    history = close.reviews.order_by("-pk")
    if internal:
        history = history.select_related("actor").prefetch_related("attachments")
    else:
        history = history.only("action", "created_at")
    history_page = Paginator(history, 15).get_page(request.GET.get("history"))
    for event in history_page:
        _label_event(event, internal)
    return render(
        request,
        "console/daily_close/detail.html",
        {
            "close": close,
            "actions": [
                (name, ACTIONS[name]) for name in _actions(request.user, close, opening, pilot)
            ],
            "can_retry": bool(
                pilot
                and pilot.enabled
                and (scope.is_global or reviews.can_review(request.user, close.warehouse_id))
            ),
            "details": _line_page(close, close.details, request.GET.get("lines")),
            "internal": internal,
            "issues": Paginator(close.issues or [], 30).get_page(request.GET.get("issues")),
            "history": history_page,
            "opening": opening,
            "versions": close.opening_versions.only(
                "version", "status", "submitted_at", "approved_at"
            ).order_by("-version"),
        },
    )


@login_required
@require_GET
def opening_version(request, pk, version):
    close = _scope_close(request.user, pk)
    opening = get_object_or_404(close.opening_versions, version=version)
    return render(
        request,
        "console/daily_close/opening_version.html",
        {
            "close": close,
            "opening": opening,
            "page": _line_page(close, opening.rows, request.GET.get("page")),
        },
    )


def _candidate_digest(rows):
    content = json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()


@login_required
@require_http_methods(["GET", "POST"])
def review_action(request, pk, action):
    close = _scope_close(request.user, pk)
    if action not in ACTIONS:
        raise Http404
    opening, pilot = _opening_and_pilot(close)
    if action not in _actions(request.user, close, opening, pilot):
        raise PermissionDenied("当前账号或日结状态不允许此操作。")
    is_opening = action == "opening"
    is_submission = action == "submit" or is_opening
    initial = {"version": close.revision}
    rows = opening.rows if action in {"approve-opening", "return-opening"} else []
    candidate_error = ""
    if is_opening:
        try:
            rows = daily_close.opening_candidate(close, allow_unknown_holds=True)
        except (ValueError, ValidationError) as exc:
            candidate_error = (
                "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            )
        else:
            initial["candidate_digest"] = _candidate_digest(rows)
    form_type = {
        "opening": OpeningForm,
        "submit": EvidenceForm,
        "approve": FinanceReviewApprovalForm,
        "approve-opening": FinanceApprovalForm,
    }.get(action, NoteForm)
    form = form_type(
        request.POST if request.method == "POST" else None,
        initial=initial,
        **({"rows": rows} if is_opening else {}),
    )
    if request.method == "POST" and form.is_valid() and not candidate_error:
        data = form.cleaned_data
        kwargs = {"version": data["version"], "note": data["note"]}
        if "billing_impact" in data:
            kwargs["billing_impact"] = data["billing_impact"]
        try:
            if is_submission:
                kwargs.update(references=data["references"], files=request.FILES.getlist("files"))
                if is_opening:
                    if data["candidate_digest"] != initial["candidate_digest"]:
                        raise ValidationError("日界数量已变化，请重新打开页面并核对。")
                    reviews.submit_opening(
                        request.user, pk, rows=form.rows, complete=data["complete"], **kwargs
                    )
                else:
                    reviews.submit_review(request.user, pk, **kwargs)
            elif action in {"approve-opening", "return-opening"}:
                reviews.decide_opening(
                    request.user, pk, approve=action == "approve-opening", **kwargs
                )
            else:
                reviews.decide_review(
                    request.user,
                    pk,
                    approve=action == "approve",
                    references=data.get("references", []),
                    **kwargs,
                )
        except ValidationError as exc:
            form.add_error(None, "；".join(exc.messages))
        else:
            messages.success(request, f"已完成{ACTIONS[action]}。")
            return redirect("console:inventory_daily_close_detail", pk=pk)
    submission = None
    if not is_submission:
        event_type = "OPENING_SUBMIT" if action.endswith("-opening") else "WAREHOUSE_SUBMIT"
        event = (
            close.reviews.filter(action=event_type)
            .select_related("actor")
            .prefetch_related("attachments")
            .order_by("-pk")
            .first()
        )
        if event:
            submission = _label_event(event, True)
    return render(
        request,
        "console/daily_close/action.html",
        {
            "close": close,
            "title": ACTIONS[action],
            "form": form,
            "is_submission": is_submission,
            "submission": submission,
            "is_opening_action": is_opening or action.endswith("-opening"),
            "candidate_rows": _line_page(close, rows, request.GET.get("page")),
            "candidate_error": candidate_error,
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_GET
def attachment(request, pk):
    return reviews.download_evidence(request.user, pk)
