from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SystemSetting


class SystemSettingsApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        grouped = {
            SystemSetting.POS_NAMESPACE: {
                SystemSetting.POS_SALE_PRINT_METHOD_KEY: SystemSetting.POS_SALE_PRINT_FRONTEND,
            }
        }
        flat = {
            f"{SystemSetting.POS_NAMESPACE}.{SystemSetting.POS_SALE_PRINT_METHOD_KEY}": (
                SystemSetting.POS_SALE_PRINT_FRONTEND
            )
        }

        queryset = SystemSetting.objects.filter(
            is_active=True,
            client_visible=True,
        ).order_by("namespace", "sort_order", "key")
        for setting in queryset:
            value = setting.effective_value()
            grouped.setdefault(setting.namespace, {})[setting.key] = value
            flat[f"{setting.namespace}.{setting.key}"] = value

        print_method = grouped[SystemSetting.POS_NAMESPACE].get(
            SystemSetting.POS_SALE_PRINT_METHOD_KEY
        )
        if print_method not in {
            SystemSetting.POS_SALE_PRINT_FRONTEND,
            SystemSetting.POS_SALE_PRINT_BACKEND,
        }:
            print_method = SystemSetting.POS_SALE_PRINT_FRONTEND
            grouped[SystemSetting.POS_NAMESPACE][
                SystemSetting.POS_SALE_PRINT_METHOD_KEY
            ] = print_method
            flat[
                f"{SystemSetting.POS_NAMESPACE}.{SystemSetting.POS_SALE_PRINT_METHOD_KEY}"
            ] = print_method

        return Response({"settings": grouped, "flat": flat})
