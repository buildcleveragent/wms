from django.utils.functional import SimpleLazyObject

from .menu import build_ribbon


def console_menu(request):
    """Only resolve navigation permissions on templates that use the console ribbon."""
    return {"CONSOLE_MENU": SimpleLazyObject(lambda: build_ribbon(request))}
