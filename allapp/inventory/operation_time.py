"""Use one business timestamp across inventory balances and their physical movements."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.utils import timezone

_UNSET = object()
_operation_time = ContextVar("inventory_operation_time", default=None)


@contextmanager
def inventory_operation():
    """Nested posting shares the outer timestamp, first taken after inventory locks."""
    if _operation_time.get() is not None:
        yield
        return
    token = _operation_time.set(_UNSET)
    try:
        yield
    finally:
        _operation_time.reset(token)


def inventory_now(explicit=None):
    current = _operation_time.get()
    if current is None:
        return explicit or timezone.now()
    if current is _UNSET:
        current = explicit or timezone.now()
        _operation_time.set(current)
    return current
