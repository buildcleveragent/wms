"""Short-lived document credentials that cannot authenticate normal API calls."""

from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import Resolver404, resolve
from django.utils.crypto import constant_time_compare, salted_hmac
from rest_framework import permissions
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

SALT = "wms.document-print.v1"
MAX_AGE = 120
ALLOWED_VIEWS = {
    ("allapp.inbound.export_print", "ReceiveTaskPrintView"),
    ("allapp.outbound.export_print", "pick_task_print"),
    ("allapp.pos.views", "PosSalePrintApi"),
    ("allapp.pos.views", "PosShiftPrintApi"),
}


def _password_version(user):
    return salted_hmac(SALT, user.password).hexdigest()


def issue_print_ticket(user, path):
    """Sign a single print path; object authorization is still checked at consumption."""
    if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "?#"):
        raise ValidationError("打印路径无效。")
    try:
        view = resolve(path).func
    except Resolver404 as exc:
        raise ValidationError("打印路径不存在。") from exc
    view = getattr(view, "view_class", view)
    if (view.__module__, view.__name__) not in ALLOWED_VIEWS:
        raise ValidationError("该路径不支持打印票据。")
    return signing.dumps(
        {"user": user.pk, "path": path, "version": _password_version(user)}, salt=SALT
    )


def user_from_print_ticket(ticket, path):
    """Validate expiry, exact document, current account state and password version."""
    try:
        payload = signing.loads(ticket, salt=SALT, max_age=MAX_AGE)
        if payload["path"] != path:
            raise ValueError("wrong document")
        user = get_user_model().objects.get(pk=payload["user"], is_active=True)
        if not constant_time_compare(payload["version"], _password_version(user)):
            raise ValueError("revoked password")
        return user
    except (
        signing.BadSignature,
        KeyError,
        TypeError,
        ValueError,
        get_user_model().DoesNotExist,
    ) as exc:
        raise AuthenticationFailed("打印票据无效或已过期，请重新打开打印。") from exc


class PrintTicketApi(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        path = request.data.get("path")
        ticket = issue_print_ticket(request.user, path)
        return Response({"ticket": ticket, "expires_in": MAX_AGE})
