from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .boss_automation import automation_status, save_configuration
from .views_boss import CanViewBossDashboard


class BossAutomationSettingsApi(APIView):
    permission_classes = [CanViewBossDashboard]

    def get(self, request):
        return Response(
            {
                **automation_status(),
                "can_manage": bool(request.user.is_active and request.user.is_superuser),
            }
        )

    def patch(self, request):
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied("仅超级用户可以修改全系统自动检查设置。")
        save_configuration(request)
        return self.get(request)
