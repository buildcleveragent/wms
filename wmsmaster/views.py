# wmsmaster/views.py
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.products.excel_import import can_import_products


@api_view(["GET"])
@permission_classes([IsAuthenticated])  # 确保用户已认证
def profile_view(request):
    user = request.user  # 获取当前用户
    access_scope = AccessScope.for_user(user)
    perms = sorted(list(user.get_all_permissions()))  # 获取用户的所有权限

    # 根据用户的权限来决定菜单项
    # 假设你有一个 Menu 模型，按权限来过滤菜单
    # 这里只是一个简单的示例，你可以根据实际情况修改

    menus = []  # 初始化菜单列表
    if "inbound.view_receiving" in perms:
        menus.append(
            {"path": "/inbound/receiving", "title": "收货看板", "icon": "el-icon-menu"}
        )
    if "inventory.view_detail" in perms:
        menus.append({"path": "/inventory", "title": "库存管理", "icon": "el-icon-box"})

    # 添加其他菜单项（根据权限动态生成）
    if any(p.startswith("billing.") for p in perms):
        menus.append(
            {"path": "/admin/billing/", "title": "计费", "icon": "el-icon-credit-card"}
        )

    can_process_warehouse_assisted_outbound = (
        bool(access_scope.warehouse_ids)
        and UserRoleScope.Role.WAREHOUSE_OPERATOR in access_scope.roles
        and user.has_perm("outbound.process_warehouse_assisted_outbound")
        and user.has_perm("tasking.claim_task_as_wh_operator")
    )
    is_warehouse_role = bool(
        access_scope.roles.intersection(
            {
                UserRoleScope.Role.WAREHOUSE_OPERATOR,
                UserRoleScope.Role.WAREHOUSE_MANAGER,
                UserRoleScope.Role.WAREHOUSE_BOSS,
            }
        )
    )
    is_owner_role = bool(
        access_scope.roles.intersection(
            {
                UserRoleScope.Role.OWNER_MANAGER,
                UserRoleScope.Role.OWNER_SALESPERSON,
            }
        )
    )
    is_boss = UserRoleScope.Role.WAREHOUSE_BOSS in access_scope.roles

    return Response(
        {
            "user": {
                "id": user.id,
                "username": user.username,
                "display_name": user.get_full_name() or user.username,
                # Compatibility scalar fields are derived from the authoritative
                # explicit scope. Multi-scope users consume ``scopes`` below.
                "owner_id": access_scope.single_owner_id,
                "warehouse_id": access_scope.single_warehouse_id,
                "roles": sorted(access_scope.roles),
                "scopes": access_scope.as_dict(),
            },
            "perms": perms,
            "capabilities": {
                "can_process_warehouse_assisted_outbound": can_process_warehouse_assisted_outbound,
                "can_view_warehouse_operations": is_warehouse_role
                and user.has_perm("reports.view_warehouse_operations"),
                "can_view_owner_operations": is_owner_role
                and user.has_perm("reports.view_owner_operations"),
                "can_view_boss_dashboard": is_boss
                and user.has_perm("reports.view_boss_dashboard"),
                "can_view_warehouse_finance": is_boss
                and user.has_perm("reports.view_warehouse_finance"),
                "can_export_operations": access_scope.is_valid
                and user.has_perm("reports.export_operations"),
                "can_import_products": can_import_products(user),
            },
            "menus": menus,  # 返回动态生成的菜单
        }
    )


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


@api_view(["POST"])
@permission_classes([AllowAny])
def logout_view(request):
    refresh = request.data.get("refresh") or ""
    if not refresh:
        return Response(
            {"refresh": ["请提供刷新令牌。"]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        RefreshToken(refresh).blacklist()
    except TokenError:
        return Response(
            {"refresh": ["刷新令牌无效或已失效。"]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_live_view(request):
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
def health_ready_view(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return Response(
            {"status": "unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    return Response({"status": "ok"})
