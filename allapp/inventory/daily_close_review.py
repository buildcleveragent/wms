"""Warehouse evidence and independent finance decisions for an inventory close."""

import logging
from contextlib import contextmanager

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.billing.models import Bill, BillingPeriod
from allapp.inbound.models import InboundOrder
from allapp.outbound.models import OutboundOrder
from allapp.reports.models import AlertCase, AlertCaseHistory
from allapp.tasking.models import WmsTask

from . import daily_close_storage as storage
from .daily_close_transaction import close_transaction
from .models import (
    InventoryCloseAttachment,
    InventoryCloseReview,
    InventoryDailyClose,
    InventoryOpeningVersion,
)
from .snapshot_services import _lock_snapshot_scope

ALERT_TYPE = "INVENTORY_DAILY_CLOSE"
VIEW = "inventory.view_inventorydailyclose"
REVIEW = "inventory.review_inventorydailyclose"
SIGN = "inventory.sign_inventorydailyclose"
ROLE = UserRoleScope.Role
logger = logging.getLogger(__name__)


def can_view(user, *, scope=None):
    scope = scope or AccessScope.for_user(user)
    if not scope.is_valid:
        return False
    if scope.is_global:
        return user.has_perm(VIEW)
    if scope.source != "user_role_scope":
        return False
    if ROLE.OWNER_FINANCE in scope.roles:
        return bool(scope.owner_ids) and user.has_perm(VIEW)
    return bool(scope.roles & UserRoleScope.WAREHOUSE_ROLES) and any(
        user.has_perm(p) for p in (VIEW, REVIEW, SIGN)
    )


def visible_closes(user):
    scope = AccessScope.for_user(user)
    qs = InventoryDailyClose.objects.all()
    return scope.filter_queryset(qs) if can_view(user, scope=scope) else qs.none()


def can_review(user, warehouse_id):
    scope = AccessScope.for_user(user)
    return (
        scope.source == "user_role_scope"
        and ROLE.WAREHOUSE_MANAGER in scope.roles
        and scope.allows(warehouse_id=warehouse_id)
        and user.has_perm(REVIEW)
    )


def can_sign(user, warehouse_id):
    scope = AccessScope.for_user(user)
    return (
        scope.source == "user_role_scope"
        and bool(scope.roles & UserRoleScope.WAREHOUSE_ROLES)
        and scope.allows(warehouse_id=warehouse_id)
        and user.has_perm(SIGN)
    )


def _locked_close(user, close_id, version, capability):
    from .daily_close import DAY, pilot_covers_date, shanghai_now

    close = get_object_or_404(visible_closes(user).select_for_update(), pk=close_id)
    if not capability(user, close.warehouse_id):
        raise PermissionDenied("当前账号没有此仓库的日结处理权限。")
    if str(version) != str(close.revision):
        raise ValidationError("日结已更新，请刷新后重新核对。")
    if close.business_date >= shanghai_now().date() or not pilot_covers_date(
        close.owner_id, close.warehouse_id, close.business_date + DAY
    ):
        raise ValidationError("此日结不在启用的试点日期范围内。")
    return close


def _checked_note(note):
    if not isinstance(note, str) or not note.strip() or len(note) > 4000:
        raise ValidationError("请填写复核说明（最多 4000 字）。")
    return note.strip()


def _checked_refs(close, refs):
    if not isinstance(refs, (list, tuple)) or len(refs) > 30:
        raise ValidationError("凭据引用格式无效，每次最多关联 30 项。")
    tenant = dict(owner_id=close.owner_id, warehouse_id=close.warehouse_id)
    checked = []
    documents = {
        "inbound": (InboundOrder, "order_no", {}),
        "outbound": (OutboundOrder, "order_no", {}),
        "bill": (Bill, "invoice_no", {}),
        "period": (BillingPeriod, "label", {}),
        "count": (WmsTask, "task_no", {"task_type": "COUNT", "posting_status": "POSTED"}),
        "adjustment": (WmsTask, "task_no", {"task_type": "ADJUST", "posting_status": "POSTED"}),
    }
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("type"), str):
            raise ValidationError("凭据引用格式无效。")
        kind = ref["type"]
        if kind == "external":
            number = ref.get("number")
            if not isinstance(number, str) or not number.strip() or len(number) > 180:
                raise ValidationError("请填写有效的外部档案号。")
            item = {"type": kind, "number": number.strip()}
        else:
            if kind not in documents:
                raise ValidationError("凭据类型无效。")
            ident = str(ref.get("id", ""))
            if not ident.isascii() or not ident.isdecimal() or not 0 < len(ident) <= 18:
                raise ValidationError("内部单据需要数字主键。")
            model, field, filters = documents[kind]
            number = (
                model.objects.filter(pk=int(ident), **tenant, **filters)
                .values_list(field, flat=True)
                .first()
            )
            if number is None:
                raise ValidationError("关联单据不存在、不在本货主及仓库范围，或尚未过账。")
            item = {"type": kind, "id": int(ident), "number": number}
        if item not in checked:
            checked.append(item)
    return checked


def sync_close_alert(close, *, actor=None):
    """Synchronize one source-owned alert while holding the daily close row lock."""
    state = close.review_state
    if state == "NONE":
        return
    status = {"WAREHOUSE": "OPEN", "FINANCE": "IN_PROGRESS", "CLOSED": "CLOSED"}[state]
    now = timezone.now()
    fields = {
        "owner_id": close.owner_id,
        "warehouse_id": close.warehouse_id,
        "alert_type": ALERT_TYPE,
        "source_type": "InventoryDailyClose",
        "source_id": str(close.pk),
        "severity": (
            AlertCase.Severity.HIGH if close.quality == "MISSING" else AlertCase.Severity.WARNING
        ),
        "detail": {
            "business_date": close.business_date.isoformat(),
            "quality": close.quality,
            "review_state": state,
        },
        "title": f"库存日结差异复核 · {close.business_date}",
    }
    case, created = AlertCase.objects.select_for_update().get_or_create(
        dedup_key=f"INV_DAILY_CLOSE:{close.pk}",
        defaults={**fields, "first_seen_at": now, "last_seen_at": now},
    )
    before = "" if created else case.status
    for name, value in fields.items():
        setattr(case, name, value)
    case.last_seen_at = now
    case.status = status
    if status == "CLOSED":
        case.closed_at = case.closed_at or now
        case.resolved_at = case.resolved_at or now
    else:
        case.closed_at = case.resolved_at = None
    case.save()
    if before != case.status:
        AlertCaseHistory.objects.create(
            case=case,
            action="OPEN" if created else ("REOPEN" if before == "CLOSED" else state),
            from_status=before,
            to_status=case.status,
            created_by=actor,
        )
    if close.case_id != case.pk:
        close.case = case
        close.save(update_fields=["case"])


@contextmanager
def _review_transaction(user, close_id, version, capability, *, opening=False):
    ids = None
    if opening:
        # Resolve the tenant before starting InnoDB's consistent read view. Scope
        # locks must be acquired before any ordinary SELECT in the transaction.
        ids = get_object_or_404(
            visible_closes(user).values("owner_id", "warehouse_id"), pk=close_id
        )
        if not capability(user, ids["warehouse_id"]):
            raise PermissionDenied("当前账号没有此仓库的日结处理权限。")
    written = []
    try:
        with close_transaction() if opening else transaction.atomic(durable=True):
            if ids is not None:
                _lock_snapshot_scope(**ids)
            close = _locked_close(user, close_id, version, capability)
            yield close, written
    except BaseException:
        for key in written:
            try:
                # A post-commit callback may have failed after the evidence row
                # became durable. Only compensate when the row did roll back.
                if not InventoryCloseAttachment.objects.filter(storage_key=key).exists():
                    storage.evidence_path(key).unlink(missing_ok=True)
            except (OSError, DatabaseError, ValidationError):
                logger.exception("日结凭据回滚时无法核实文件 %s", key)
        raise


def _record(close, user, action, note, refs, state):
    event = InventoryCloseReview.objects.create(
        close=close,
        actor=user,
        action=action,
        note=_checked_note(note),
        references=refs,
    )
    close.review_state = state
    close.revision += 1
    close.save(update_fields=["review_state", "revision"])
    sync_close_alert(close, actor=user)
    return event


def _attach(event, prepared, written):
    for file in prepared:
        key = storage.write_evidence(event.close_id, file)
        written.append(key)
        InventoryCloseAttachment.objects.create(
            review=event,
            file_name=file.file_name,
            content_type=file.content_type,
            size=len(file.data),
            storage_key=key,
        )


def _recheck_evidence(close, submission):
    refs = _checked_refs(close, submission.references)
    if refs != submission.references:
        raise ValidationError("关联单据已变更，请退回后重新核对。")
    files = list(submission.attachments.all())
    for item in files:
        path = storage.evidence_path(item.storage_key, close_id=close.pk)
        if not path.is_file() or path.stat().st_size != item.size:
            raise ValidationError("原始凭据缺失或大小不符，请退回核对。")
    if not refs and not files:
        raise ValidationError("缺少仓管提交的原始凭据。")
    return refs, bool(files)


BILLING_IMPACTS = {"NO_CHANGE": "无需账务处理", "FOLLOW_UP": "需按原流程核对账单或调整"}


def _billing_decision(value, approve):
    if not value and not approve:
        return []
    if not isinstance(value, str) or value not in BILLING_IMPACTS:
        raise ValidationError("请选择对计费和账单的影响，并在签核说明中记录核对依据。")
    return [{"type": "billing_impact", "value": value, "number": BILLING_IMPACTS[value]}]


def submit_review(user, close_id, *, version, note, references, files=()):
    with _review_transaction(user, close_id, version, can_review) as (close, written):
        if (
            close.review_state != "WAREHOUSE"
            or close.opening_versions.filter(status="PENDING").exists()
        ):
            raise ValidationError("此日结当前不能提交仓管复核。")
        refs = _checked_refs(close, references)
        prepared = storage.prepare_files(files)
        if not refs and not prepared:
            raise ValidationError("请附上复核凭据或关联原始单据。")
        event = _record(close, user, "WAREHOUSE_SUBMIT", note, refs, "FINANCE")
        _attach(event, prepared, written)
        return close


@transaction.atomic
def decide_review(user, close_id, *, version, approve, note, references=(), billing_impact=None):
    close = _locked_close(user, close_id, version, can_sign)
    if close.review_state != "FINANCE" or close.opening_versions.filter(status="PENDING").exists():
        raise ValidationError("此日结当前没有待签的差异复核。")
    submission = close.reviews.filter(action="WAREHOUSE_SUBMIT").order_by("-pk").first()
    if not submission or submission.actor_id == user.pk:
        raise PermissionDenied("请由另一名授权财务人员签核。")
    if not isinstance(approve, bool):
        raise ValidationError("请明确选择签核或退回。")
    if approve:
        _recheck_evidence(close, submission)
    refs = _checked_refs(close, references) + _billing_decision(billing_impact, approve)
    _record(
        close,
        user,
        "FINANCE_APPROVE" if approve else "FINANCE_RETURN",
        note,
        refs,
        "CLOSED" if approve else "WAREHOUSE",
    )
    return close


def submit_opening(user, close_id, *, version, note, references, files=(), rows, complete):
    from .daily_close import opening_is_current
    from .daily_close_opening import checked_opening_rows

    with _review_transaction(user, close_id, version, can_review, opening=True) as (close, written):
        if complete is not True:
            raise ValidationError("请先确认日切时点、货主全仓范围、实盘与占用凭据。")
        approved = close.opening_versions.filter(status="APPROVED").order_by("-version").first()
        if (
            close.quality not in ("MISSING", "APPROXIMATE")
            or close.review_state == "FINANCE"
            or close.opening_versions.filter(status="PENDING").exists()
            or (approved is not None and opening_is_current(approved, close))
        ):
            raise ValidationError("此日结当前不能提交新的期初版本。")
        refs = _checked_refs(close, references)
        prepared = storage.prepare_files(files)
        confirmed = checked_opening_rows(close, rows, refs, has_files=bool(prepared))
        last = close.opening_versions.order_by("-version").values_list("version", flat=True).first()
        InventoryOpeningVersion.objects.create(
            close=close,
            submitted_by=user,
            version=(last or 0) + 1,
            rows=confirmed,
        )
        event = _record(close, user, "OPENING_SUBMIT", note, refs, "FINANCE")
        _attach(event, prepared, written)
        return close


def decide_opening(user, close_id, *, version, approve, note, billing_impact=None):
    from .daily_close_opening import checked_opening_rows

    with _review_transaction(user, close_id, version, can_sign, opening=True) as (close, _):
        pending = close.opening_versions.filter(status="PENDING").first()
        if pending is None or not isinstance(approve, bool):
            raise ValidationError("当前没有可签核的期初版本，或未选择签核决定。")
        if pending.submitted_by_id == user.pk:
            raise PermissionDenied("请由另一名授权财务人员签核。")
        if approve:
            if close.review_state != "FINANCE" or close.quality not in ("MISSING", "APPROXIMATE"):
                raise ValidationError("提交后日结已变化，请退回仓管重新核对。")
            event = close.reviews.filter(action="OPENING_SUBMIT").order_by("-pk").first()
            if event is None or event.actor_id != pending.submitted_by_id:
                raise ValidationError("未找到此版本的仓管签核及原始凭据。")
            refs, has_files = _recheck_evidence(close, event)
            confirmed = checked_opening_rows(close, pending.rows, refs, has_files=has_files)
            if confirmed != pending.rows:
                raise ValidationError("提交后日终依据已经变化，请退回重新核对。")
        pending.status = "APPROVED" if approve else "RETURNED"
        pending.decided_at = timezone.now()
        if approve:
            pending.approved_by = user
            pending.approved_at = pending.decided_at
        pending.save(update_fields=["status", "decided_at", "approved_by", "approved_at"])
        _record(
            close,
            user,
            "OPENING_APPROVE" if approve else "OPENING_RETURN",
            note,
            _billing_decision(billing_impact, approve),
            "CLOSED" if approve else "WAREHOUSE",
        )
        return close


def download_evidence(user, attachment_id):
    """Authorize the original review scope on each download."""
    scope = AccessScope.for_user(user)
    if not can_view(user, scope=scope) or (not scope.is_global and not scope.warehouse_ids):
        raise PermissionDenied("需要相应仓库的日结凭据查看权限。")
    qs = scope.filter_queryset(
        InventoryCloseAttachment.objects.select_related("review"),
        owner_field="review__close__owner_id",
        warehouse_field="review__close__warehouse_id",
    )
    item = get_object_or_404(qs, pk=attachment_id)
    try:
        path = storage.evidence_path(item.storage_key, close_id=item.review.close_id)
        file = path.open("rb")
    except (FileNotFoundError, ValidationError):
        raise Http404("日结凭据不存在。") from None
    response = FileResponse(
        file, as_attachment=True, filename=item.file_name, content_type=item.content_type
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
