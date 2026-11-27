from django.urls import path

from . import views_daily_close as views

urlpatterns = [
    path("", views.close_list, name="inventory_daily_close_list"),
    path("pilot/", views.configure_pilot, name="inventory_daily_close_pilot"),
    path("run/", views.manual_run, name="inventory_daily_close_run"),
    path("<int:pk>/", views.close_detail, name="inventory_daily_close_detail"),
    path(
        "<int:pk>/opening/<int:version>/",
        views.opening_version,
        name="inventory_daily_close_opening",
    ),
    path("<int:pk>/retry/", views.retry_close, name="inventory_daily_close_retry"),
    path("<int:pk>/<slug:action>/", views.review_action, name="inventory_daily_close_action"),
    path("evidence/<int:pk>/download/", views.attachment, name="inventory_daily_close_attachment"),
]
