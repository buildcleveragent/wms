from django.urls import path

from .views_excel import (
    ProductExportExcelApi,
    ProductExportOwnersApi,
    ProductImportExcelApi,
    ProductImportTemplateApi,
)


app_name = "products_pda"

urlpatterns = [
    path("export-owners/", ProductExportOwnersApi.as_view(), name="export-owners"),
    path("export-excel/", ProductExportExcelApi.as_view(), name="export-excel"),
    path(
        "import-template/",
        ProductImportTemplateApi.as_view(),
        name="import-template",
    ),
    path(
        "import-excel/",
        ProductImportExcelApi.as_view(),
        name="import-excel",
    ),
]
