# allapp/inbound/urls.py
from django.urls import path
from .views import ReceiveGoodsWithoutOrder
from .export_views import export_receive_task_excel
from .export_print import ReceiveTaskPrintView
urlpatterns = [
    path('receive_without_order/', ReceiveGoodsWithoutOrder.as_view(), name='receive-without-order'),
    path("receive_task/<int:task_id>/export_excel/",export_receive_task_excel,name="receive_task_export_excel",),
    path(
        "receive_task/<int:task_id>/print/",
        ReceiveTaskPrintView.as_view(),
        name="receive-task-print",
    ),
]
