import itertools
import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from allapp.baseinfo.models import Customer, Owner
from allapp.core.models import DocSequence
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from allapp.tasking.posting_exec import execute_posting_handler

from .models import PosReturn, PosSale, PosShift
from .services import create_pos_return, create_pos_sale, void_pos_sale


class PosCheckoutConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="PCONC", name="POS concurrent owner")
        self.warehouse = Warehouse.objects.create(code="PCWH", name="POS concurrent WH")
        Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="PCWH",
            name="POS concurrent subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="PCWH-01-01-01",
            name="POS concurrent location",
        )
        self.uom = ProductUom.objects.create(code="PCEA", name="EA", is_active=True)
        self.products = [
            self._create_product("PCA", "POS concurrent A", Decimal("10.0000")),
            self._create_product("PCB", "POS concurrent B", Decimal("5.0000")),
        ]
        self.users = []
        for index in (1, 2):
            user = get_user_model().objects.create_user(
                username=f"pos-concurrent-{index}",
                password="x",
                owner=self.owner,
                warehouse=self.warehouse,
            )
            PosShift.objects.create(
                shift_no=f"PCSHIFT-{index}",
                warehouse=self.warehouse,
                cashier=user,
                opened_by=user,
                opened_at=timezone.now(),
            )
            self.users.append(user)
        Customer.objects.create(
            owner=self.owner,
            salesperson=self.users[0],
            code="CASH",
            name="Cash customer",
        )
        self._sequence = itertools.count(1)
        self._sequence_lock = threading.Lock()

    def _create_product(self, code, name, onhand):
        product = Product.objects.create(
            owner=self.owner,
            code=code,
            sku=code,
            name=name,
            base_uom=self.uom,
            price=Decimal("9.00"),
            min_price=Decimal("1.00"),
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=onhand,
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        return product

    def _run_actions(self, actions):
        start = threading.Barrier(len(actions))
        results = [None] * len(actions)
        errors = [None] * len(actions)

        def next_value(prefix):
            with self._sequence_lock:
                return f"{prefix}-{next(self._sequence):08d}"

        def next_code(*, doc_type, **kwargs):
            return next_value(doc_type)

        def invoke(index):
            close_old_connections()
            try:
                start.wait(timeout=10)
                results[index] = actions[index]()
            except BaseException as exc:
                errors[index] = exc
            finally:
                close_old_connections()

        with mock.patch.object(
            DocSequence, "next_code", side_effect=next_code
        ), mock.patch(
            "allapp.pos.services._make_sale_no",
            side_effect=lambda *args, **kwargs: next_value("PSALE"),
        ):
            threads = [
                threading.Thread(target=invoke, args=(index,))
                for index in range(len(actions))
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        if any(thread.is_alive() for thread in threads):
            self.fail("concurrent POS actions did not finish")
        return results, errors

    def _run_checkouts(self, payloads):
        actions = [
            lambda index=index: create_pos_sale(
                user=self.users[index],
                src_bill_no=payloads[index]["src_bill_no"],
                items=payloads[index]["items"],
                payment=payloads[index]["payment"],
            )
            for index in range(len(payloads))
        ]
        return self._run_actions(actions)

    def _create_ordinary_pick(self, *, product, qty):
        now = timezone.now()
        task = WmsTask.objects.create(
            task_no=f"ORD-PICK-{product.id}-{qty}",
            task_type=WmsTask.TaskType.PICK,
            owner=self.owner,
            warehouse=self.warehouse,
            ref_no=f"ORD-PICK-{product.id}",
            source_app="outbound",
            source_model="outboundorder",
            source_pk="999999",
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
            released_at=now,
            started_at=now,
            finished_at=now,
            approved_by=self.users[0],
            approved_at=now,
            created_by=self.users[0],
        )
        line = WmsTaskLine.objects.create(
            task=task,
            product=product,
            from_location=self.location,
            qty_plan=qty,
            qty_done=qty,
            status=WmsTaskLine.Status.COMPLETED,
            started_at=now,
            finished_at=now,
            finished_by=self.users[0],
            scan_snapshot_rev=0,
        )
        TaskScanLog.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task=task,
            task_line=line,
            product=product,
            location=self.location,
            label_key=None,
            method=TaskScanLog.Method.API,
            source="API",
            by_user=self.users[0],
            qty_base_delta=qty,
            status=TaskScanLog.ScanStatus.OK,
            review_status=TaskScanLog.ReviewStatus.APPROVED,
            reviewed_by=self.users[0],
            reviewed_at=now,
            fp=f"ORD-PICK-{task.id}-{line.id}",
            scan_snapshot_rev=0,
        )
        return task

    def _create_initial_sale(self, *, src_bill_no):
        create_pos_sale(
            user=self.users[0],
            src_bill_no=src_bill_no,
            items=[
                {
                    "product_id": self.products[0].id,
                    "qty": "4.000",
                    "price": "9.0000",
                }
            ],
            payment={"method": "CASH", "amount_received": "36.00"},
        )
        return PosSale.objects.get(src_bill_no=src_bill_no)

    def _checkout_remaining_six(self, src_bill_no):
        return create_pos_sale(
            user=self.users[1],
            src_bill_no=src_bill_no,
            items=[
                {
                    "product_id": self.products[0].id,
                    "qty": "6.000",
                    "price": "9.0000",
                }
            ],
            payment={"method": "CASH", "amount_received": "54.00"},
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_same_sku_concurrent_checkout_cannot_oversell(self):
        product = self.products[0]
        payloads = [
            {
                "src_bill_no": f"POS-CONC-OVER-{index}",
                "payment": {"method": "CASH", "amount_received": "54.00"},
                "items": [
                    {
                        "product_id": product.id,
                        "qty": "6.000",
                        "price": "9.0000",
                    }
                ],
            }
            for index in (1, 2)
        ]

        results, errors = self._run_checkouts(payloads)

        self.assertEqual(sum(result is not None for result in results), 1)
        failures = [error for error in errors if error is not None]
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        detail = InventoryDetail.objects.get(product=product)
        self.assertEqual(detail.onhand_qty, Decimal("4.0000"))
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                product=product,
                src_model="WmsTask",
            ).count(),
            1,
        )
        self.assertEqual(
            PosSale.objects.filter(src_bill_no__startswith="POS-CONC-OVER-").count(),
            1,
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_reverse_product_order_checkouts_do_not_deadlock(self):
        first, second = self.products
        payloads = [
            {
                "src_bill_no": "POS-CONC-ORDER-1",
                "payment": {"method": "CASH", "amount_received": "18.00"},
                "items": [
                    {"product_id": first.id, "qty": "1.000", "price": "9.0000"},
                    {"product_id": second.id, "qty": "1.000", "price": "9.0000"},
                ],
            },
            {
                "src_bill_no": "POS-CONC-ORDER-2",
                "payment": {"method": "CASH", "amount_received": "18.00"},
                "items": [
                    {"product_id": second.id, "qty": "1.000", "price": "9.0000"},
                    {"product_id": first.id, "qty": "1.000", "price": "9.0000"},
                ],
            },
        ]

        results, errors = self._run_checkouts(payloads)

        self.assertTrue(all(result is not None for result in results))
        self.assertEqual(errors, [None, None])
        self.assertEqual(
            list(
                InventoryDetail.objects.filter(product_id__in=[first.id, second.id])
                .order_by("product_id")
                .values_list("onhand_qty", flat=True)
            ),
            [Decimal("8.0000"), Decimal("3.0000")],
        )
        tasks = WmsTask.objects.filter(source_app="pos")
        self.assertEqual(tasks.count(), 2)
        self.assertEqual(WmsTaskLine.objects.filter(task__in=tasks).count(), 4)
        self.assertEqual(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                tx_type="ISSUE",
            ).count(),
            4,
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_pos_checkout_and_ordinary_pick_serialize_without_lost_stock(self):
        product = self.products[0]
        ordinary_task = self._create_ordinary_pick(
            product=product,
            qty=Decimal("4.000"),
        )
        actions = [
            lambda: self._checkout_remaining_six("POS-CONC-ORDINARY-PICK"),
            lambda: execute_posting_handler(
                ordinary_task,
                note="concurrent ordinary PICK",
                by_user=self.users[0],
            ),
        ]

        _results, errors = self._run_actions(actions)

        self.assertEqual(errors, [None, None])
        detail = InventoryDetail.objects.get(product=product)
        self.assertEqual(detail.onhand_qty, Decimal("0.0000"))
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("0.0000"))
        ordinary_issue = InventoryTransaction.objects.get(
            src_model="WmsTask",
            src_id=ordinary_task.id,
            tx_type="ISSUE",
        )
        self.assertIsNone(ordinary_issue.src_line_id)
        self.assertEqual(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                tx_type="ISSUE",
            ).count(),
            2,
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_void_and_checkout_same_sku_serialize_without_lost_update(self):
        sale = self._create_initial_sale(src_bill_no="POS-CONC-VOID-SOURCE")
        actions = [
            lambda: self._checkout_remaining_six("POS-CONC-AFTER-VOID"),
            lambda: void_pos_sale(
                sale_id=sale.id,
                user=self.users[0],
                reason="concurrent void",
            ),
        ]

        _results, errors = self._run_actions(actions)

        self.assertEqual(errors, [None, None])
        sale.refresh_from_db()
        self.assertEqual(sale.status, PosSale.Status.VOIDED)
        self.assertTrue(
            PosSale.objects.filter(src_bill_no="POS-CONC-AFTER-VOID").exists()
        )
        detail = InventoryDetail.objects.get(product=self.products[0])
        self.assertEqual(detail.onhand_qty, Decimal("4.0000"))
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("4.0000"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_return_and_checkout_same_sku_serialize_without_lost_update(self):
        sale = self._create_initial_sale(src_bill_no="POS-CONC-RETURN-SOURCE")
        sale_line = sale.lines.get()
        actions = [
            lambda: self._checkout_remaining_six("POS-CONC-AFTER-RETURN"),
            lambda: create_pos_return(
                user=self.users[0],
                sale_id=sale.id,
                lines=[{"sale_line_id": sale_line.id, "qty": "4.000"}],
                refunds=[{"method": "CASH", "amount": "36.00"}],
                reason="concurrent return",
            ),
        ]

        _results, errors = self._run_actions(actions)

        self.assertEqual(errors, [None, None])
        self.assertTrue(PosReturn.objects.filter(sale=sale).exists())
        self.assertTrue(
            PosSale.objects.filter(src_bill_no="POS-CONC-AFTER-RETURN").exists()
        )
        detail = InventoryDetail.objects.get(product=self.products[0])
        self.assertEqual(detail.onhand_qty, Decimal("4.0000"))
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("4.0000"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_critical_checkout_locking_is_stable_for_twenty_rounds(self):
        for round_index in range(20):
            oversell_product = self._create_product(
                f"PC-STRESS-OVER-{round_index}",
                f"POS oversell stress {round_index}",
                Decimal("10.0000"),
            )
            oversell_payloads = [
                {
                    "src_bill_no": f"POS-STRESS-OVER-{round_index}-{worker}",
                    "payment": {"method": "CASH", "amount_received": "54.00"},
                    "items": [
                        {
                            "product_id": oversell_product.id,
                            "qty": "6.000",
                            "price": "9.0000",
                        }
                    ],
                }
                for worker in (1, 2)
            ]

            oversell_results, oversell_errors = self._run_checkouts(oversell_payloads)

            self.assertEqual(
                sum(result is not None for result in oversell_results),
                1,
                msg=f"oversell round {round_index}",
            )
            failures = [error for error in oversell_errors if error is not None]
            self.assertEqual(len(failures), 1, msg=f"oversell round {round_index}")
            self.assertIsInstance(
                failures[0], ValidationError, msg=f"oversell round {round_index}"
            )
            oversell_detail = InventoryDetail.objects.get(product=oversell_product)
            self.assertEqual(oversell_detail.onhand_qty, Decimal("4.0000"))

            first = self._create_product(
                f"PC-STRESS-A-{round_index}",
                f"POS order stress A {round_index}",
                Decimal("5.0000"),
            )
            second = self._create_product(
                f"PC-STRESS-B-{round_index}",
                f"POS order stress B {round_index}",
                Decimal("5.0000"),
            )
            order_payloads = [
                {
                    "src_bill_no": f"POS-STRESS-ORDER-{round_index}-1",
                    "payment": {"method": "CASH", "amount_received": "18.00"},
                    "items": [
                        {"product_id": first.id, "qty": "1.000", "price": "9.0000"},
                        {"product_id": second.id, "qty": "1.000", "price": "9.0000"},
                    ],
                },
                {
                    "src_bill_no": f"POS-STRESS-ORDER-{round_index}-2",
                    "payment": {"method": "CASH", "amount_received": "18.00"},
                    "items": [
                        {"product_id": second.id, "qty": "1.000", "price": "9.0000"},
                        {"product_id": first.id, "qty": "1.000", "price": "9.0000"},
                    ],
                },
            ]

            order_results, order_errors = self._run_checkouts(order_payloads)

            self.assertTrue(
                all(result is not None for result in order_results),
                msg=(
                    f"reverse-order round {round_index}: "
                    f"errors={[repr(error) for error in order_errors]}"
                ),
            )
            self.assertEqual(
                order_errors,
                [None, None],
                msg=f"reverse-order round {round_index}",
            )
            self.assertEqual(
                list(
                    InventoryDetail.objects.filter(product_id__in=[first.id, second.id])
                    .order_by("product_id")
                    .values_list("onhand_qty", flat=True)
                ),
                [Decimal("3.0000"), Decimal("3.0000")],
            )
