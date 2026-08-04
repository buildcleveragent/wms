from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding, Supplier
from allapp.locations.models import Warehouse
from allapp.outbound.serializers import OutboundOrderCreateSerializer
from allapp.products.models import Product, ProductUom


class OwnerSupplierScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="SUP-OWN", name="Supplier Owner")
        self.other_owner = Owner.objects.create(code="SUP-OTHER", name="Other Owner")
        self.warehouse = Warehouse.objects.create(code="SUP-WH", name="Supplier WH")
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.user = get_user_model().objects.create_user(username="supplier-user")
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        uom = ProductUom.objects.create(code="SUP-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="SUP-PRODUCT",
            sku="SUP-PRODUCT",
            name="Supplier Product",
            base_uom=uom,
            price=Decimal("10"),
        )
        self.active_supplier = Supplier.objects.create(
            owner=self.owner,
            code="SUP-ACTIVE",
            name="Active Supplier",
        )
        self.inactive_supplier = Supplier.objects.create(
            owner=self.owner,
            code="SUP-INACTIVE",
            name="Inactive Supplier",
            is_active=False,
        )
        self.other_supplier = Supplier.objects.create(
            owner=self.other_owner,
            code="SUP-OTHER",
            name="Other Supplier",
        )
        self.request = APIRequestFactory().post("/api/outbound/orders/")
        self.request.user = self.user

    def payload(self, supplier_id):
        return {
            "warehouse_id": self.warehouse.id,
            "supplier_id": supplier_id,
            "outbound_type": "SUPPLIER_RETURN",
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": "1.000",
                    "price": "0.0000",
                }
            ],
        }

    def test_same_owner_active_supplier_is_allowed(self):
        serializer = OutboundOrderCreateSerializer(
            data=self.payload(self.active_supplier.id),
            context={"request": self.request},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_missing_inactive_and_cross_owner_suppliers_share_safe_error(self):
        expected = "供应商不存在、已停用或不属于当前货主。"
        for supplier_id in (
            self.other_supplier.id,
            self.inactive_supplier.id,
            999999,
        ):
            with self.subTest(supplier_id=supplier_id):
                serializer = OutboundOrderCreateSerializer(
                    data=self.payload(supplier_id),
                    context={"request": self.request},
                )
                self.assertFalse(serializer.is_valid())
                self.assertEqual(str(serializer.errors["supplier_id"][0]), expected)
