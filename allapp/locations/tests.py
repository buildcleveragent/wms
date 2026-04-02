from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.locations.models import Container, Location, Subwarehouse, Warehouse


class LocationsWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Location", code="OWN-LOC")
        self.warehouse = Warehouse.objects.create(code="WH-LOC-1", name="Warehouse Location 1")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWLOC1",
            name="Subwarehouse Location 1",
        )

    def test_subwarehouse_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            Subwarehouse.objects.create(code="SWLOC2", name="Subwarehouse Location 2")

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_location_derives_warehouse_from_subwarehouse_code(self):
        location = Location.objects.create(
            code="SWLOC1-01-01-01",
            name="Location 1",
        )

        self.assertEqual(location.subwarehouse_id, self.subwarehouse.id)
        self.assertEqual(location.warehouse_id, self.warehouse.id)

    def test_container_derives_warehouse_from_location(self):
        location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWLOC1-01-01-02",
            name="Location 2",
        )

        container = Container.objects.create(
            owner=self.owner,
            location=location,
            container_no="CONT-LOC-1",
        )

        self.assertEqual(container.warehouse_id, self.warehouse.id)
