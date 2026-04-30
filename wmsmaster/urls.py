from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from rest_framework_simplejwt.views import TokenVerifyView
from .views import TokenObtainPairView, TokenRefreshView
from . import settings
from .views import profile_view           # 别再用名为 profile 的函数，避免与标准库冲突
from .auth_views import LoginView         # ★ 直接导入视图，不再 include 模块
from allapp.console.views_dashboard import DashboardHomeView
urlpatterns = [
    path('api/inbound/', include('allapp.inbound.urls')),  # 放在可能覆盖它的 router 之前
    path("api/", include("allapp.inventory.urls")),
    path("api/", include("allapp.billing.urls")),
    # path("", TemplateView.as_view(template_name="index.html"), name="home"),
    path('', DashboardHomeView.as_view(), name='dashboard_home'),
    path("admin/", admin.site.urls),
    # path('areceive-products/', ReceiveGoodsWithoutOrder.as_view(), name='receive-products'),

    # Auth
    path("api/auth/login/",   LoginView.as_view(),  name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/verify/",  TokenVerifyView.as_view(),  name="token_verify"),
    path("api/auth/profile/", profile_view,               name="auth_profile"),

    # 业务 API
    # 统一 API 版本入口（把所有业务路由聚合到 allapp.api.urls）
    path("api/v1/", include("allapp.api.urls")),




    # 调试
    path("api-auth/", include("rest_framework.urls")),

    # Django 账号
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/login/", RedirectView.as_view(pattern_name="admin:login", query_string=True), name="login"),
    path("api/tasking/", include("allapp.tasking.urls")),
    path("api/reports/", include("allapp.reports.urls_api")),

    path("reports/", include("allapp.reports.urls")),
    path("tasking/console/", include("allapp.tasking.urls_console", namespace="tasking_console")),
    path("console/", include("allapp.console.urls", namespace="console")),
    path(    "console/op/", include(("allapp.console.urls_op", "op"), namespace="op"),),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),  # 登录获取 Token
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),  # 刷新 Token
    path("api/", include("allapp.outbound.urls")),  # ✅ 就地挂载 outbound 的 API

    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    # static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    path('products/', include('allapp.products.urls')),  # 包含产品模块的路由


]
if settings.ENABLE_DEBUG_TOOLBAR:
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
