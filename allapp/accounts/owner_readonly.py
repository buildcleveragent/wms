"""Business-write barrier for the owner finance role, independent of grants."""

from django.http import JsonResponse
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import SAFE_METHODS
from rest_framework_simplejwt.authentication import JWTAuthentication

from .access import AccessScope

READ_ONLY_MESSAGE = "货主财务为只读角色，不能修改业务数据。"


def is_owner_finance(user):
    """Use current role identity, including conflicting scopes, not cached grants."""
    return "owner_finance" in AccessScope.for_user(user).roles


def require_business_write(user):
    """Protect service entry points that may also be invoked outside HTTP."""
    if is_owner_finance(user):
        raise PermissionDenied(READ_ONLY_MESSAGE)


class OwnerFinanceReadOnlyMiddleware:
    """Enforce before dispatch for both session and JWT endpoints.

    POST exceptions are concrete authentication/download views, never suffixes
    such as 'export'. Object authorization still happens in the original view.
    """

    auth_views = frozenset(
        {
            "token_obtain_pair",
            "token_obtain_pair_legacy",
            "token_refresh",
            "token_refresh_legacy",
            "token_verify",
            "auth_password_change",
            "auth_logout",
            "login",
            "logout",
            "password_change",
            "admin:login",
            "admin:logout",
            "rest_framework:login",
            "rest_framework:logout",
        }
    )
    download_views = frozenset(
        {
            ("allapp.reports.views_operations", "OperationsExportApi"),
            ("allapp.reports.owner_archive_views", "OwnerArchiveJobs"),
            ("allapp.core.print_tickets", "PrintTicketApi"),
        }
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in SAFE_METHODS:
            return None
        view = getattr(view_func, "cls", getattr(view_func, "view_class", view_func))
        if request.method == "POST" and (
            request.resolver_match.view_name in self.auth_views
            or (view.__module__, view.__name__) in self.download_views
        ):
            return None
        users = [request.user]
        # Django's AuthenticationMiddleware resolves only session credentials.
        # Resolve bearer credentials too, without replacing the view's own auth.
        try:
            authenticated = JWTAuthentication().authenticate(request)
        except APIException:
            authenticated = None  # The view retains its normal invalid-token response.
        if authenticated:
            users.append(authenticated[0])
        if any(is_owner_finance(user) for user in users):
            return JsonResponse({"detail": READ_ONLY_MESSAGE}, status=403)
        return None
