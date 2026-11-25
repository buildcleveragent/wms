from datetime import date

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.audit import record_audit_event

from .models import OwnerArchiveJob
from .owner_archive_jobs import archive_file, authorized_owner, enqueue


class ArchiveDates(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        if set(self.initial_data) - set(self.fields):
            raise ValidationError("只支持开始日期和结束日期。")
        if (
            attrs.get("date_from")
            and attrs.get("date_to")
            and attrs["date_from"] > attrs["date_to"]
        ):
            raise ValidationError("开始日期不能晚于结束日期。")
        if attrs.get("date_to") == date.max:
            raise ValidationError("结束日期超出可查询范围。")
        return attrs


def payload(job):
    return {
        key: getattr(job, key)
        for key in ("id", "status", "date_from", "date_to", "created_at", "expires_at")
    }


def visible(request):
    return OwnerArchiveJob.objects.filter(
        user=request.user, owner_id=authorized_owner(request.user), expires_at__gt=timezone.now()
    )


class OwnerArchiveJobs(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {"results": [payload(j) for j in visible(request).order_by("-id")]},
            headers={"Cache-Control": "private, no-store"},
        )

    def post(self, request):
        dates = ArchiveDates(data=request.data)
        dates.is_valid(raise_exception=True)
        return Response(payload(enqueue(request, dates.validated_data)), status=202)


class OwnerArchiveDownload(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        job = get_object_or_404(visible(request), pk=pk, status="ready")
        try:
            stream = archive_file(job).open("rb")
        except FileNotFoundError:
            raise NotFound("文件已失效，请重新生成。") from None
        try:
            record_audit_event(
                action="EXPORT", module="owner.archive.download", request=request, obj=job
            )
            response = FileResponse(
                stream, as_attachment=True, filename=f"货主业务档案-{job.pk}.xlsx"
            )
            response["Cache-Control"] = "private, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception:
            stream.close()
            raise
