"""Shared count permissions and blind-count response policy."""

from allapp.tasking.count_options import can_manage_count


def can_operate_count(user):
    return bool(
        can_manage_count(user)
        or (
            user
            and user.is_authenticated
            and user.is_active
            and user.has_perm("tasking.claim_task_as_wh_operator")
        )
    )


def hide_count_book(task, user, *, context=None):
    """Blind quantities stay private across all endpoints and task states."""
    if task.task_type != "COUNT" or not getattr(
        getattr(task, "counttaskextra", None), "blind", True
    ):
        return False
    # Serializer contexts live for one response; avoid repeating role-scope queries per line.
    if context is None:
        return not can_manage_count(user)
    if "count_can_manage" not in context:
        context["count_can_manage"] = can_manage_count(user)
    return not context["count_can_manage"]


def count_record_response(task, user, **data):
    if hide_count_book(task, user):
        data.pop("qty_diff", None)
    return data
