from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PrintConfig, SystemSetting
from .serializers import PrintConfigSerializer


class SystemSettingsApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        grouped = {
            SystemSetting.POS_NAMESPACE: {
                SystemSetting.POS_SALE_PRINT_METHOD_KEY: SystemSetting.POS_SALE_PRINT_FRONTEND,
                SystemSetting.POS_SALE_PRINT_CONFIG_KEY: (
                    SystemSetting.POS_DEFAULT_SALE_PRINT_CONFIG
                ),
            }
        }
        flat = {
            f"{SystemSetting.POS_NAMESPACE}.{SystemSetting.POS_SALE_PRINT_METHOD_KEY}": (
                SystemSetting.POS_SALE_PRINT_FRONTEND
            ),
            f"{SystemSetting.POS_NAMESPACE}.{SystemSetting.POS_SALE_PRINT_CONFIG_KEY}": (
                SystemSetting.POS_DEFAULT_SALE_PRINT_CONFIG
            ),
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


class PrintConfigListApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        module = request.query_params.get("module") or PrintConfig.Module.POS_SALE
        queryset = PrintConfig.objects.filter(module=module, is_active=True).order_by(
            "sort_order", "code"
        )
        default_config = resolve_default_print_config(module)
        return Response(
            {
                "results": PrintConfigSerializer(queryset, many=True).data,
                "default": (
                    PrintConfigSerializer(default_config).data
                    if default_config
                    else None
                ),
            }
        )


class DefaultPrintConfigApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        module = request.query_params.get("module") or PrintConfig.Module.POS_SALE
        default_config = resolve_default_print_config(module)
        if not default_config:
            return Response({"detail": "未找到可用打印配置。"}, status=404)
        return Response(PrintConfigSerializer(default_config).data)


def resolve_default_print_config(module):
    code = None
    if module == PrintConfig.Module.POS_SALE:
        code = SystemSetting.get_value(
            SystemSetting.POS_NAMESPACE,
            SystemSetting.POS_SALE_PRINT_CONFIG_KEY,
            SystemSetting.POS_DEFAULT_SALE_PRINT_CONFIG,
        )
    return PrintConfig.get_default(module, code=code)
