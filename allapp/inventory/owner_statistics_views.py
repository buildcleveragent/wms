"""Read-only, tenant-scoped business statistics endpoints."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.audit import record_audit_event

from .owner_charts import chart_payload
from .owner_statistics import OwnerStatistics, parse_statistics_filters


class OwnerStatisticsView(APIView):
    permission_classes = [IsAuthenticated]
    section = "summary"

    def get(self, request, event_id=None):
        report = OwnerStatistics(
            request.user,
            parse_statistics_filters(request.query_params, chart=self.section == "chart"),
        )
        if self.section == "export":
            return report.export(request)
        if event_id is not None:
            payload = report.event(event_id)
        elif self.section == "chart":
            payload = chart_payload(report)
        elif self.section == "summary":
            payload = report.summary()
        else:
            payload = report.listing(self.section)
        record_audit_event(
            action="QUERY",
            module="inventory.owner_statistics",
            request=request,
            owner_id=report.owner_id,
            metadata={"section": self.section, "event_id": event_id},
        )
        response = Response(payload)
        response["Cache-Control"] = "private, no-store"
        return response
