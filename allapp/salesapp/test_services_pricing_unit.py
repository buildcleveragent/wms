from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Q

from . import services_pricing


def _attr(obj, path):
    value = obj
    for part in path.split("__"):
        value = getattr(value, part)
    return value


def _matches_lookup(obj, lookup, expected):
    if lookup.endswith("__isnull"):
        return (_attr(obj, lookup.removesuffix("__isnull")) is None) is expected
    if lookup.endswith("__lte"):
        value = _attr(obj, lookup.removesuffix("__lte"))
        return value is not None and value <= expected
    if lookup.endswith("__gte"):
        value = _attr(obj, lookup.removesuffix("__gte"))
        return value is not None and value >= expected
    return _attr(obj, lookup) == expected


def _matches_q(obj, query):
    if not isinstance(query, Q):
        return True

    results = []
    for child in query.children:
        if isinstance(child, Q):
            results.append(_matches_q(obj, child))
        else:
            lookup, expected = child
            results.append(_matches_lookup(obj, lookup, expected))

    matched = all(results) if query.connector == Q.AND else any(results)
    return not matched if query.negated else matched


class FakeQuerySet:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *queries, **lookups):
        rows = self.rows
        for query in queries:
            rows = [row for row in rows if _matches_q(row, query)]
        for lookup, expected in lookups.items():
            rows = [row for row in rows if _matches_lookup(row, lookup, expected)]
        return FakeQuerySet(rows)

    def annotate(self, **kwargs):
        return self

    def order_by(self, *fields):
        rows = list(self.rows)
        for field in reversed(fields):
            reverse = field.startswith("-")
            key = field[1:] if reverse else field
            rows.sort(key=lambda row: self._sort_value(row, key), reverse=reverse)
        return FakeQuerySet(rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)

    def _sort_value(self, row, key):
        if key == "channel_priority":
            channel = getattr(
                row,
                "channel",
                getattr(getattr(row, "promotion", None), "channel", None),
            )
            return 1 if channel is not None else 0
        if key == "customer_priority":
            customer = getattr(getattr(row, "promotion", None), "customer", None)
            return 1 if customer is not None else 0
        return _attr(row, key)


class FakeItems:
    def __init__(self, product, price):
        self.product = product
        self.price = price

    def filter(self, product):
        if product != self.product:
            return FakeQuerySet([])
        return FakeQuerySet([SimpleNamespace(price=self.price)])


class FakeModel:
    def __init__(self, rows):
        self.objects = FakeQuerySet(rows)


def _empty_model():
    return FakeModel([])


def _patch_price_dependencies(monkeypatch, *, price_lists=(), special_prices=()):
    monkeypatch.setattr(services_pricing, "CustomerSpecialPrice", _empty_model())
    monkeypatch.setattr(services_pricing, "PriceMemory", _empty_model())
    monkeypatch.setattr(services_pricing, "PriceList", FakeModel(price_lists))
    monkeypatch.setattr(
        services_pricing, "PromotionSpecialPrice", FakeModel(special_prices)
    )


def test_compute_price_prefers_channel_pricelist_over_default(monkeypatch):
    owner = object()
    customer = object()
    channel = object()
    product = object()
    default_list = SimpleNamespace(
        owner=owner,
        channel=None,
        is_default=True,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        items=FakeItems(product, Decimal("30.0000")),
    )
    channel_list = SimpleNamespace(
        owner=owner,
        channel=channel,
        is_default=False,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        items=FakeItems(product, Decimal("20.0000")),
    )
    _patch_price_dependencies(monkeypatch, price_lists=[default_list, channel_list])

    price = services_pricing.compute_price_for_line(
        owner, customer, product, date(2026, 6, 28), channel
    )

    assert price == Decimal("20.0000")


def test_compute_price_ignores_channel_pricelist_without_customer_channel(monkeypatch):
    owner = object()
    customer = object()
    channel = object()
    product = SimpleNamespace(price=Decimal("12.5000"))
    channel_list = SimpleNamespace(
        owner=owner,
        channel=channel,
        is_default=False,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        items=FakeItems(product, Decimal("5.0000")),
    )
    _patch_price_dependencies(monkeypatch, price_lists=[channel_list])

    price = services_pricing.compute_price_for_line(
        owner, customer, product, date(2026, 6, 28), None
    )

    assert price == Decimal("12.5000")


def test_compute_price_ignores_other_customer_promotion_special(monkeypatch):
    owner = object()
    customer = object()
    other_customer = object()
    product = SimpleNamespace(price=Decimal("12.5000"))
    other_promotion = SimpleNamespace(
        owner=owner,
        customer=other_customer,
        channel=None,
        effective_from=date(2020, 1, 1),
        effective_to=None,
    )
    special_price = SimpleNamespace(
        owner=owner,
        product=product,
        promotion=other_promotion,
        special_price=Decimal("1.0000"),
    )
    _patch_price_dependencies(monkeypatch, special_prices=[special_price])

    price = services_pricing.compute_price_for_line(
        owner, customer, product, date(2026, 6, 28), None
    )

    assert price == Decimal("12.5000")
