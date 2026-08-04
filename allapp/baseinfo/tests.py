from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from allapp.baseinfo.models import CarrierCompany, Customer, Employee, Owner


class BaseinfoWarehouseScopeTests(TestCase):
    def test_employee_without_warehouse_stays_null(self):
        employee = Employee.objects.create(code="EMP-1", name="Employee 1")

        self.assertIsNone(employee.warehouse_id)

    def test_carrier_company_without_warehouse_stays_null(self):
        carrier = CarrierCompany.objects.create(name="Carrier 1", manager="Manager 1")

        self.assertIsNone(carrier.warehouse_id)


class CustomerTenantConstraintTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Customer Owner", code="CUSTOWN1")
        self.other_owner = Owner.objects.create(
            name="Other Customer Owner",
            code="CUSTOWN2",
        )
        self.salesperson = get_user_model().objects.create_user(
            username="customer-salesperson"
        )

    def test_customer_code_is_unique_per_owner_but_reusable_across_owners(self):
        Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="CUSTOMER-001",
            name="First customer",
        )
        other_owner_customer = Customer.objects.create(
            owner=self.other_owner,
            salesperson=self.salesperson,
            code="CUSTOMER-001",
            name="Other owner's customer",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Customer.objects.create(
                    owner=self.owner,
                    salesperson=self.salesperson,
                    code="CUSTOMER-001",
                    name="Duplicate customer",
                )

        self.assertIsNotNone(other_owner_customer.pk)

    def test_blank_external_codes_are_normalized_and_do_not_collide(self):
        first = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="CUSTOMER-002",
            name="Blank external code one",
            external_code="   ",
        )
        second = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="CUSTOMER-003",
            name="Blank external code two",
            external_code="",
        )

        self.assertIsNone(first.external_code)
        self.assertIsNone(second.external_code)
