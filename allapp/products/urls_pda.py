from django.urls import path

from .views_excel import ProductImportExcelApi, ProductImportTemplateApi


app_name = "products_pda"

urlpatterns = [
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
