# allapp/core/api/mixins.py
from rest_framework.decorators import action
from rest_framework.response import Response

class BulkApproveMixin:
    @action(methods=["post"], detail=False)
    def bulk_approve(self, request):
        ids = request.data.get("ids", [])
        count = self.get_queryset().filter(id__in=ids).update(status="APPROVED")
        return Response({"updated": count})

    @action(methods=["post"], detail=False)
    def bulk_unapprove(self, request):
        ids = request.data.get("ids", [])
        count = self.get_queryset().filter(id__in=ids).update(status="NEW")
        return Response({"updated": count})

# allapp/core/api/viewsets.py
from rest_framework import viewsets, permissions
class BaseModelViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    # 统一 search/order 字段在子类声明：search_fields / ordering_fields / filterset_fields
