from django.urls import path
from .views import dispatch_note_html, dispatch_note_pdf

urlpatterns = [
    path("dispatch/<int:task_id>/", dispatch_note_html, name="report_dispatch_html"),
    path("dispatch/<int:task_id>/pdf/", dispatch_note_pdf, name="report_dispatch_pdf"),
]