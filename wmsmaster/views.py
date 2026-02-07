# wmsmaster/views.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
# views.py
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView



@api_view(["GET"])
@permission_classes([IsAuthenticated])  # 确保用户已认证
def profile_view(request):
    user = request.user  # 获取当前用户
    perms = sorted(list(user.get_all_permissions()))  # 获取用户的所有权限

    # 根据用户的权限来决定菜单项
    # 假设你有一个 Menu 模型，按权限来过滤菜单
    # 这里只是一个简单的示例，你可以根据实际情况修改

    menus = []  # 初始化菜单列表
    if "inbound.view_receiving" in perms:
        menus.append({"path": "/inbound/receiving", "title": "收货看板", "icon": "el-icon-menu"})
    if "inventory.view_detail" in perms:
        menus.append({"path": "/inventory", "title": "库存管理", "icon": "el-icon-box"})

    # 添加其他菜单项（根据权限动态生成）
    if "billing.view_billing" in perms:
        menus.append({"path": "/billing", "title": "计费", "icon": "el-icon-credit-card"})

    return Response({
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.get_full_name() or user.username,
        },
        "perms": perms,
        "menus": menus  # 返回动态生成的菜单
    })
