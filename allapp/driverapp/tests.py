from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from allapp.baseinfo.models import Driver, Owner
from allapp.driverapp.models import DeliveryTask, DriverShift
from allapp.locations.models import Warehouse


class DriverappWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Driver", code="OWN-DRV")
        self.driver = Driver.objects.create(name="Driver 1")
        self.warehouse = Warehouse.objects.create(code="WH-DRV-1", name="Warehouse Driver 1")

    def test_delivery_task_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            DeliveryTask.objects.create(owner=self.owner)

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_driver_shift_without_warehouse_stays_null(self):
        shift = DriverShift.objects.create(
            driver=self.driver,
            action=DriverShift.Action.CLOCK_IN,
            at=timezone.now(),
            request_id="shift-no-warehouse",
        )

        self.assertIsNone(shift.warehouse_id)

    def test_driver_shift_request_id_is_idempotent_per_driver(self):
        occurred_at = timezone.now()
        DriverShift.objects.create(
            driver=self.driver,
            warehouse=self.warehouse,
            action=DriverShift.Action.CLOCK_IN,
            at=occurred_at,
            request_id="same-mobile-request",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DriverShift.objects.create(
                    driver=self.driver,
                    warehouse=self.warehouse,
                    action=DriverShift.Action.CLOCK_OUT,
                    at=occurred_at,
                    request_id="same-mobile-request",
                )

        other_driver = Driver.objects.create(name="Driver 2")
        other_shift = DriverShift.objects.create(
            driver=other_driver,
            warehouse=self.warehouse,
            action=DriverShift.Action.CLOCK_IN,
            at=occurred_at,
            request_id="same-mobile-request",
        )
        self.assertIsNotNone(other_shift.pk)
