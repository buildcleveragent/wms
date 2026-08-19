"""Reusable query predicates for inbound operational records."""

from django.db.models import Q

from allapp.tasking.models import WmsTask

from .constants import (
    PDA_NO_ORDER_RECEIVE_NOTE,
    PDA_NO_ORDER_RECEIVE_SOURCE_APP,
    PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
)


def pda_no_order_receive_q() -> Q:
    """Match current and legacy PDA no-order receiving tasks."""

    return Q(task_type=WmsTask.TaskType.RECEIVE) & (
        Q(
            source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
            source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
        )
        | Q(posting_note__icontains=PDA_NO_ORDER_RECEIVE_NOTE)
    )
