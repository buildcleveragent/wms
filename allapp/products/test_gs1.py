from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from allapp.products.gs1 import equivalent_gtins, normalize_gtin, public_candidate
from allapp.products.models import Gs1LookupCache


class Gs1HelpersTests(SimpleTestCase):
    def test_normalizes_supported_gtin_forms(self):
        self.assertEqual(normalize_gtin("6921168509256"), ("6921168509256", "06921168509256"))
        self.assertEqual(
            normalize_gtin("0106921168509256"),
            ("06921168509256", "06921168509256"),
        )
        self.assertIn("6921168509256", equivalent_gtins("06921168509256"))

    def test_rejects_invalid_barcode(self):
        with self.assertRaisesMessage(ValueError, "条码必须"):
            normalize_gtin("ABC-123")

    def test_public_candidate_only_exposes_trusted_https_images(self):
        cache = Gs1LookupCache(
            canonical_gtin="06921168509256",
            query_code="6921168509256",
            status=Gs1LookupCache.Status.SUCCESS,
            found=True,
            registered=False,
            expires_at=timezone.now() + timedelta(hours=1),
            payload={
                "name": "测试饮用水",
                "images": [
                    "https://www.gds.org.cn/product/test.jpg",
                    "http://www.gds.org.cn/insecure.jpg",
                    "https://example.com/untrusted.jpg",
                ],
            },
        )

        candidate = public_candidate(cache)

        self.assertEqual(candidate["images"], ["https://www.gds.org.cn/product/test.jpg"])
        self.assertFalse(candidate["registered"])
