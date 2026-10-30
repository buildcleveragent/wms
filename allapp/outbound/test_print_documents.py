from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from allapp.core.print_tickets import issue_print_ticket
from allapp.inventory.models import InventoryTransaction, PostingJournal
from allapp.locations.models import Warehouse
from allapp.pos.models import PosReceiptWarehouseInfo
from allapp.tasking.models import TaskScanLog, WmsTaskLine

from .export_print import pick_task_print
from .models import OutboundOrderLine
from .print_documents import build_document
from .test_physical_review import PhysicalReviewFixture


class OutboundDocumentTests(PhysicalReviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.info = PosReceiptWarehouseInfo.objects.create(
            warehouse=self.wh, name="打印仓", address="仓储路88号", phone="0771-1234567"
        )

    def printed(self, task, kind="delivery", user=None, **params):
        request = RequestFactory().get(
            f"/api/pda/pick-tasks/{task.pk}/print/", {"kind": kind, **params}
        )
        request.user = user or self.reviewer
        return pick_task_print(request, task.pk)

    def posted(self):
        task = self.ready_pick()
        result = self.post_review(task)
        self.assertEqual(result.status_code, 200, result.data)
        task.refresh_from_db()
        return task

    def test_pick_has_no_prices_and_delivery_requires_successful_review(self):
        task = self.ready_pick()
        response = self.printed(task, "pick", self.picker)
        self.assertContains(response, "拣货单")
        self.assertContains(response, "实拣填写")
        self.assertNotContains(response, "单价")
        self.assertNotContains(response, "金额")
        self.assertEqual(self.printed(task, user=self.picker).status_code, 409)
        task.review_status = "APPROVED"
        task.posting_status = "FAILED"
        task.save(update_fields=["review_status", "posting_status"])
        self.assertEqual(self.printed(task, user=self.picker).status_code, 409)
        task.posting_status = "NOT_READY"
        task.status = "CANCELLED"
        task.review_status = "NOT_READY"
        task.save(update_fields=["status", "review_status", "posting_status"])
        self.assertEqual(self.printed(task, "pick", self.picker).status_code, 409)

    def test_posted_document_header_completed_search_reprint_and_idempotency(self):
        task = self.posted()
        response = self.printed(task)
        self.assertContains(response, "送货单")
        self.assertContains(response, self.info.address)
        self.assertContains(response, self.info.phone)
        html = response.content.decode()
        self.assertLess(
            (
                html.index('class="warehouse-address"')
                if 'class="warehouse-address"' in html
                else html.index('warehouse-address">')
            ),
            html.index('<table class="goods">'),
        )
        self.assertEqual(self.queue(stage="completed", search=task.ref_no).data["count"], 1)
        self.assertEqual(self.queue(stage="completed", search="不存在的客户").data["count"], 0)
        self.assertEqual(
            self.queue(stage="completed", search=self.customer.name, page_size=1).data["count"], 1
        )
        self.assertTrue(self.context(task).data["task"]["can_print_delivery"])
        count = InventoryTransaction.objects.filter(src_id=task.pk, src_model="WmsTask").count()
        self.assertTrue(self.post_review(task).data["idempotent"])
        self.assertEqual(self.printed(task).status_code, 200)
        self.assertEqual(
            InventoryTransaction.objects.filter(src_id=task.pk, src_model="WmsTask").count(), count
        )

    def test_missing_blank_address_and_selection_never_output_formal_blank_note(self):
        task = self.posted()
        self.info.address = "  "
        self.info.save()
        self.assertContains(self.printed(task), "补充", status_code=409)
        self.info.address = "地址甲"
        self.info.save()
        common = PosReceiptWarehouseInfo.objects.create(name="公共地址", address="地址乙")
        response = self.printed(task)
        self.assertNotContains(response, '<table class="goods">')
        self.assertNotContains(response, 'onload="window.print()"')
        self.assertContains(
            self.printed(task, warehouse_info_id=common.pk), 'onload="window.print()"'
        )

    def test_manual_pick_print_does_not_depend_on_delivery_address_configuration(self):
        task = self.ready_pick()
        common = PosReceiptWarehouseInfo.objects.create(name="公共地址", address="地址乙")
        for configuration in ("multiple", "single", "none"):
            with self.subTest(configuration=configuration):
                if configuration == "single":
                    common.delete()
                elif configuration == "none":
                    self.info.delete()
                # A stale delivery address parameter must not block warehouse work.
                response = self.printed(task, "pick", self.picker, warehouse_info_id="stale")
                self.assertContains(response, '<table class="goods">')
                self.assertContains(response, 'onload="window.print()"')
                self.assertNotContains(response, 'name="warehouse_info_id"')
                self.assertNotContains(response, "仓库地址：")

    def test_all_source_lines_must_match_and_source_cannot_be_guessed(self):
        task = self.ready_pick()
        line = task.lines.get()
        WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=self.loc,
            qty_plan=1,
            src_model="OutboundOrderLine",
            src_id=99999999,
        )
        self.assertEqual(self.printed(task, "pick", self.picker).status_code, 409)
        task.lines.exclude(pk=line.pk).delete()
        task.source_pk = ""
        task.save(update_fields=["source_pk"])
        self.assertEqual(self.printed(task, "pick", self.picker).status_code, 409)

    def test_print_ticket_scopes_and_expiry(self):
        task = self.posted()
        path = f"/api/pda/pick-tasks/{task.pk}/print/"
        token = issue_print_ticket(self.reviewer, path)
        self.assertEqual(self.printed(task, user=AnonymousUser(), ticket=token).status_code, 200)
        with patch("django.core.signing.time.time", return_value=9999999999):
            self.assertEqual(
                self.printed(task, user=AnonymousUser(), ticket=token).status_code, 401
            )
        other = self.operator(
            "foreign-reviewer", Warehouse.objects.create(code="PRTOTHER", name="其他仓")
        )
        self.assertEqual(self.printed(task, user=other).status_code, 404)
        self.client.force_authenticate(other)
        self.assertEqual(self.queue(stage="completed").data["count"], 0)

    def test_no_order_quantity_fallback_and_journal_and_ledger_must_agree(self):
        task = self.posted()
        scan = TaskScanLog.objects.get(task=task)
        for changes in (
            {"posting_journal_id": None},
            {"qty_base_delta": Decimal("1")},
            {"status": "FAIL"},
        ):
            with self.subTest(changes=changes):
                previous = {key: getattr(scan, key) for key in changes}
                TaskScanLog.objects.filter(pk=scan.pk).update(**changes)
                self.assertEqual(self.printed(task).status_code, 409)
                TaskScanLog.objects.filter(pk=scan.pk).update(**previous)
        PostingJournal.objects.filter(src_model="WmsTask", src_id=task.pk).update(status="FAILED")
        self.assertEqual(self.printed(task).status_code, 409)
        PostingJournal.objects.filter(src_model="WmsTask", src_id=task.pk).update(status="POSTED")
        InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk).update(
            qty_delta=-99
        )
        self.assertEqual(self.printed(task).status_code, 409)
        TaskScanLog.objects.filter(task=task).delete()
        self.assertEqual(self.printed(task).status_code, 409)

    def test_actual_quantity_signed_correction_and_distinct_prices(self):
        task = self.ready_pick()
        line = task.lines.get()
        # Keep differently priced order lines separate even when stock posting aggregates them.
        first = OutboundOrderLine.objects.get(pk=line.src_id)
        OutboundOrderLine.objects.filter(pk=first.pk).update(base_qty=9, base_price=Decimal("10"))
        second = OutboundOrderLine.objects.create(
            order=first.order,
            product=self.product,
            base_uom=self.uom,
            base_qty=8,
            base_price=Decimal("20"),
            line_no=first.line_no + 1,
        )
        split = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=self.loc,
            qty_plan=1,
            qty_done=1,
            src_model="OutboundOrderLine",
            src_id=second.pk,
            plan_meta=line.plan_meta,
        )
        self.stock.allocated_qty += Decimal(1)
        self.stock.save()
        line.qty_done = Decimal("2")
        line.save(update_fields=["qty_done"])
        for suffix, task_line, qty in (("correction", line, "-0.125"), ("second", split, "1")):
            TaskScanLog.objects.create(
                task=task,
                task_line=task_line,
                owner=self.owner,
                warehouse=self.wh,
                product=self.product,
                location=self.loc,
                method="MANUAL",
                qty_base_delta=Decimal(qty),
                fp=f"print-{suffix}-{task.pk}",
                scan_snapshot_rev=0,
            )
        result = self.post_review(task)
        self.assertEqual(result.status_code, 200, result.data)
        task.refresh_from_db()
        _, rows = build_document(task, "delivery")
        self.assertEqual(
            [(row.base_qty, row.amount) for row in rows],
            [(Decimal(2), Decimal(20)), (Decimal(1), Decimal(20))],
        )
        OutboundOrderLine.objects.filter(pk=first.pk).update(is_priced=False, base_price=0)
        OutboundOrderLine.objects.filter(pk=second.pk).update(base_price=0)
        _, rows = build_document(task, "delivery")
        self.assertIsNone(rows[0].amount)
        self.assertEqual(rows[1].amount, 0)
        response = self.printed(task)
        self.assertContains(response, "未计价")
        self.assertContains(response, "0.00")

    def test_split_locations_batches_aggregate_only_for_delivery(self):
        from allapp.inventory.models import InventoryDetail
        from allapp.locations.models import Location

        task = self.ready_pick()
        first = task.lines.get()
        # Preserve two warehouse allocations and their distinct batch identities.
        self.stock.refresh_from_db()
        InventoryDetail.objects.filter(pk=self.stock.pk).update(batch_no="LOT-A")
        TaskScanLog.objects.filter(task=task).update(
            qty_base_delta=Decimal("1.125"), lot_no="LOT-A"
        )
        first.qty_plan = first.qty_done = Decimal("1.125")
        first.save(update_fields=["qty_plan", "qty_done"])
        location = Location.objects.create(
            warehouse=self.wh, subwarehouse=self.sub, code="REORDERS-01-01-02", name="二号库位"
        )
        stock = InventoryDetail.objects.get(pk=self.stock.pk)
        stock.pk = None
        stock.location = location
        stock.batch_no = "LOT-B"
        stock.onhand_qty = 5
        stock.allocated_qty = 1
        stock.save()
        split = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=location,
            qty_plan=1,
            qty_done=1,
            src_model="OutboundOrderLine",
            src_id=first.src_id,
            plan_meta={"inventory_detail_id": stock.pk},
        )
        TaskScanLog.objects.create(
            task=task,
            task_line=split,
            owner=self.owner,
            warehouse=self.wh,
            product=self.product,
            location=location,
            qty_base_delta=1,
            lot_no="LOT-B",
            fp=f"print-split-{task.pk}",
            scan_snapshot_rev=0,
        )
        _, picks = build_document(task, "pick")
        self.assertEqual([row["lot_no"] for row in picks], ["LOT-A", "LOT-B"])
        self.assertEqual(len({row["location"].pk for row in picks}), 2)
        result = self.post_review(task)
        self.assertEqual(result.status_code, 200, result.data)
        task.refresh_from_db()
        _, delivery = build_document(task, "delivery")
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery[0].base_qty, Decimal("2.125"))

    def test_auxiliary_quantity_uses_actual_quantity(self):
        from allapp.products.models import ProductPackage, ProductUom

        task = self.posted()
        uom = ProductUom.objects.create(code="PRTBOX", name="箱")
        package = ProductPackage.objects.create(product=self.product, uom=uom, qty_in_base=10)
        origin_id = task.lines.get().src_id
        OutboundOrderLine.objects.filter(pk=origin_id).update(
            aux_uom=package, aux_qty=9, ratio=10, base_qty=90
        )
        _, rows = build_document(task, "delivery")
        self.assertEqual(rows[0].aux_qty, Decimal("0.2125"))
        self.assertEqual(rows[0].base_qty, Decimal("2.125"))

    def test_completed_history_uses_same_read_permission_as_printing(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from allapp.accounts.models import UserRoleScope

        task = self.posted()
        reader = get_user_model().objects.create_user(username="print-reader")
        UserRoleScope.objects.create(user=reader, role="warehouse_manager", warehouse=self.wh)
        reader.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasking", codename="view_wmstask")
        )
        self.assertEqual(self.printed(task, user=reader).status_code, 200)
        self.client.force_authenticate(reader)
        self.assertEqual(self.queue(stage="completed").data["count"], 1)
        self.assertEqual(self.context(task).status_code, 200)
        self.assertEqual(self.queue().status_code, 403)
