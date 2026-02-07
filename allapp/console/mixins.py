# console/mixins.py
from django.shortcuts import render
from django.template.response import TemplateResponse

class HtmxMixin:
    """根据是否为 HTMX 请求，选择全页模板或局部模板"""
    partial_template_name = None  # e.g. "console/task/_table.html"
    full_template_name = None     # e.g. "console/task/list.html"

    def is_htmx(self):
        return self.request.headers.get("HX-Request") == "true"

    def render(self, context, *, template_name=None, status=200, headers=None):
        tpl = template_name or (self.partial_template_name if self.is_htmx() else self.full_template_name)
        resp = TemplateResponse(self.request, tpl, context, status=status)
        if headers:
            for k, v in headers.items():
                resp[k] = v
        return resp
