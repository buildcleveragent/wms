"""Additive Boss APIs: attention, digest and paginated calendar statistics."""

from rest_framework.views import APIView

from .boss_attention import attention_page
from .boss_digest import confirm, digest, digest_details
from .boss_periods import comparison_ranges, metric_details, period_page, totals
from .views_boss_activity import ActivityApiMixin, positive_int


class BossAttentionApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        return self.respond(
            scope,
            attention_page(
                access,
                positive_int(request.query_params, "page", 1),
                positive_int(request.query_params, "page_size", 20, 100),
            ),
        )


class BossDigestApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        return self.respond(scope, digest(access))

    def post(self, request):
        access, scope = self.context(request)
        return self.respond(scope, confirm(access, request.data.get("token", "")))


class BossDigestDetailsApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        return self.respond(
            scope,
            digest_details(
                access,
                request.query_params.get("token", ""),
                category=request.query_params.get("category", ""),
                action=request.query_params.get("action", ""),
                cursor=request.query_params.get("cursor", ""),
                limit=positive_int(request.query_params, "limit", 20, 100),
            ),
        )


class BossPeriodsApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        start, end = scope["date_from"], scope["date_to"]
        data = period_page(
            access,
            start,
            end,
            request.query_params.get("granularity", "auto"),
            positive_int(request.query_params, "page", 1),
            positive_int(request.query_params, "page_size", 100, 200),
        )
        return self.respond(scope, data)


class BossPeriodSummaryApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        start, end = scope["date_from"], scope["date_to"]
        current = totals(access, start, end)
        comparisons = {}
        for key, dates in comparison_ranges(
            start, end, request.query_params.get("preset", "custom")
        ).items():
            comparisons[key] = (
                {"date_from": dates[0], "date_to": dates[1], "metrics": totals(access, *dates)}
                if dates
                else None
            )
        return self.respond(
            scope,
            {
                "results": current,
                "comparisons": comparisons,
                "coverage": "业务记录口径；历史完整性未知。无历史记录时不计算增长率。",
                "notices": [
                    "POS金额未记录币种，整单收付款不分摊到货主。",
                    "应计金额按原币不含税口径；库存余额与应收余额另按截止日查看。",
                ],
            },
        )


class BossPeriodDetailsApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        return self.respond(
            scope,
            metric_details(
                access,
                scope["date_from"],
                scope["date_to"],
                request.query_params.get("metric", ""),
                positive_int(request.query_params, "page", 1),
                positive_int(request.query_params, "page_size", 20, 100),
                params=request.query_params,
            ),
        )


class BossSlaDetailsApi(ActivityApiMixin, APIView):
    def get(self, request):
        from .boss_periods import sla_details

        access, scope = self.context(request)
        data = sla_details(
            access,
            scope["date_from"],
            scope["date_to"],
            request.query_params.get("ratio", "otif"),
            positive_int(request.query_params, "page", 1),
            positive_int(request.query_params, "page_size", 20, 100),
        )
        response = self.respond(scope, data)
        response.data["meta"].update(
            data_as_of=data["processed_at"],
            etl_processed_at=data["processed_at"],
            source="PROCESSED_REPORTS",
        )
        return response


class BossTodayApi(ActivityApiMixin, APIView):
    def get(self, request):
        from .boss_today import today_summary

        access, scope = self.context(request)
        data = today_summary(access)
        scope.update(date_from=data["business_today"], date_to=data["business_today"])
        return self.respond(scope, data)


class BossTodayDetailsApi(ActivityApiMixin, APIView):
    def get(self, request):
        from rest_framework.exceptions import ValidationError
        from .boss_today import today_details

        access, scope = self.context(request)
        if scope["date_from"] != scope["date_to"]:
            raise ValidationError({"date": "今日业务明细须指定同一天。"})
        return self.respond(
            scope,
            today_details(
                access,
                scope["date_from"],
                request.query_params.get("metric", ""),
                positive_int(request.query_params, "page", 1),
                positive_int(request.query_params, "page_size", 20, 100),
                positive_int(request.query_params, "document_id"),
            ),
        )
