"""An opt-in decimal representation for clients that require exact amounts."""

from decimal import Decimal

from rest_framework.response import Response


def _decimal_strings(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _decimal_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimal_strings(item) for item in value]
    return value


class ExactDecimalResponseMixin:
    """Keep legacy JSON by default; decimal_format=string avoids float conversion."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if request.query_params.get("decimal_format") == "string" and isinstance(
            response, Response
        ):
            response.data = _decimal_strings(response.data)
        return response
