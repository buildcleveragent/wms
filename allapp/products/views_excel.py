from urllib.parse import quote

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.audit import record_audit_event

from .excel_export import (
    build_product_export_workbook,
    export_owner_queryset,
    resolve_export_owner,
    resolve_product_export_access,
)
from .excel_import import (
    ProductExcelImporter,
    ProductImportConflictError,
    ProductImportFileError,
    build_product_import_template,
    resolve_product_import_access,
)


XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEMPLATE_FILENAME = "商品批量导入模板.xlsx"


def product_template_response(user):
    content = build_product_import_template(user)
    response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
    encoded_filename = quote(TEMPLATE_FILENAME)
    response["Content-Disposition"] = (
        "attachment; filename=product_import_template.xlsx; "
        f"filename*=UTF-8''{encoded_filename}"
    )
    response["Cache-Control"] = "private, no-store"
    return response


def import_product_file(*, uploaded_file, user, request=None):
    importer = ProductExcelImporter(user=user, request=request)
    return importer.import_file(uploaded_file)


class ProductImportTemplateApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        resolve_product_import_access(request.user)
        return product_template_response(request.user)


class ProductImportExcelApi(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        resolve_product_import_access(request.user)
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return Response(
                {"detail": "请上传 Excel 文件，字段名为 file。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = import_product_file(
                uploaded_file=uploaded_file,
                user=request.user,
                request=request,
            )
        except ProductImportConflictError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except ProductImportFileError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        response_status = (
            status.HTTP_400_BAD_REQUEST if result["error_count"] else status.HTTP_200_OK
        )
        return Response(result, status=response_status)


class ProductExportOwnersApi(APIView):
    permission_classes = [IsAuthenticated]
    page_size = 20

    def get(self, request):
        resolve_product_export_access(request.user)
        search = request.query_params.get("search", "")
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        queryset = export_owner_queryset(request.user, search=search)
        count = queryset.count()
        start = (page - 1) * self.page_size
        owners = list(queryset[start : start + self.page_size])
        return Response(
            {
                "count": count,
                "page": page,
                "page_size": self.page_size,
                "next": page + 1 if start + self.page_size < count else None,
                "results": [
                    {"id": owner.pk, "code": owner.code, "name": owner.name}
                    for owner in owners
                ],
            }
        )


class ProductExportExcelApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.query_params.get("owner_id"):
            return Response(
                {"detail": "请选择要导出的货主。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        owner = resolve_export_owner(request.user, request.query_params.get("owner_id"))
        content, product_count, package_count = build_product_export_workbook(owner)
        record_audit_event(
            action="products.export_excel",
            module="products",
            request=request,
            owner_id=owner.pk,
            succeeded=True,
            metadata={
                "owner_code": owner.code,
                "product_count": product_count,
                "package_count": package_count,
                "template_version": "3",
            },
        )
        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        filename_owner_code = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in owner.code
        )
        filename = f"商品档案-{filename_owner_code or owner.pk}-{timestamp}.xlsx"
        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = (
            "attachment; filename=product_archive.xlsx; "
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
        response["Cache-Control"] = "private, no-store"
        return response
