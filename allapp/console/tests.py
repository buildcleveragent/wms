from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, Supplier
from allapp.core.choices import InvTxType
from allapp.inbound.constants import (
    PDA_NO_ORDER_RECEIVE_NOTE,
    PDA_NO_ORDER_RECEIVE_SOURCE_APP,
    PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
)
from allapp.inbound.models import InboundOrder
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.pos.models import PosPayment, PosPaymentLine, PosSale, PosSaleLine
from allapp.products.identifier_services import add_product_barcode
from allapp.products.models import (
    Brand,
    Product,
    ProductBarcode,
    ProductCategory,
    ProductUom,
)
from allapp.salesapp.models import SaleProductConfig
from allapp.tasking.models import WmsTask, WmsTaskLine


class DashboardSummaryConsoleTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="DSH", name="仪表盘货主")
        self.warehouse = Warehouse.objects.create(code="DSHWH", name="仪表盘仓")
        self.user = get_user_model().objects.create_user(
            username="dashboard-user",
            password="pw",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="DSH-PICK-1",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.PENDING,
        )
        self.url = reverse("console:dashboard_summary")
        self.today = timezone.now().date()

    def test_dashboard_summary_requires_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_homepage_contains_only_real_dashboard_content(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard_home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console/home.html")
        self.assertContains(response, "仓库运营看板")
        self.assertContains(response, "最近30天")
        self.assertContains(response, "无订单收货")
        self.assertContains(response, "POS 收银")
        self.assertNotContains(response, "数据为示例")
        self.assertNotContains(response, "99.99%")
        self.assertNotContains(response, "SKU-A2387")
        self.assertNotContains(response, "98.5%")

    def test_tasks_use_30_day_cohort_and_enforce_scope(self):
        other_owner = Owner.objects.create(code="DSH-OTHER", name="其他货主")
        other_warehouse = Warehouse.objects.create(
            code="DSHWH2",
            name="其他仓库",
        )
        WmsTask.objects.create(
            owner=other_owner,
            warehouse=other_warehouse,
            task_no="DSH-PICK-OUT-OF-SCOPE",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.READY,
        )
        WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="DSH-PICK-CANCELLED",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.CANCELLED,
        )
        old_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="DSH-PICK-OLD",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.READY,
        )
        WmsTask.objects.filter(pk=old_task.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertIn("kpi", data)
        self.assertIn("outbound_ts", data)
        self.assertIn("eff_putaway", data)
        self.assertIn("eff_pick", data)
        self.assertIn("data_as_of", data)
        self.assertEqual(data["range"]["days"], 30)
        self.assertEqual(len(data["inbound_ts"]["dates"]), 30)
        self.assertEqual(data["kpi"]["pick"]["total"], 1)
        self.assertEqual(data["kpi"]["pick"]["done"], 1)
        self.assertFalse(data["no_order_receive"]["available"])
        self.assertFalse(data["pos"]["available"])

    def _make_product_and_location(self, *, suffix=""):
        code_suffix = suffix.replace("-", "")
        uom = ProductUom.objects.create(code=f"DSH-EA{suffix}", name=f"件{suffix}")
        product = Product.objects.create(
            owner=self.owner,
            code=f"DSH-PRODUCT{suffix}",
            sku=f"DSH-SKU{suffix}",
            name=f"仪表盘商品{suffix}",
            base_uom=uom,
        )
        subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code=f"DSHSW{code_suffix}",
            name=f"仪表盘子仓{suffix}",
        )
        location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=subwarehouse,
            code=f"{subwarehouse.code}-01-01-01",
            name=f"仪表盘库位{suffix}",
        )
        return product, location

    def _grant_dashboard_module_permissions(self):
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="reports",
                codename="view_warehouse_operations",
            ),
            Permission.objects.get(
                content_type__app_label="pos",
                codename="view_possale",
            ),
        )

    def test_no_order_receive_uses_posted_actuals_and_legacy_marker(self):
        self._grant_dashboard_module_permissions()
        product, location = self._make_product_and_location(suffix="-NOR")

        def make_receive(task_no, qty, **task_fields):
            task_status = task_fields.pop("status", WmsTask.Status.COMPLETED)
            posting_status = task_fields.pop(
                "posting_status", WmsTask.PostingStatus.POSTED
            )
            if task_status == WmsTask.Status.CANCELLED:
                review_status = WmsTask.ReviewStatus.NONE
                posting_status = WmsTask.PostingStatus.NONE
            elif posting_status == WmsTask.PostingStatus.POSTED:
                review_status = WmsTask.ReviewStatus.APPROVED
            else:
                review_status = WmsTask.ReviewStatus.PENDING
            task = WmsTask.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                task_no=task_no,
                task_type=WmsTask.TaskType.RECEIVE,
                status=task_status,
                review_status=review_status,
                posting_status=posting_status,
                posted_at=(
                    timezone.now()
                    if posting_status == WmsTask.PostingStatus.POSTED
                    else None
                ),
                created_by=self.user,
                **task_fields,
            )
            InventoryTransaction.objects.create(
                tx_type=InvTxType.RECEIVE,
                owner=self.owner,
                product=product,
                warehouse=self.warehouse,
                location=location,
                qty_delta=Decimal(qty),
                src_model="WmsTask",
                src_id=task.id,
                src_line_id=task.id,
                src_no=task.task_no,
                posted_at=timezone.now(),
            )
            return task

        make_receive(
            "DSH-NOR-CURRENT",
            "3.000",
            source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
            source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
        )
        make_receive(
            "DSH-NOR-LEGACY",
            "2.500",
            posting_note=PDA_NO_ORDER_RECEIVE_NOTE,
        )
        make_receive(
            "DSH-NOR-ORDERED",
            "9.000",
            source_app="inbound",
            source_model="InboundOrder",
        )
        make_receive(
            "DSH-NOR-CANCELLED",
            "7.000",
            status=WmsTask.Status.CANCELLED,
            source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
            source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
        )
        make_receive(
            "DSH-NOR-UNPOSTED",
            "8.000",
            posting_status=WmsTask.PostingStatus.NOT_READY,
            source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
            source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
        )
        self.client.force_login(self.user)

        payload = self.client.get(self.url).json()["data"]["no_order_receive"]

        self.assertTrue(payload["available"])
        self.assertEqual(payload["summary"], {"orders": 2, "lines": 2, "qty": "5.500"})
        self.assertEqual(payload["trend"]["qty"][-1], "5.500")
        self.assertEqual(len(payload["trend"]["dates"]), 30)

    def test_pos_dashboard_uses_scoped_existing_pos_aggregation(self):
        self._grant_dashboard_module_permissions()
        product, _ = self._make_product_and_location(suffix="-POS")
        sale = PosSale.objects.create(
            sale_no="DSH-POS-OWN",
            warehouse=self.warehouse,
            cashier=self.user,
            status=PosSale.Status.COMPLETED,
            total_amount=Decimal("12.00"),
        )
        PosSaleLine.objects.create(
            sale=sale,
            owner=self.owner,
            product=product,
            line_no=1,
            qty=Decimal("2.000"),
            price=Decimal("6.0000"),
            amount=Decimal("12.00"),
        )
        PosPayment.objects.create(
            sale=sale,
            method=PosPayment.Method.CASH,
            amount_due=Decimal("12.00"),
            amount_received=Decimal("12.00"),
        )
        PosPaymentLine.objects.create(
            sale=sale,
            method=PosPayment.Method.CASH,
            amount=Decimal("12.00"),
            amount_received=Decimal("12.00"),
        )

        other_warehouse = Warehouse.objects.create(code="DSHPOSO", name="其他POS仓")
        other_sale = PosSale.objects.create(
            sale_no="DSH-POS-OTHER",
            warehouse=other_warehouse,
            cashier=self.user,
            status=PosSale.Status.COMPLETED,
            total_amount=Decimal("99.00"),
        )
        PosSaleLine.objects.create(
            sale=other_sale,
            owner=self.owner,
            product=product,
            line_no=1,
            qty=Decimal("1.000"),
            price=Decimal("99.0000"),
            amount=Decimal("99.00"),
        )
        self.client.force_login(self.user)

        payload = self.client.get(self.url).json()["data"]["pos"]

        self.assertTrue(payload["available"])
        self.assertEqual(payload["today"]["summary"]["completed_count"], 1)
        self.assertEqual(payload["today"]["summary"]["net_amount"], "12.00")
        self.assertEqual(payload["today"]["summary"]["received_amount"], "12.00")
        self.assertEqual(payload["trend_30d"]["net_amount"][-1], "12.00")
        self.assertEqual(
            payload["today"]["cashiers"][0]["cashier_username"], self.user.username
        )

    def test_orders_use_biz_date_close_flag_status_labels_and_scope(self):
        supplier = Supplier.objects.create(
            owner=self.owner,
            code="DSH-SUP",
            name="仪表盘供应商",
        )
        customer = Customer.objects.create(
            owner=self.owner,
            code="DSH-CUST",
            name="仪表盘客户",
            salesperson=self.user,
        )
        InboundOrder.objects.create(
            owner=self.owner,
            supplier=supplier,
            warehouse=self.warehouse,
            order_no="DSH-IN-OPEN",
            biz_date=self.today,
            approval_status="WHS_PENDING",
        )
        InboundOrder.objects.create(
            owner=self.owner,
            supplier=supplier,
            warehouse=self.warehouse,
            order_no="DSH-IN-CLOSED",
            biz_date=self.today,
            approval_status="WHS_APPROVED",
            is_closed=True,
            close_reason="已收货",
        )
        InboundOrder.objects.create(
            owner=self.owner,
            supplier=supplier,
            warehouse=self.warehouse,
            order_no="DSH-IN-CANCELLED",
            biz_date=self.today,
            approval_status="CANCELLED",
        )
        OutboundOrder.objects.create(
            owner=self.owner,
            customer=customer,
            warehouse=self.warehouse,
            order_no="DSH-OUT-OPEN",
            biz_date=self.today,
            approval_status="OWNER_PENDING",
        )
        OutboundOrder.objects.create(
            owner=self.owner,
            customer=customer,
            warehouse=self.warehouse,
            order_no="DSH-OUT-CLOSED",
            biz_date=self.today,
            approval_status="WHS_APPROVED",
            is_closed=True,
            close_reason="已发货",
        )
        OutboundOrder.objects.create(
            owner=self.owner,
            customer=customer,
            warehouse=self.warehouse,
            order_no="DSH-OUT-CANCELLED",
            biz_date=self.today,
            approval_status="CANCELLED",
        )

        other_owner = Owner.objects.create(code="DSH-ORD-O", name="订单越权货主")
        other_warehouse = Warehouse.objects.create(code="DSHORDW", name="订单越权仓")
        other_supplier = Supplier.objects.create(
            owner=other_owner,
            code="DSH-OTHER-SUP",
            name="订单越权供应商",
        )
        InboundOrder.objects.create(
            owner=other_owner,
            supplier=other_supplier,
            warehouse=other_warehouse,
            order_no="DSH-IN-OUT-OF-SCOPE",
            biz_date=self.today,
            approval_status="WHS_PENDING",
        )
        self.client.force_login(self.user)

        data = self.client.get(self.url).json()["data"]

        self.assertEqual(data["inbound_ts"]["total"][-1], 2)
        self.assertEqual(data["inbound_ts"]["finished"][-1], 1)
        self.assertEqual(data["outbound_ts"]["total"][-1], 2)
        self.assertEqual(data["outbound_ts"]["finished"][-1], 1)
        self.assertEqual(data["inbound_backlog"]["statuses"], ["WHS_PENDING"])
        self.assertEqual(data["inbound_backlog"]["labels"], ["待仓库管理员确认"])
        self.assertEqual(data["inbound_backlog"]["values"], [1])
        self.assertEqual(data["outbound_backlog"]["statuses"], ["OWNER_PENDING"])
        self.assertEqual(data["outbound_backlog"]["labels"], ["待货主管理员审核"])
        self.assertEqual(data["outbound_backlog"]["values"], [1])

    def test_efficiency_uses_finished_by_enforces_scope_and_limits_top_ten(self):
        creator = get_user_model().objects.create_user(
            username="dashboard-task-creator",
            password="pw",
        )
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="DSH-EFF-PICK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.PENDING,
            created_by=creator,
        )
        workers = [
            get_user_model().objects.create_user(
                username=f"dashboard-worker-{index:02d}",
                password="pw",
            )
            for index in range(11)
        ]
        for worker in workers:
            WmsTaskLine.objects.create(
                task=task,
                qty_plan=Decimal("1.000"),
                qty_done=Decimal("1.000"),
                status=WmsTaskLine.Status.COMPLETED,
                finished_at=timezone.now(),
                finished_by=worker,
            )
        WmsTaskLine.objects.create(
            task=task,
            qty_plan=Decimal("1.000"),
            qty_done=Decimal("1.000"),
            status=WmsTaskLine.Status.COMPLETED,
            finished_at=timezone.now(),
            finished_by=workers[0],
        )
        WmsTaskLine.objects.create(
            task=task,
            qty_plan=Decimal("1.000"),
            status=WmsTaskLine.Status.IN_PROGRESS,
            finished_at=timezone.now(),
            finished_by=workers[0],
        )

        other_owner = Owner.objects.create(code="DSH-EFF-O", name="人效越权货主")
        other_warehouse = Warehouse.objects.create(code="DSHEFFW", name="人效越权仓")
        other_task = WmsTask.objects.create(
            owner=other_owner,
            warehouse=other_warehouse,
            task_no="DSH-EFF-OUT-OF-SCOPE",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.PENDING,
        )
        WmsTaskLine.objects.create(
            task=other_task,
            qty_plan=Decimal("1.000"),
            qty_done=Decimal("1.000"),
            status=WmsTaskLine.Status.COMPLETED,
            finished_at=timezone.now(),
            finished_by=creator,
        )
        self.client.force_login(self.user)

        ranking = self.client.get(self.url).json()["data"]["eff_pick"]

        self.assertEqual(len(ranking["labels"]), 10)
        self.assertEqual(ranking["labels"][0], workers[0].username)
        self.assertEqual(ranking["values"][0], 2)
        self.assertNotIn(creator.username, ranking["labels"])
        self.assertEqual(ranking["unit"], "完成任务行数")

    def test_empty_scope_returns_zeroes_and_empty_collections(self):
        empty_warehouse = Warehouse.objects.create(code="DSHEMPTY", name="空数据仓")
        empty_user = get_user_model().objects.create_user(
            username="dashboard-empty-user",
            password="pw",
        )
        UserRoleScope.objects.create(
            user=empty_user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=empty_warehouse,
        )
        self.client.force_login(empty_user)

        data = self.client.get(self.url).json()["data"]

        self.assertEqual(data["kpi"]["putaway"], {"total": 0, "done": 0})
        self.assertEqual(data["kpi"]["pick"], {"total": 0, "done": 0})
        self.assertEqual(data["inbound_ts"]["total"], [0] * 30)
        self.assertEqual(data["outbound_backlog"]["labels"], [])
        self.assertEqual(data["eff_pack"]["labels"], [])
        self.assertEqual(data["no_order_receive"]["reason"], "permission_denied")
        self.assertEqual(data["no_order_receive"]["trend"]["dates"], [])
        self.assertEqual(data["pos"]["reason"], "permission_denied")
        self.assertEqual(data["pos"]["today"]["cashiers"], [])

    def test_legacy_user_fields_do_not_authorize_dashboard(self):
        legacy_user = get_user_model().objects.create_user(
            username="legacy-dashboard-user",
            password="pw",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.client.force_login(legacy_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)


class OperationConsoleScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="OP-SCOPE", name="作业台货主")
        self.warehouse = Warehouse.objects.create(code="OPSCOPEWH", name="授权仓")
        self.other_warehouse = Warehouse.objects.create(
            code="OPSCOPEOT",
            name="未授权仓",
        )
        self.user = get_user_model().objects.create_user(
            username="op-scope-user",
            password="pw",
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking",
                codename="claim_task_as_wh_operator",
            )
        )
        self.task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            task_no="OP-SCOPE-OUT-1",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
        )
        self.line = self.task.lines.create(
            qty_plan=Decimal("2.000"),
            status=WmsTask.Status.RELEASED,
        )
        self.client.force_login(self.user)

    def test_direct_mutation_urls_hide_out_of_scope_task_and_line(self):
        scan_response = self.client.post(
            reverse("op:scan", args=[self.task.id]),
            {"payload": f"L{self.line.id};Q1"},
        )
        line_response = self.client.post(
            reverse("op:line_detail", args=[self.line.id]),
            {"line-qty_done": "1.000", "line-remark": "cross warehouse"},
        )
        with mock.patch(
            "allapp.console.views_op.tasking_services._run_posting_handler"
        ) as mocked_post:
            post_response = self.client.post(
                reverse("op:post", args=[self.task.id]),
                {"confirm": "on"},
            )

        self.assertEqual(scan_response.status_code, 404)
        self.assertEqual(line_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)
        mocked_post.assert_not_called()


class InventoryTransactionConsoleSearchTests(TestCase):
    def setUp(self):
        from allapp.console.views import _filtered_qs

        self.filtered_qs = _filtered_qs
        self.owner = Owner.objects.create(code="TXSEARCH", name="流水搜索货主")
        self.uom = ProductUom.objects.create(code="TXSEARCH-EA", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="TX-PRODUCT",
            sku="TX-WMS-SKU",
            name="流水搜索商品",
            base_uom=self.uom,
        )
        add_product_barcode(
            product=self.product,
            barcode="TX-HISTORICAL-BARCODE",
            barcode_type=ProductBarcode.BarcodeType.OTHER,
        )
        self.warehouse = Warehouse.objects.create(code="TXWH", name="流水授权仓")
        subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="TXSW",
            name="流水子仓",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=subwarehouse,
            code="TXSW-01-01-01",
            name="流水库位",
        )
        self.transaction = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=self.product,
            location=self.location,
            qty_delta=Decimal("1.0000"),
            src_model="console.tests",
            src_id=1,
            src_line_id=1,
            src_no="TX-SEARCH-1",
        )
        self.user = get_user_model().objects.create_user(
            username="tx-search-user",
            password="pw",
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )

    def test_search_uses_identifier_history_and_enforces_scope(self):
        request = RequestFactory().get("/console/inventory/", {"q": "historical-bar"})
        request.user = self.user

        queryset, _ = self.filtered_qs(request)

        self.assertEqual(list(queryset), [self.transaction])

        other_warehouse = Warehouse.objects.create(code="TXOTHER", name="未授权仓")
        other_subwarehouse = Subwarehouse.objects.create(
            warehouse=other_warehouse,
            code="TXOTHERSW",
            name="未授权子仓",
        )
        other_location = Location.objects.create(
            warehouse=other_warehouse,
            subwarehouse=other_subwarehouse,
            code="TXOTHERSW-01-01-01",
            name="未授权库位",
        )
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=self.product,
            location=other_location,
            qty_delta=Decimal("1.0000"),
            src_model="console.tests",
            src_id=2,
            src_line_id=1,
            src_no="TX-SEARCH-2",
        )

        queryset, _ = self.filtered_qs(request)
        self.assertEqual(list(queryset), [self.transaction])


class SaleMiniProductListingConsoleTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="SLC", name="商城运营货主")
        self.category = ProductCategory.objects.create(code="SLC-CAT", name="饮品")
        self.brand = Brand.objects.create(code="SLC-BRAND", name="测试品牌")
        self.uom = ProductUom.objects.create(code="SLC-EA", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="SLC001",
            sku="SLC001",
            name="可上架商品",
            category=self.category,
            brand=self.brand,
            base_uom=self.uom,
            price=Decimal("12.50"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.no_price_product = Product.objects.create(
            owner=self.owner,
            code="SLC002",
            sku="SLC002",
            name="缺价格商品",
            category=self.category,
            brand=self.brand,
            base_uom=self.uom,
            price=None,
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.warehouse = Warehouse.objects.create(code="SLCWH", name="商城运营仓")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SLCSW",
            name="商城运营子仓",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SLCSW-01-01-01",
            name="商城运营库位",
        )
        self.inventory = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("7.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        self.user = get_user_model().objects.create_superuser(
            username="catalog-admin",
            email="catalog@example.com",
            password="pw",
        )
        self.client.force_login(self.user)
        self.url = reverse("console:sale_mini_product_listing")

    def test_listing_page_shows_unconfigured_products_and_filters(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "可上架商品")
        self.assertContains(response, "缺价格商品")
        self.assertContains(response, "未创建商城配置")

        stock_response = self.client.get(self.url, {"stock": "in"})
        self.assertContains(stock_response, "可上架商品")
        self.assertNotContains(stock_response, "缺价格商品")

        missing_price_response = self.client.get(self.url, {"price": "missing"})
        self.assertContains(missing_price_response, "缺价格商品")
        self.assertNotContains(missing_price_response, "可上架商品")

    def test_bulk_list_creates_config_and_public_product_without_changing_inventory(
        self,
    ):
        before_available = InventoryDetail.objects.get(
            pk=self.inventory.pk
        ).available_qty

        response = self.client.post(
            self.url,
            {
                "bulk_action": "list",
                "product_ids": [str(self.product.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product)
        self.assertTrue(config.is_listed)
        self.assertTrue(config.is_active)
        self.assertEqual(config.sale_price, Decimal("12.50"))
        self.assertEqual(
            InventoryDetail.objects.get(pk=self.inventory.pk).available_qty,
            before_available,
        )

        public_response = self.client.get("/api/sale-mini/products/")
        self.assertEqual(public_response.status_code, 200)
        rows = public_response.json()["results"]
        self.assertEqual({row["id"] for row in rows}, {self.product.id})

    def test_bulk_list_rejects_missing_price_and_keeps_product_unlisted(self):
        response = self.client.post(
            self.url,
            {
                "bulk_action": "list",
                "product_ids": [str(self.no_price_product.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未设置商品价格")
        config = SaleProductConfig.objects.get(
            owner=self.owner,
            product=self.no_price_product,
        )
        self.assertFalse(config.is_listed)

        public_response = self.client.get("/api/sale-mini/products/")
        self.assertEqual(public_response.json()["results"], [])

    def test_bulk_price_and_badge_updates_only_sale_product_config(self):
        SaleProductConfig.objects.create(
            owner=self.owner,
            product=self.product,
            sale_price=Decimal("12.5000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
        )
        before_available = InventoryDetail.objects.get(
            pk=self.inventory.pk
        ).available_qty

        self.client.post(
            self.url,
            {
                "bulk_action": "set_sale_price",
                "product_ids": [str(self.product.id)],
                "sale_price": "8.88",
            },
        )
        self.client.post(
            self.url,
            {
                "bulk_action": "set_badges",
                "product_ids": [str(self.product.id)],
                "is_recommended": "1",
                "is_hot": "1",
            },
        )

        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product)
        self.assertEqual(config.sale_price, Decimal("8.88"))
        self.assertTrue(config.is_recommended)
        self.assertTrue(config.is_hot)
        self.assertFalse(config.is_new)
        self.assertEqual(
            InventoryDetail.objects.get(pk=self.inventory.pk).available_qty,
            before_available,
        )

    def test_bulk_quantity_rules_require_explicit_enable_switch(self):
        config = SaleProductConfig.objects.create(
            owner=self.owner,
            product=self.product,
            sale_price=Decimal("12.5000"),
            min_order_qty=Decimal("2.000"),
            multiple_qty=Decimal("3.000"),
        )

        self.client.post(
            self.url,
            {
                "bulk_action": "set_rules",
                "product_ids": [str(self.product.id)],
                "min_order_qty": "2.000",
                "multiple_qty": "3.000",
                "max_order_qty": "",
            },
        )
        config.refresh_from_db()
        self.assertFalse(config.enable_qty_rules)

        self.client.post(
            self.url,
            {
                "bulk_action": "set_rules",
                "product_ids": [str(self.product.id)],
                "enable_qty_rules": "1",
                "min_order_qty": "2.000",
                "multiple_qty": "3.000",
                "max_order_qty": "20.000",
            },
        )
        config.refresh_from_db()
        self.assertTrue(config.enable_qty_rules)
        self.assertEqual(config.min_order_qty, Decimal("2.000"))
        self.assertEqual(config.multiple_qty, Decimal("3.000"))
        self.assertEqual(config.max_order_qty, Decimal("20.000"))
