# wmsmaster/views.py
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView  # noqa: F401



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
    if any(p.startswith("billing.") for p in perms):
        menus.append({"path": "/admin/billing/", "title": "计费", "icon": "el-icon-credit-card"})

    return Response({
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.get_full_name() or user.username,
        },
        "perms": perms,
        "menus": menus  # 返回动态生成的菜单
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    user = request.user
    old_password = request.data.get("old_password") or ""
    new_password1 = request.data.get("new_password1") or ""
    new_password2 = request.data.get("new_password2") or ""

    if not old_password:
        return Response(
            {"old_password": ["请输入原密码。"]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not user.check_password(old_password):
        return Response(
            {"old_password": ["原密码不正确。"]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not new_password1:
        return Response(
            {"new_password1": ["请输入新密码。"]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if new_password1 != new_password2:
        return Response(
            {"new_password2": ["两次输入的新密码不一致。"]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        password_validation.validate_password(new_password1, user)
    except ValidationError as exc:
        return Response(
            {"new_password1": list(getattr(exc, "messages", [str(exc)]))},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.set_password(new_password1)
    user.save(update_fields=["password"])

    return Response({"detail": "密码修改成功。"})
