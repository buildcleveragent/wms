from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from openpyxl import load_workbook

from allapp.accounts import test_owner_finance as finance_fixtures
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer
from allapp.inventory import test_owner_statistics as statistics_fixtures

from .models import OwnerArchiveJob
from .owner_archive_jobs import archive_file, run_one

URL = "/api/reports/owner-archives/"


@override_settings(USE_TZ=True)
class OwnerArchiveTests(TransactionTestCase):
    tx = finance_fixtures.OwnerFinanceTests.tx
    receipt = statistics_fixtures.OwnerStatisticsTests.receipt
    review = statistics_fixtures.OwnerStatisticsTests.review
    client_for = finance_fixtures.OwnerFinanceTests.client_for

    def setUp(self):
        finance_fixtures.OwnerFinanceTests.setUpTestData.__func__(type(self))
        self.root = TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        settings = override_settings(OWNER_ARCHIVE_ROOT=self.root.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def test_finance_export_reconciles_and_is_private(self):
        self.receipt(100)
        self.receipt(60, "2026-11-03T10:00:00+08:00")
        self.review(30)
        self.review(20, "2026-11-03T11:00:00+08:00")
        self.receipt(7, "2026-10-30T10:00:00+08:00")
        Customer.objects.filter(pk=self.customer.pk).update(
            phone="13800001234", address="完整收货地址禁止导出"
        )
        client = self.client_for(self.finance)
        dates = {"date_from": "2026-11-01", "date_to": "2026-11-30"}
        result = client.post(URL, dates, format="json")
        self.assertEqual(result.status_code, 202, result.data)
        pk = result.data["id"]
        self.assertEqual(client.post(URL, dates, format="json").data["id"], pk)
        self.assertEqual(client.get(URL).data["results"][0]["status"], "queued")
        self.assertTrue(run_one())
        job = OwnerArchiveJob.objects.get(pk=pk)
        self.assertEqual(job.status, "ready")
        download = URL + str(pk) + "/download/"
        response = client.get(download)
        self.assertEqual(response.status_code, 200)
        data = b"".join(response.streaming_content)
        response.close()
        book = load_workbook(BytesIO(data), data_only=True)
        self.assertGreaterEqual(len(book.sheetnames), 25)
        daily = list(book["按日统计"].values)[1:]
        self.assertEqual(sum(r[6] for r in daily if r[2] == "正常入库"), 160)
        self.assertEqual(sum(r[7] for r in daily if r[2] == "正常出库"), 250)
        for name in ("按日统计", "按周统计", "按月统计", "按年统计", "商品汇总"):
            grouped = list(book[name].values)[1:]
            self.assertEqual(sum(r[6] for r in grouped if r[2] == "正常出库"), 50, name)
            self.assertEqual(sum(r[7] for r in grouped if r[2] == "正常入库"), 400, name)
        actual = list(book["实际出库明细"].values)[1:]
        self.assertEqual([abs(r[10]) for r in actual], [30, 20])
        self.assertEqual([r[11] for r in actual], ["瓶", "瓶"])
        self.assertEqual([r[16] for r in actual], [str(self.sales2.pk)] * 2)
        self.assertEqual(len(book["经营总览"]._charts), 2)
        self.assertTrue(
            any(cell[0].hyperlink for cell in book["导出说明与目录"] if cell[0].row > 2)
        )
        with ZipFile(BytesIO(data)) as z:
            from xml.etree.ElementTree import fromstring

            contents = " ".join(
                " ".join(fromstring(z.read(n)).itertext())
                for n in z.namelist()
                if n.startswith("xl/worksheets/") and n.endswith(".xml")
            )
        for secret in ("13800001234", "完整收货地址禁止导出", "其他客户"):
            self.assertNotIn(secret, contents)
        self.assertIn("****1234", contents)
        self.assertEqual(self.client_for(self.user).get(download).status_code, 404)
        self.assertEqual(self.client_for(self.sales).post(URL, {}, format="json").status_code, 403)
        UserRoleScope.objects.filter(user=self.finance).update(is_active=False)
        self.assertEqual(client.get(download).status_code, 403)

    @override_settings(USE_TZ=False)
    def test_revoked_job_cleanup_recovery_and_dates(self):
        self.receipt(3, day="2026-11-03T00:01:00", price="0")
        client = self.client_for(self.user)
        for dates in (
            {"owner_id": self.other.pk},
            {"date_from": "2026-11-03", "date_to": "2026-11-01"},
        ):
            self.assertEqual(client.post(URL, dates, format="json").status_code, 400)
        pk = client.post(URL, {}, format="json").data["id"]
        OwnerArchiveJob.objects.filter(pk=pk).update(status="running")
        job = OwnerArchiveJob.objects.get(pk=pk)
        archive_file(job, ".part").write_bytes(b"incomplete")
        self.assertTrue(run_one())
        job.refresh_from_db()
        self.assertEqual(job.status, "ready")
        self.assertFalse(archive_file(job, ".part").exists())
        book = load_workbook(archive_file(job), read_only=True)
        try:
            daily = list(book["按日统计"].values)[1:]
            self.assertEqual(daily[0][0], "2026-11-03")
            self.assertEqual(daily[0][6:], (3, None, 0, 1))
            pending = list(book["待核对明细"].values)[1:]
            self.assertIn("缺采购价", pending[0][-1])
        finally:
            book.close()
        OwnerArchiveJob.objects.filter(pk=pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(client.get(URL + str(pk) + "/download/").status_code, 404)
        run_one()
        self.assertFalse(archive_file(job).exists())
        other_pk = client.post(URL, {}, format="json").data["id"]
        UserRoleScope.objects.filter(user=self.user).update(owner=self.other)
        self.assertEqual(client.get(URL).data["results"], [])
        self.assertTrue(run_one())
        self.assertEqual(OwnerArchiveJob.objects.get(pk=other_pk).status, "failed")


def test_large_sheet_reopens_and_splits(tmp_path, monkeypatch):
    from openpyxl import Workbook

    from .owner_workbook import Sheet

    book = Workbook(write_only=True)
    sheet = Sheet(book, "明细", ["编号"], "全部历史")
    monkeypatch.setattr(Sheet, "limit", 50001)
    for i in range(50004):
        sheet.append([f"{i:08d}"])
    sheet.finish()
    path = tmp_path / "large.xlsx"
    book.save(path)
    opened = load_workbook(path, read_only=True)
    assert opened.sheetnames == ["明细", "明细 2"]
    assert sum(1 for _ in opened["明细"].values) == 50002
    assert list(opened["明细 2"].values)[-1] == ("00050003",)
    opened.close()
