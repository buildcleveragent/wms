from urllib.parse import quote

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import Owner

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
    product_import_warehouse_queryset,
    resolve_product_import_access,
)
from .identifier_excel import (
    IdentifierExcelError,
    IdentifierExcelConflictError,
    build_identifier_export,
    build_identifier_template,
    import_identifier_workbook,
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


def import_product_file(*, uploaded_file, user, request=None, warehouse_id=None):
    importer = ProductExcelImporter(
        user=user, request=request, warehouse_id=warehouse_id
    )
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
                warehouse_id=request.data.get("warehouse_id"),
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


class ProductImportWarehousesApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        resolve_product_import_access(request.user)
        warehouses = product_import_warehouse_queryset(request.user).select_related(
            "default_receive_location"
        )
        results = []
        for warehouse in warehouses:
            location = warehouse.default_receive_location
            receipt_ready = bool(
                location
                and location.warehouse_id == warehouse.pk
                and location.is_active
                and not location.is_deleted
                and not location.is_disabled
                and not location.is_frozen
            )
            results.append(
                {
                    "id": warehouse.pk,
                    "code": warehouse.code,
                    "name": warehouse.name,
                    "receipt_ready": receipt_ready,
                    "default_receive_location": (
                        {
                            "id": location.pk,
                            "code": location.code,
                            "name": location.name,
                        }
                        if location
                        else None
                    ),
                }
            )
        return Response({"results": results})


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
                "template_version": "6",
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


class ProductIdentifierTemplateApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        resolve_product_import_access(request.user)
        response = HttpResponse(build_identifier_template(), content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = "attachment; filename=product_identifier_maintenance.xlsx; filename*=UTF-8''%E5%95%86%E5%93%81%E6%A0%87%E8%AF%86%E7%BB%B4%E6%8A%A4%E6%A8%A1%E6%9D%BF.xlsx"
        return response


class ProductIdentifierExportApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner = resolve_export_owner(request.user, request.query_params.get("owner_id"))
        response = HttpResponse(build_identifier_export(owner), content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = "attachment; filename=product_identifier_history.xlsx"
        return response


class ProductIdentifierImportApi(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        access = resolve_product_import_access(request.user)
        try:
            owner_id = int(request.data.get("owner_id"))
        except (TypeError, ValueError):
            return Response({"detail": "请选择有效货主。"}, status=status.HTTP_400_BAD_REQUEST)
        if not access.allows_owner(owner_id):
            return Response({"detail": "无权维护该货主的商品标识。"}, status=status.HTTP_403_FORBIDDEN)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response({"detail": "请上传 Excel 文件，字段名为 file。"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = import_identifier_workbook(uploaded, owner=Owner.objects.get(pk=owner_id))
        except IdentifierExcelConflictError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except (IdentifierExcelError, Owner.DoesNotExist) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
