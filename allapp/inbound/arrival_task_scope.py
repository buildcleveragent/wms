"""Only ordered tasks with a provable, same-owner/warehouse source."""

from django.db.models import CharField, Exists, OuterRef, Q
from django.db.models.functions import Cast, Collate

from allapp.tasking.models import WmsTask

from .constants import PDA_NO_ORDER_RECEIVE_SOURCE_APP, PDA_NO_ORDER_RECEIVE_SOURCE_MODEL
from .models import InboundOrder


def ordered_tasks(queryset):
    orders = (
        InboundOrder.objects.filter(
            is_deleted=False,
            owner_id=OuterRef("owner_id"),
            warehouse_id=OuterRef("warehouse_id"),
        )
        .annotate(source_key=Collate(Cast("pk", CharField()), "utf8mb4_bin"))
        .filter(source_key=OuterRef("source_pk"))
    )
    receive = (
        WmsTask.objects.filter(
            task_type="RECEIVE",
            owner_id=OuterRef("owner_id"),
            warehouse_id=OuterRef("warehouse_id"),
            is_deleted=False,
        )
        .annotate(
            order_exists=Exists(orders),
            source_key=Collate(Cast("pk", CharField()), "utf8mb4_bin"),
        )
        .filter(source_key=OuterRef("source_pk"))
        .filter(
            Q(
                order_exists=True,
                source_app__in=["inbound", ""],
                source_model__in=["InboundOrder", "inbound.InboundOrder", "inboundorder"],
            )
            | Q(
                source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
                source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
            )
        )
    )
    return (
        queryset.annotate(
            order_exists=Exists(orders.filter(inbound_type="PURCHASE")),
            receive_exists=Exists(receive),
        )
        .filter(
            Q(
                task_type="RECEIVE",
                source_model__in=["InboundOrder", "inbound.InboundOrder", "inboundorder"],
                source_app__in=["inbound", ""],
                order_exists=True,
            )
            | Q(
                task_type="PUTAWAY",
                source_model__in=["WmsTask", "tasking.WmsTask", "wmstask"],
                source_app__in=["tasking", ""],
                receive_exists=True,
            )
        )
        .select_related("arrival_receipt", "arrival_putaway_receipt")
    )
