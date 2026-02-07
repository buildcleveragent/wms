# allapp/labeling/admin.py
from django.contrib import admin
from .models import Printer, LabelTemplate, PrintJob, LabelRenderLog


@admin.register(Printer)
class PrinterAdmin(admin.ModelAdmin):
    list_display = ("name", "model", "address", "is_active")
    search_fields = ("name", "model", "address")
    list_filter = ("is_active",)
    ordering = ("name",)


@admin.register(LabelTemplate)
class LabelTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "engine", "width_mm", "height_mm")
    search_fields = ("name", "type", "engine")
    list_filter = ("type", "engine")
    ordering = ("name",)


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner",
        "warehouse",
        "template",
        "printer",
        "status",
        "copies",
        "created_at",
        "printed_at",
    )
    search_fields = (
        "template__name",
        "printer__name",
        "owner__name",
    )
    list_filter = ("status", "template", "printer", "owner")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "printed_at", "error")


@admin.register(LabelRenderLog)
class LabelRenderLogAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "ok", "message", "created_at")
    search_fields = ("message",)
    list_filter = ("ok",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

