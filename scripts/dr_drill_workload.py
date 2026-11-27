"""Inside the drill's isolated WMS runtime: create synthetic documents and verify their photos."""

import hashlib
import io
import json
import os
import select
import sys
from contextlib import redirect_stdout
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import django
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone
from PIL import Image


def jpg(sequence):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), (sequence % 250, 63, 189)).save(
        stream, "JPEG", quality=95, comment=f"synthetic-photo-{sequence}".encode("ascii")
    )
    return ContentFile(stream.getvalue(), name=f"dr-{sequence}.jpg")


def write_close_evidence(owner, warehouse, actor, day):
    from allapp.inventory.daily_close_storage import prepare_files, write_evidence
    from allapp.inventory.models import (
        InventoryCloseAttachment,
        InventoryCloseReview,
        InventoryDailyClose,
    )

    with transaction.atomic():
        close = InventoryDailyClose.objects.create(
            owner=owner,
            warehouse=warehouse,
            business_date=day,
            review_state=InventoryDailyClose.ReviewState.FINANCE,
        )
        review = InventoryCloseReview.objects.create(
            close=close,
            actor=actor,
            action="WAREHOUSE_SUBMIT",
            note="Synthetic DR fixture",
        )
        stream = io.BytesIO()
        Image.new("RGB", (16, 16), "white").save(stream, "PDF")
        (document,) = prepare_files([ContentFile(stream.getvalue(), name="dr-count.pdf")])
        key = write_evidence(close.pk, document)
        InventoryCloseAttachment.objects.create(
            review=review,
            storage_key=key,
            file_name=document.file_name,
            content_type=document.content_type,
            size=len(document.data),
        )


def write_stream():
    from django.contrib.auth import get_user_model

    from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding, Supplier
    from allapp.billing.enums import CalcMethod, ChargeType
    from allapp.billing.models import BillingRule
    from allapp.billing.services import accrue_metrics_for_date, generate_metrics_for_date
    from allapp.inbound.arrival_models import ArrivalPhoto
    from allapp.inbound.customer_return_models import (
        CustomerReturn,
        CustomerReturnLine,
        CustomerReturnPhoto,
    )
    from allapp.inbound.models import (
        InboundOrder,
        InboundOrderLine,
        InboundReceipt,
        InboundReceiptLine,
    )
    from allapp.inventory.services_quick_adjust import QuickAdjustInput, quick_adjust_via_post_task
    from allapp.locations.models import Location, Subwarehouse, Warehouse
    from allapp.outbound.models import OutboundOrder, OutboundOrderLine
    from allapp.products.models import Product, ProductUom
    from scripts.run_system_tests import _schema_identity

    _schema_identity(schema=True)
    owner = Owner.objects.create(code="DR", name="DR owner")
    warehouse = Warehouse.objects.create(code="DR", name="DR warehouse")
    OwnerWarehouseBinding.objects.create(owner=owner, warehouse=warehouse)
    Subwarehouse.objects.create(warehouse=warehouse, code="DR", name="DR")
    location = Location.objects.create(warehouse=warehouse, code="DR-01-01-01", name="DR")
    user = get_user_model().objects.create_user(
        username="dr-user", owner=owner, warehouse=warehouse
    )
    customer = Customer.objects.create(owner=owner, salesperson=user, code="DR", name="DR")
    supplier = Supplier.objects.create(owner=owner, code="DR", name="DR")
    unit = ProductUom.objects.create(code="DR", name="piece")
    product = Product.objects.create(
        owner=owner,
        name="DR",
        code="DR",
        sku="DR",
        base_uom=unit,
        volume=Decimal("0.25"),
        batch_control=False,
        expiry_control=False,
        product_image=jpg(0),
    )
    quick_adjust_via_post_task(
        QuickAdjustInput(
            user=user,
            owner=owner,
            warehouse=warehouse,
            location=location,
            product=product,
            qty_base_delta=Decimal("5"),
            reason="DRILL",
            remark="synthetic recovery drill",
        )
    )
    BillingRule.objects.create(
        owner=owner,
        warehouse=warehouse,
        charge_type=ChargeType.STORAGE,
        calc_method=CalcMethod.PER_CBM_DAY,
        unit_price=Decimal("2.00"),
    )
    day = timezone.now().date()
    generate_metrics_for_date(owner.pk, warehouse.pk, day, metric_types=["CBM"])
    accrue_metrics_for_date(owner.pk, warehouse.pk, day)
    write_close_evidence(owner, warehouse, user, day - timedelta(days=1))
    batch_size = 5
    for sequence in range(1, batch_size + 1):
        with transaction.atomic():
            number = f"DR-{sequence:04d}"
            arrival = InboundOrder.objects.create(
                owner=owner,
                warehouse=warehouse,
                supplier=supplier,
                order_no=number,
                created_by=user,
            )
            ordered = InboundOrderLine.objects.create(order=arrival, product=product, base_qty=1)
            receipt = InboundReceipt.objects.create(
                owner=owner,
                warehouse=warehouse,
                supplier=supplier,
                order=arrival,
                receipt_no=number,
                biz_date=day,
            )
            received = InboundReceiptLine.objects.create(
                receipt=receipt,
                order_line=ordered,
                line_no=1,
                product=product,
                base_qty=1,
                qty_ok=1,
            )
            ArrivalPhoto.objects.create(line=received, actor=user, file=jpg(sequence * 2))
            if sequence == batch_size:
                # Ask for quiescence while the last document pair is still being written.
                print("DR_STREAM_READY", file=sys.__stdout__, flush=True)
                if (
                    not select.select([sys.stdin], [], [], 90)[0]
                    or sys.stdin.readline().strip() != "STOP"
                ):
                    raise RuntimeError("Missing controlled writer stop signal")
            sale = OutboundOrder.objects.create(
                owner=owner,
                warehouse=warehouse,
                customer=customer,
                order_no=number,
                created_by=user,
            )
            sold = OutboundOrderLine.objects.create(
                order=sale, product=product, base_uom=unit, base_qty=1
            )
            returned = CustomerReturn.objects.create(
                owner=owner,
                warehouse=warehouse,
                original_order=sale,
                number=number,
                created_by=user,
            )
            line = CustomerReturnLine.objects.create(
                order=returned,
                original_line=sold,
                product=product,
                product_name=product.name,
                product_code=product.code,
                unit=unit.code,
                qty=1,
                reason_code="OTHER",
                reason="synthetic recovery drill",
            )
            CustomerReturnPhoto.objects.create(
                order=returned,
                line=line,
                actor=user,
                purpose="APPLICATION",
                content_type="image/jpeg",
                file=jpg(sequence * 2 + 1),
            )
    print(json.dumps({"committed": batch_size}), file=sys.__stdout__, flush=True)


def post_snapshot_receipt():
    """Commit one later attachment to prove we restored the cut rather than the live source."""
    from allapp.inbound.arrival_models import ArrivalPhoto

    last = ArrivalPhoto.objects.select_related("line", "actor").latest("pk")
    photo = ArrivalPhoto.objects.create(line=last.line, actor=last.actor, file=jpg(251))
    print(json.dumps({"arrival_photo_id": photo.pk, "path": photo.file.name}))


def photo_manifest(kind, root, rows):
    entries = {}
    referenced = set()
    for document, line_document, filename in rows:
        if (
            document != line_document
            or document in entries
            or not filename
            or filename in referenced
        ):
            raise RuntimeError(f"Unexpected fixture identity in {kind}: {document}")
        referenced.add(filename)
        contents = (Path(root) / filename).read_bytes()
        with Image.open(io.BytesIO(contents)) as photo:
            photo.verify()
        entries[document] = {"path": filename, "sha256": hashlib.sha256(contents).hexdigest()}
    files = {p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file()}
    if files != referenced:
        raise RuntimeError(f"Unreferenced or missing files in {kind}")
    return entries


def snapshot():
    from allapp.inbound.arrival_models import ArrivalPhoto
    from allapp.inbound.customer_return_models import CustomerReturn, CustomerReturnPhoto
    from allapp.inbound.models import InboundReceipt
    from allapp.inventory.daily_close_storage import evidence_path, private_close_root
    from allapp.inventory.models import InventoryCloseAttachment
    from allapp.products.models import Product

    result = {}
    evidence = (
        (
            "media",
            settings.MEDIA_ROOT,
            Product.objects.exclude(product_image="")
            .exclude(product_image__isnull=True)
            .values_list("code", "code", "product_image"),
        ),
        (
            "private_arrivals",
            settings.PRIVATE_ARRIVAL_ROOT,
            ArrivalPhoto.objects.values_list(
                "line__receipt__order__order_no", "line__order_line__order__order_no", "file"
            ),
        ),
        (
            "private_returns",
            settings.PRIVATE_RETURN_ROOT,
            CustomerReturnPhoto.objects.values_list("order__number", "line__order__number", "file"),
        ),
    )
    for kind, root, rows in evidence:
        result[kind] = photo_manifest(kind, root, rows)
    for kind, documents in (
        ("private_arrivals", InboundReceipt.objects.values_list("receipt_no", flat=True)),
        ("private_returns", CustomerReturn.objects.values_list("number", flat=True)),
    ):
        if set(documents) != result[kind].keys():
            raise RuntimeError(f"Document without its attachment in {kind}")
    close_files = {}
    for attachment in InventoryCloseAttachment.objects.select_related("review__close"):
        close = attachment.review.close
        key = attachment.storage_key
        contents = evidence_path(key, close_id=close.pk).read_bytes()
        if len(contents) != attachment.size:
            raise RuntimeError("Close evidence size differs from its attachment record")
        close_files[key] = {
            "owner": close.owner_id,
            "warehouse": close.warehouse_id,
            "date": close.business_date.isoformat(),
            "review": attachment.review_id,
            "sha256": hashlib.sha256(contents).hexdigest(),
        }
    root = private_close_root()
    actual = {file.relative_to(root).as_posix() for file in root.rglob("*") if file.is_file()}
    if not close_files or actual != close_files.keys():
        raise RuntimeError("Missing or unreferenced close evidence")
    result["private_close"] = close_files
    print(json.dumps(result))


def verify_business():
    from allapp.baseinfo.models import Owner
    from allapp.billing.models import BillingAccrual, BillingJobRun
    from allapp.inventory.models import InventoryDetail, InventorySummary
    from allapp.locations.models import Warehouse
    from scripts.run_system_tests import _schema_identity

    identity = _schema_identity(schema=True)
    owner = Owner.objects.get(code="DR")
    warehouse = Warehouse.objects.get(code="DR")
    detail = InventoryDetail.objects.get(owner=owner)
    summary = InventorySummary.objects.get(owner=owner)
    accrual = BillingAccrual.objects.get(owner=owner)
    if detail.onhand_qty != 5 or summary.onhand_qty != 5 or accrual.amount != Decimal("2.50"):
        raise RuntimeError("Recovered inventory or billed amount differs from posted fixture")
    call_command("reconcile_data_accuracy", fail_on_issues=True)
    call_command("collectstatic", interactive=False, verbosity=0)
    if not (Path(settings.STATIC_ROOT) / "admin/css/base.css").is_file():
        raise RuntimeError("Public static assets were not regenerated")
    day = accrual.service_date
    call_command(
        "billing_run_scheduler",
        once=True,
        dates=[day.isoformat()],
        owner=owner.pk,
        warehouse=warehouse.pk,
        metric_types=["CBM"],
    )
    job = BillingJobRun.objects.get(owner=owner, warehouse=warehouse, service_date=day)
    if job.status not in ("SUCCESS", "WARNING") or "CBM" not in job.summary["metric_coverage"]:
        raise RuntimeError("Restored scheduler did not complete the requested scope")
    print(
        json.dumps(
            {
                "inventory_qty": str(detail.onhand_qty),
                "accrual_amount": str(accrual.amount),
                "scheduler_status": job.status,
                "database": identity["database"],
                "mysql_version": identity["mysql_version"],
                "migration_heads": identity["migration_heads"],
            }
        )
    )


if __name__ == "__main__":
    django.setup()
    from docker.check_volume_access import check_release_volumes
    from wmsmaster.test_database import validate_test_process, verify_test_database

    validate_test_process(settings.DATABASES["default"])
    verify_test_database(settings.DATABASES["default"])
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@server_uuid")
        if cursor.fetchone()[0] != os.environ["DR_MYSQL_UUID"]:
            raise RuntimeError("MySQL server differs from the isolated instance")
    if sys.argv[1] in ("write", "verify"):
        check_release_volumes()
    if sys.argv[1] == "write":
        with redirect_stdout(sys.stderr):
            write_stream()
    else:
        {"snapshot": snapshot, "postcut": post_snapshot_receipt, "verify": verify_business}[
            sys.argv[1]
        ]()
