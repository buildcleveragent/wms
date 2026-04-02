from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.reports.models import ReportSnapshot


class ReportsWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Report", code="OWN-RPT")
        self.user = get_user_model().objects.create_user(username="report-user", password="x")

    def test_report_snapshot_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            ReportSnapshot.objects.create(
                owner=self.owner,
                src_model="ReportSource",
                src_id=1,
                doc_type="DISPATCH_NOTE",
                payload={"header": {}, "items": []},
                fp="report-snapshot-no-warehouse",
                created_by=self.user,
            )

        self.assertIn("warehouse", exc.exception.message_dict)
