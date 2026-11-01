from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory.models import InventoryDetail, PostingJournal
from allapp.locations.models import Warehouse
from allapp.products.models import Brand, Product
from allapp.tasking import test_counting_closure as fixtures
from allapp.tasking.counting import create_count_task, release_count_task
from allapp.tasking.models import CountScopeLock, WmsTask


class CountManagementAPITests(TestCase):
    setUp = fixtures.CountProductionClosureTests.setUp

    def api_client(self, user=None):
        client = APIClient()
        client.force_authenticate(user or self.manager)
        return client

    def create(self, **changes):
        data = {
            "owner_id": self.owner.id,
            "warehouse_id": self.warehouse.id,
            "scope": "ALL",
            **changes,
        }
        return self.api_client().post("/api/pda/count-tasks/", data, format="json")

    def test_draft_survives_reload_and_release_freezes_scope(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        task_id = response.data["id"]
        self.assertFalse(CountScopeLock.objects.filter(released_at__isnull=True).exists())
        listed = self.api_client().get("/api/pda/count-tasks/?stage=draft").data
        self.assertEqual([row["id"] for row in listed], [task_id])
        detail = self.api_client().get(f"/api/pda/count-tasks/{task_id}/").data
        self.assertEqual(detail["scope_payload"]["max_lines"], 1000)
        self.assertEqual(detail["line_count"], 1)
        released = self.api_client().post(f"/api/pda/count-tasks/{task_id}/release/")
        self.assertEqual(released.status_code, 200, released.data)
        self.assertTrue(
            CountScopeLock.objects.filter(active_task_id=task_id, released_at__isnull=True).exists()
        )
        self.assertEqual(self.api_client().get("/api/pda/count-tasks/?stage=draft").data, [])

    def test_posting_stage_includes_failures_but_not_posted_or_unapproved(self):
        ids = []
        for review, posting in [
            ("APPROVED", "PENDING"),
            ("APPROVED", "FAILED"),
            ("APPROVED", "POSTED"),
            ("PENDING", "NOT_READY"),
        ]:
            task_id = self.create().data["id"]
            WmsTask.objects.filter(pk=task_id).update(
                status="COMPLETED", review_status=review, posting_status=posting
            )
            ids.append(task_id)
        result = self.api_client().get("/api/pda/count-tasks/?stage=posting")
        self.assertEqual([row["id"] for row in result.data], [ids[1], ids[0]])
        self.assertEqual(
            self.api_client().get("/api/pda/count-tasks/?stage=bogus").status_code, 400
        )
        # Legacy callers retain their original default list and array response.
        self.assertEqual(
            [row["id"] for row in self.api_client().get("/api/pda/count-tasks/").data], [ids[3]]
        )

    def test_operator_cannot_access_management_options_or_actions(self):
        user = get_user_model().objects.create_user(username="count-worker")
        user.user_permissions.add(Permission.objects.get(codename="claim_task_as_wh_operator"))
        UserRoleScope.objects.create(
            user=user, role=UserRoleScope.Role.WAREHOUSE_OPERATOR, warehouse=self.warehouse
        )
        client = self.api_client(user)
        for stage in ["draft", "posting", "review"]:
            self.assertEqual(client.get(f"/api/pda/count-tasks/?stage={stage}").status_code, 403)
        self.assertEqual(client.get("/api/pda/count-tasks/options/").status_code, 403)
        self.assertEqual(client.post("/api/pda/count-tasks/", {}).status_code, 403)
        self.assertFalse(client.get("/api/auth/profile/").data["capabilities"]["can_manage_count"])

    def test_scoped_manager_options_and_creation_reject_other_warehouse(self):
        user = get_user_model().objects.create_user(username="scoped-count-manager")
        user.user_permissions.add(Permission.objects.get(codename="taskconfirm_as_wh_manager"))
        UserRoleScope.objects.create(
            user=user, role=UserRoleScope.Role.WAREHOUSE_MANAGER, warehouse=self.warehouse
        )
        other = Warehouse.objects.create(code="OTHER-CNT", name="Other")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=other)
        client = self.api_client(user)
        data = client.get("/api/pda/count-tasks/options/").data
        self.assertEqual([row["id"] for row in data["warehouses"]], [self.warehouse.id])
        self.assertTrue(client.get("/api/auth/profile/").data["capabilities"]["can_manage_count"])
        self.assertEqual(
            client.get("/api/pda/count-tasks/options/", {"warehouse_id": other.id}).status_code, 403
        )
        self.assertEqual(
            client.post(
                "/api/pda/count-tasks/", {"owner_id": self.owner.id, "warehouse_id": other.id}
            ).status_code,
            403,
        )
        other_task = self.create().data["id"]
        WmsTask.objects.filter(pk=other_task).update(warehouse=other)
        for action in ["release", "cancel", "approve", "post"]:
            self.assertEqual(
                client.post(f"/api/pda/count-tasks/{other_task}/{action}/").status_code, 404
            )

    def test_product_search_distinguishes_models_and_paginates(self):
        brand = Brand.objects.create(code="COUNT-B", name="盘点品牌")
        self.product.brand = brand
        self.product.model = "型号甲"
        self.product.save(update_fields=["brand", "model"])
        second = Product.objects.create(
            owner=self.owner,
            code="COUNT-SKU2",
            name=self.product.name,
            brand=brand,
            model="型号乙",
            base_uom=self.uom,
        )
        params = {
            "kind": "products",
            "warehouse_id": self.warehouse.id,
            "owner_id": self.owner.id,
            "search": "型号乙",
        }
        result = self.api_client().get("/api/pda/count-tasks/options/", params)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual([row["id"] for row in result.data["results"]], [second.id])
        self.assertIn("型号乙", result.data["results"][0]["product_display_name"])
        params.update(search=self.product.name, page_size=1)
        first = self.api_client().get("/api/pda/count-tasks/options/", params).data
        second_page = (
            self.api_client()
            .get("/api/pda/count-tasks/options/", {**params, "page": first["next"]})
            .data
        )
        self.assertEqual(
            {first["results"][0]["id"], second_page["results"][0]["id"]},
            {self.product.id, second.id},
        )
        self.assertIsNone(second_page["next"])

    def test_invalid_scope_objects_return_validation_errors(self):
        self.assertEqual(self.create(location_id=999999, scope="LOC").status_code, 400)
        self.assertEqual(self.create(subwarehouse_id=999999).status_code, 400)
        self.assertEqual(self.create(product_id=999999, scope="SKU").status_code, 400)
        unbound = Owner.objects.create(code="COUNT-UNB", name="未绑定")
        self.assertEqual(self.create(owner_id=unbound.id).status_code, 400)

    def test_truncated_scope_cannot_release_and_draft_lines_are_unchanged(self):
        product = Product.objects.create(
            owner=self.owner, code="COUNT-EXTRA", name="其他商品", base_uom=self.uom
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=self.location,
            product=product,
            onhand_qty=2,
        )
        response = self.create(max_lines=1)
        self.assertTrue(response.data["truncated"])
        task_id = response.data["id"]
        task = WmsTask.objects.get(pk=task_id)
        old_lines = list(task.lines.values_list("id", flat=True))
        self.assertTrue(
            self.api_client().get(f"/api/pda/count-tasks/{task_id}/").data["scope_truncated"]
        )
        response = self.api_client().post(f"/api/pda/count-tasks/{task_id}/release/")
        self.assertEqual(response.status_code, 400)
        task.refresh_from_db()
        self.assertEqual(task.status, "DRAFT")
        self.assertEqual(list(task.lines.values_list("id", flat=True)), old_lines)
        self.assertFalse(CountScopeLock.objects.filter(released_at__isnull=True).exists())
        self.assertEqual(
            self.api_client().post(f"/api/pda/count-tasks/{task_id}/cancel/").status_code, 200
        )

    @override_settings(COUNT_MAX_TIMES=1)
    def test_review_then_retry_posting_from_list_is_idempotent(self):
        client = self.api_client()
        task_id = self.create().data["id"]
        base = f"/api/pda/count-tasks/{task_id}/"
        self.assertEqual(client.post(base + "release/").status_code, 200)
        line = client.get(base + "lines/").data[0]
        self.assertEqual(
            client.post(
                base + "record/",
                {"line_id": line["id"], "qty_counted": "4", "client_seq": "pda-count-1"},
            ).status_code,
            200,
        )
        self.assertEqual(client.post(base + "submit/").status_code, 200)
        self.assertEqual(client.post(base + "approve/").status_code, 200)
        client.raise_request_exception = False
        with patch(
            "allapp.inventory.services.post_task",
            side_effect=RuntimeError("simulated posting failure"),
        ):
            self.assertEqual(client.post(base + "post/").status_code, 500)
        pending = client.get("/api/pda/count-tasks/?stage=posting").data
        self.assertEqual(pending[0]["posting_status"], "FAILED")
        self.assertEqual(client.post(base + "post/").status_code, 200)
        journal_count = PostingJournal.objects.count()
        self.assertEqual(client.post(base + "post/").status_code, 200)
        self.assertEqual(PostingJournal.objects.count(), journal_count)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 4)
        self.assertEqual(client.get("/api/pda/count-tasks/?stage=posting").data, [])


class CountManagementConcurrencyTests(TransactionTestCase):
    setUp = fixtures.CountProductionClosureTests.setUp

    def test_concurrent_overlapping_release_only_one_succeeds(self):
        tasks = [
            create_count_task(
                created_by=self.manager, owner_id=self.owner.id, warehouse_id=self.warehouse.id
            )[0]
            for _ in range(2)
        ]
        barrier = Barrier(2)

        def release(task):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.manager.pk)
                barrier.wait(timeout=10)
                release_count_task(task.pk, by_user=user)
                return "released"
            except Exception as exc:
                from django.core.exceptions import ValidationError

                if isinstance(exc, ValidationError):
                    return "conflict"
                raise
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(release, tasks))
        self.assertCountEqual(outcomes, ["released", "conflict"])
        self.assertEqual(
            WmsTask.objects.filter(pk__in=[task.pk for task in tasks], status="RELEASED").count(), 1
        )
        self.assertEqual(CountScopeLock.objects.filter(released_at__isnull=True).count(), 1)
