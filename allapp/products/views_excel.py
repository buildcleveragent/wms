from urllib.parse import quote

from django.http import HttpResponse
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    response["Content-Disposition"] = (
        f"attachment; filename=product_import_template.xlsx; filename*=UTF-8''{quote(TEMPLATE_FILENAME)}"
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
