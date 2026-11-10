"""The admin must show fulfilment facts independently of order approval."""

from django.contrib import admin
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from allapp.baseinfo.models import Owner
from allapp.tasking.models import WmsTask

from . import test_order_progress as fixtures
from .admin import OutboundOrderAdmin
from .models import OutboundOrder


class OutboundAdminProgressTests(TestCase):
    setUpTestData = classmethod(fixtures.OrderProgressTests.setUpTestData.__func__)
    task = fixtures.OrderProgressTests.task
    set_order = fixtures.OrderProgressTests.set_order

    def setUp(self):
        fixtures.OrderProgressTests.setUp(self)
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.model_admin = OutboundOrderAdmin(OutboundOrder, admin.site)

    def changelist(self, **params):
        request = RequestFactory().get("/admin/outbound/outboundorder/", params)
        request.user = self.user
        return self.model_admin.get_changelist_instance(request)

    def status(self):
        order = next(o for o in self.changelist().result_list if o.pk == self.order.pk)
        return self.model_admin.fulfilment_status_display(order)

    def test_rendered_list_separates_approval_from_completed_review(self):
        pick = self.task("PICK", "COMPLETED", 10)
        self.task("REVIEW", "COMPLETED", 10, parent=pick)
        self.client.force_login(self.user)
        response = self.client.get("/admin/outbound/outboundorder/")
        self.assertContains(response, "仓库管理员已确认")
        self.assertContains(response, "出库进度")
        self.assertContains(response, "已复核／待发运")
        self.assertNotContains(response, "仓库管理员已确认,待拣货")
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_status, "WHS_APPROVED")
        self.assertFalse(self.order.is_closed)

    def test_live_pick_review_and_shipment_progress(self):
        pick = self.task()
        self.assertEqual(self.status(), "待拣货")
        WmsTask.objects.filter(pk=pick.pk).update(status="IN_PROGRESS")
        self.assertEqual(self.status(), "拣货中")
        WmsTask.objects.filter(pk=pick.pk).update(status="COMPLETED", review_status="PENDING")
        self.assertEqual(self.status(), "待复核")
        review = self.task("REVIEW", "IN_PROGRESS", parent=pick)
        self.assertEqual(self.status(), "复核中")
        WmsTask.objects.filter(pk=review.pk).update(
            status="COMPLETED", review_status="APPROVED", posting_status="POSTED"
        )
        self.assertEqual(self.status(), "已复核／待发运")
        dispatch = self.task("DISPATCH", "COMPLETED", 4, parent=review)
        self.assertEqual(self.status(), "部分发运")
        dispatch.lines.update(qty_done=10)
        self.set_order(is_closed=True, close_reason="全部发运完成")
        self.assertEqual(self.status(), "已发运")

    def test_legacy_assisted_completion_and_cancelled_order(self):
        self.set_order(processing_mode="WAREHOUSE_ASSISTED", is_closed=True)
        self.task("PICK", "COMPLETED", 10)
        self.assertEqual(self.status(), "代办出库完成")
        self.set_order(approval_status="CANCELLED", is_closed=False)
        self.assertEqual(self.status(), "已取消")

    def test_failed_or_unapproved_review_is_not_complete(self):
        pick = self.task("PICK", "COMPLETED", 10)
        review = self.task("REVIEW", "COMPLETED", 10, parent=pick)
        WmsTask.objects.filter(pk=review.pk).update(
            review_status="PENDING", posting_status="NOT_READY"
        )
        self.assertEqual(self.status(), "复核中")
        WmsTask.objects.filter(pk=review.pk).update(review_status="REJECTED")
        self.assertEqual(self.status(), "履约异常")
        WmsTask.objects.filter(pk=review.pk).update(
            review_status="APPROVED", posting_status="POSTED"
        )
        WmsTask.objects.filter(pk=pick.pk).update(posting_status="FAILED")
        self.assertEqual(self.status(), "履约异常")

    def test_deleted_cancelled_and_foreign_tasks_do_not_advance_progress(self):
        self.task("REVIEW", "CANCELLED", 10)
        deleted = self.task("REVIEW", "COMPLETED", 10)
        WmsTask.objects.filter(pk=deleted.pk).update(is_deleted=True)
        foreign = self.task("REVIEW", "COMPLETED", 10)
        owner = Owner.objects.create(code="ADM-OTHER", name="其他货主")
        WmsTask.objects.filter(pk=foreign.pk).update(owner=owner)
        self.assertEqual(self.status(), "待拣货")

    def test_approval_filter_and_owner_scope_are_preserved(self):
        self.task("REVIEW", "COMPLETED", 10)
        self.assertEqual(
            len(self.changelist(approval_status__exact="OWNER_PENDING").result_list), 0
        )
        self.assertEqual(len(self.changelist(approval_status__exact="WHS_APPROVED").result_list), 1)
        self.user.is_superuser = False
        request = RequestFactory().get("/admin/outbound/outboundorder/")
        request.user = self.user
        foreign = Owner.objects.create(code="ADM-SCOPE", name="其他货主")
        self.set_order(owner=foreign)
        self.assertFalse(self.model_admin.get_queryset(request).exists())

    def test_progress_is_batch_loaded_only_for_the_current_page(self):
        self.task("REVIEW", "COMPLETED", 10)
        with CaptureQueriesContext(connection) as one:
            self.changelist()
        for _ in range(4):
            fixtures.OrderProgressTests.setUp(self)
            self.task("REVIEW", "COMPLETED", 10)
        with CaptureQueriesContext(connection) as many:
            changelist = self.changelist()
        self.assertEqual(len(one), len(many))
        with self.assertNumQueries(0):
            for order in changelist.result_list:
                self.assertEqual(
                    self.model_admin.fulfilment_status_display(order), "已复核／待发运"
                )
        self.model_admin.list_per_page = 2
        page = self.changelist()
        self.assertEqual(len(page.result_list), 2)
        self.assertEqual(page.result_count, 5)
