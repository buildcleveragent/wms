from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Customer, Owner
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder


class OutboundWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Outbound", code="OWN-OUT")
        self.user = get_user_model().objects.create_user(username="outbound-sales", password="x")
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUS-OUT",
            name="Customer Outbound",
        )

    def test_outbound_order_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            OutboundOrder.objects.create(
                owner=self.owner,
                customer=self.customer,
            )

        self.assertIn("warehouse", exc.exception.message_dict)
