"""Read-only review metrics and synthetic local application requests. No database access."""
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ["APP_ENV"] = "test"
os.environ["DJANGO_SETTINGS_MODULE"] = "wmsmaster.settings"
root = Path("/wms")
sys.path.insert(0, str(root))
import django
django.setup()
from django.conf import settings
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import Client, override_settings
from django.urls import URLResolver, get_resolver
from rest_framework.test import APIRequestFactory, force_authenticate
from allapp.salesapp import salemini_api as mall

dest = root / "var/assessments/2026-11-18/current-worktree"
evidence = {}
with patch.object(BaseDatabaseWrapper, "ensure_connection", side_effect=AssertionError("Offline review must not access DB")):
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        (base / "media").mkdir()
        sample = bytes(range(32))
        (base / "media/bv2.apk").write_bytes(sample)
        with override_settings(BASE_DIR=base, ALLOWED_HOSTS=["testserver"]):
            client = Client()
            responses = []
            for headers in ({}, {"HTTP_RANGE":"bytes=16-31"}):
                response = client.get("/api/v1/bv2/synthetic.apk", **headers)
                responses.append(dict(request_range=headers.get("HTTP_RANGE"), status=response.status_code,
                                      length=len(b"".join(response.streaming_content)), advertised=response.get("Accept-Ranges"),
                                      content_range=response.get("Content-Range")))
            evidence["apk_download_synthetic"] = responses

    class CountingOrders:
        def __init__(self, total):
            self.total = total
            self.visited = 0
        def select_related(self, *args):
            return self
        def order_by(self, *args):
            return self
        def __iter__(self):
            for order_id in range(self.total):
                self.visited += 1
                yield SimpleNamespace(id=order_id, source="sale-mini")
    rows = []
    for total in [100, 10000]:
        dataset = CountingOrders(total)
        request = APIRequestFactory().get("/api/sale-mini/orders/", {"page": 1, "page_size": 20}, HTTP_HOST="testserver")
        force_authenticate(request, user=SimpleNamespace(pk=1, is_authenticated=True))
        with (override_settings(ALLOWED_HOSTS=["testserver"]),
              patch.object(mall, "_buyer_bindings_for_user", return_value=[1]),
              patch.object(mall.SaleMiniOrderMapping.objects, "filter", return_value=dataset),
              patch.object(mall, "_order_group_payload", side_effect=lambda req, group: group[0].id)):
            response = mall.SaleMiniOrderListCreateApi.as_view()(request)
            assert response.status_code == 200
            rows.append(dict(input_rows=total, query_rows_consumed=dataset.visited,
                             first_page_count=len(response.data["results"]), total=response.data["count"]))
    evidence["buyer_order_pagination"] = dict(samples=rows, method="Real list view, group and paginator, with synthetic query iterable and stub payload formatter. Counts in-memory rows consumed, not SQL wall time or production load.")
    applications = [str(x) for x in settings.INSTALLED_APPS if str(x).startswith("allapp.")]
    evidence["domain_app_registrations"] = applications
    routes = Counter()
    def walk(patterns):
        for item in patterns:
            if isinstance(item, URLResolver):
                walk(item.url_patterns)
            else:
                callback = item.callback
                module = getattr(getattr(callback,"cls",None) or getattr(callback,"view_class",None) or callback,"__module__","")
                if module.startswith("allapp."):
                    routes[module.split(".")[1]] += 1
    walk(get_resolver().url_patterns)
    evidence["registered_url_patterns_with_application_callback"] = dict(sorted(routes.items()))
files = ["wmspda/pages/pos/index.vue","wmsandroid/pages/pos/index.vue","allapp/salesapp/salemini_api.py","allapp/pos/services.py"]
evidence["file_lines"] = {name:len((root/name).read_bytes().splitlines()) for name in files}
(dest/"independent-evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({k:evidence[k] for k in ("apk_download_synthetic","buyer_order_pagination","file_lines")},ensure_ascii=False))
