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

    def test_location_rejects_warehouse_that_conflicts_with_code(self):
        other_warehouse = Warehouse.objects.create(
            code="WH-LOC-2",
            name="Warehouse Location 2",
        )
        Subwarehouse.objects.create(
            warehouse=other_warehouse,
            code="SWLOC2",
            name="Subwarehouse Location 2",
        )

        with self.assertRaises(ValidationError) as exc:
            Location.objects.create(
                warehouse=self.warehouse,
                code="SWLOC2-01-01-01",
                name="Cross-warehouse location",
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_container_rejects_location_from_another_warehouse(self):
        location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWLOC1-01-01-03",
            name="Location 3",
        )
        other_warehouse = Warehouse.objects.create(
            code="WH-LOC-2",
            name="Warehouse Location 2",
        )

        with self.assertRaises(ValidationError) as exc:
            Container.objects.create(
                owner=self.owner,
                warehouse=other_warehouse,
                location=location,
                container_no="CONT-CROSS-WH",
            )

        self.assertIn("location", exc.exception.message_dict)

    def test_container_scope_requires_matching_owner_binding(self):
        with self.assertRaises(ValidationError) as public_exc:
            Container.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                scope=Container.Scope.PUBLIC,
                container_no="CONT-PUBLIC-OWNER",
            )
        self.assertIn("owner", public_exc.exception.message_dict)

        with self.assertRaises(ValidationError) as private_exc:
            Container.objects.create(
                warehouse=self.warehouse,
                scope=Container.Scope.PRIVATE,
                container_no="CONT-PRIVATE-NO-OWNER",
            )
        self.assertIn("owner", private_exc.exception.message_dict)
