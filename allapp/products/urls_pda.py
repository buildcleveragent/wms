from django.urls import path

from .views_excel import (
    ProductExportExcelApi,
    ProductExportOwnersApi,
    ProductImportExcelApi,
    ProductImportTemplateApi,
    ProductImportWarehousesApi,
    ProductIdentifierExportApi,
    ProductIdentifierImportApi,
    ProductIdentifierTemplateApi,
)


app_name = "products_pda"

urlpatterns = [
    path("identifier-maintenance-template/", ProductIdentifierTemplateApi.as_view(), name="identifier-template"),
    path("identifier-maintenance-export/", ProductIdentifierExportApi.as_view(), name="identifier-export"),
    path("identifier-maintenance-import/", ProductIdentifierImportApi.as_view(), name="identifier-import"),
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
    path(
        "import-warehouses/",
        ProductImportWarehousesApi.as_view(),
        name="import-warehouses",
    ),
]
