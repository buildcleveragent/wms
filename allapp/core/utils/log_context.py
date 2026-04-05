from __future__ import annotations

from typing import Any, Mapping


LOG_CONTEXT_KEYS = (
    "task_id",
    "task_no",
    "order_id",
    "order_no",
    "owner_id",
    "warehouse_id",
    "user_id",
    "posting_batch",
    "journal_id",
)


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _object_value(obj: Any, *names: str) -> Any:
    if obj is None:
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return None


def _id_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (int, str)):
        return value
    return _object_value(value, "id", "pk")


def build_log_context(
    *,
    task: Any = None,
    order: Any = None,
    owner: Any = None,
    warehouse: Any = None,
    user: Any = None,
    journal: Any = None,
    task_id: Any = None,
    task_no: Any = None,
    order_id: Any = None,
    order_no: Any = None,
    owner_id: Any = None,
    warehouse_id: Any = None,
    user_id: Any = None,
    posting_batch: Any = None,
    journal_id: Any = None,
) -> dict[str, Any]:
    context = {key: None for key in LOG_CONTEXT_KEYS}
    context["task_id"] = _coalesce(task_id, _id_value(task))
    context["task_no"] = _coalesce(task_no, _object_value(task, "task_no"))
    context["order_id"] = _coalesce(
        order_id,
        _id_value(order),
        _object_value(task, "source_pk"),
    )
    context["order_no"] = _coalesce(
        order_no,
        _object_value(order, "order_no", "ref_no"),
        _object_value(task, "ref_no"),
    )
    context["owner_id"] = _coalesce(
        owner_id,
        _id_value(owner),
        _object_value(task, "owner_id"),
        _object_value(order, "owner_id"),
        _object_value(journal, "owner_id"),
    )
    context["warehouse_id"] = _coalesce(
        warehouse_id,
        _id_value(warehouse),
        _object_value(task, "warehouse_id"),
        _object_value(order, "warehouse_id"),
        _object_value(journal, "warehouse_id"),
    )
    context["user_id"] = _coalesce(user_id, _id_value(user))
    context["posting_batch"] = posting_batch
    context["journal_id"] = _coalesce(journal_id, _id_value(journal))
    return context


def format_log_context(context: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={_coalesce(context.get(key), '-')}" for key in LOG_CONTEXT_KEYS)


def build_log_payload(**kwargs: Any) -> tuple[dict[str, Any], str]:
    context = build_log_context(**kwargs)
    return context, format_log_context(context)
