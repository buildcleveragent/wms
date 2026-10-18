"""Customer access always requires an explicit owner relationship."""

from django.db.models import OuterRef, Subquery

from .models import Customer, OwnerCustomerBinding


def active_bindings(owner_id=None):
    bindings = OwnerCustomerBinding.objects.filter(
        is_active=True,
        customer__is_active=True,
        customer__is_deleted=False,
        owner__is_active=True,
        owner__is_deleted=False,
    )
    if owner_id is not None:
        bindings = bindings.filter(owner_id=owner_id)
    return bindings


def customer_binding(customer, owner_id, *, active=True):
    customer_id = getattr(customer, "pk", customer)
    qs = (
        active_bindings(owner_id)
        if active
        else OwnerCustomerBinding.all_objects.filter(owner_id=owner_id)
    )
    return qs.select_related("salesperson").filter(customer_id=customer_id).first()


def customers_for_owner(owner_id):
    bindings = active_bindings(owner_id)
    current = bindings.filter(customer_id=OuterRef("pk"))
    return Customer.objects.filter(pk__in=bindings.values("customer_id")).annotate(
        owner_customer_code=Subquery(current.values("code")[:1]),
        owner_customer_salesperson_id=Subquery(current.values("salesperson_id")[:1]),
    )


def customer_code(customer, owner_id):
    if customer is None:
        return ""
    binding = customer_binding(customer, owner_id, active=False)
    return binding.code if binding else ""
