"""Read-only owner history endpoints; all paths enforce the same tenant boundary."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.audit import record_audit_event

from .owner_history import OwnerHistory, parse_filters


class OwnerHistoryView(APIView):
    permission_classes = [IsAuthenticated]
    section = "summary"

    def get(self, request, event_id=None):
        history = OwnerHistory(request.user, parse_filters(request.query_params))
        if self.section == "export":
            return history.export(request)
        if event_id is not None:
            payload = history.event(event_id)
        elif self.section == "summary":
            payload = history.summary()
        else:
            payload = history.listing(self.section)
        record_audit_event(
            action="QUERY",
            module="inventory.owner_history",
            request=request,
            owner_id=history.owner_id,
            metadata={"section": self.section, "event_id": event_id},
        )
        response = Response(payload)
        response["Cache-Control"] = "private, no-store"
        return response
