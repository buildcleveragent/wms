from django.test import TestCase

from allapp.baseinfo.models import CarrierCompany, Employee


class BaseinfoWarehouseScopeTests(TestCase):
    def test_employee_without_warehouse_stays_null(self):
        employee = Employee.objects.create(code="EMP-1", name="Employee 1")

        self.assertIsNone(employee.warehouse_id)

    def test_carrier_company_without_warehouse_stays_null(self):
        carrier = CarrierCompany.objects.create(name="Carrier 1", manager="Manager 1")

        self.assertIsNone(carrier.warehouse_id)
