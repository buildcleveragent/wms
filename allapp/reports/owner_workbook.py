"""Build a complete XLSX on the worker's single consistent-read connection."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, localcontext

from django.db import connection
from MySQLdb.cursors import SSDictCursor
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.series import SeriesLabel
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from rest_framework.exceptions import PermissionDenied

from allapp.core.owner_exports import _cell
from allapp.inventory.owner_history import CATEGORIES, stamp
from allapp.inventory.owner_history_sql import table
from allapp.inventory.owner_statistics import OwnerStatistics
from allapp.inventory.owner_statistics_sql import statistics_sql

from .owner_archive_sheets import write_registers


def heading(ws, labels):
    cells = [_cell(ws, label) for label in labels]
    for cell in cells:
        cell.font = Font(name="微软雅黑", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="205545")
    ws.append(cells)


class Sheet:
    limit = 1048575  # One row is reserved for the header.

    def __init__(self, book, name, headers, basis):
        self.book, self.name, self.headers, self.basis = book, name, headers, basis
        self.parts = []
        self.new_part()

    def new_part(self):
        suffix = f" {len(self.parts)+1}" if self.parts else ""
        self.ws = self.book.create_sheet(self.name + suffix)
        self.ws.freeze_panes = "A2"
        for i, header in enumerate(self.headers, 1):
            column = self.ws.column_dimensions[get_column_letter(i)]
            column.width = max(16, len(header) * 2 + 2)
            column.hidden = header == "单位编码"
        heading(self.ws, self.headers)
        self.parts.append([self.ws.title, 0])

    def append(self, row):
        if self.parts[-1][1] == self.limit:
            self.finish()
            self.new_part()
        self.ws.append([_cell(self.ws, v) for v in row])
        self.parts[-1][1] += 1

    def finish(self):
        self.ws.auto_filter.ref = f"A1:{get_column_letter(len(self.headers))}{self.parts[-1][1]+1}"


def facts(report):
    where, args = report.where()
    # Scope source documents before attributing customers or original salespeople.
    sql = statistics_sql() + f"""
      SELECT f.*, COALESCE(o.customer_id, orig.customer_id, sale.selected_customer_id) customer_key,
        COALESCE(o.created_by_id,i.created_by_id,orig.created_by_id,sale.cashier_id) actor_key
      FROM (SELECT * FROM statistics_facts WHERE {where}) f
      LEFT JOIN {table('outbound.OutboundOrder')} o ON o.id=f.outbound_id AND o.owner_id=%s
      LEFT JOIN {table('inbound.InboundOrder')} i ON i.id=f.inbound_id AND i.owner_id=%s
      LEFT JOIN {table('inbound.CustomerReturn')} ret ON ret.id=f.customer_return_id AND ret.owner_id=%s
      LEFT JOIN {table('outbound.OutboundOrder')} orig ON orig.id=ret.original_order_id AND orig.owner_id=%s
      LEFT JOIN {table('pos.PosSale')} sale ON f.document_key=CONCAT('pos:',sale.id)
      ORDER BY f.posted_at,f.id"""
    from datetime import timezone

    cutoff = report.cutoff
    if cutoff.tzinfo:
        cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    with connection.connection.cursor(SSDictCursor) as cursor:
        cursor.execute(
            sql,
            [
                report.owner_id,
                report.ledger_max,
                report.owner_id,
                report.review_max,
                cutoff,
                *args,
                *[report.owner_id] * 4,
            ],
        )
        yield from cursor


def calendar_keys(day, job):
    if day is None:
        return [("时间未记录", "时间未记录")] * 4
    monday = day - timedelta(days=day.weekday())
    month = day.replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    periods = [
        (day, day),
        (monday, monday + timedelta(days=6)),
        (month, next_month - timedelta(days=1)),
        (day.replace(month=1, day=1), day.replace(month=12, day=31)),
    ]
    return [
        (max(start, job.date_from or start), min(end, job.date_to or end)) for start, end in periods
    ]


def write_workbook(path, job, owner):
    from allapp.accounts.models import UserRoleScope
    from allapp.baseinfo.models import OwnerCustomerBinding

    report = OwnerStatistics(
        job.user, {k: v for k, v in (("date_from", job.date_from), ("date_to", job.date_to)) if v}
    )
    if report.owner_id != owner.pk or report.personal:
        raise PermissionDenied("货主范围已变更。")
    as_of = stamp(report.cutoff)
    book = Workbook(write_only=True)
    summary = book.create_sheet("经营总览")
    index = book.create_sheet("导出说明与目录")
    sheets = []

    def sheet(name, headers, basis):
        s = Sheet(book, name, headers.split(), basis)
        sheets.append(s)
        return s

    customers = dict(
        OwnerCustomerBinding.all_objects.filter(owner=owner).values_list(
            "customer_id", "customer__name"
        )
    )
    agents = {
        user_id: (last + first).strip() or f"业务员 {user_id}"
        for user_id, first, last in UserRoleScope.objects.filter(
            owner=owner, role="owner_salesperson"
        ).values_list("user_id", "user__first_name", "user__last_name")
    }
    fact_header = "记录编号 原始单号 单据关联键 时间 商品编号 商品编码 商品名称 仓库 批次 业务类型 数量变动 单位 币种 已计价金额 入账状态 客户ID 业务员ID 收货记录ID"
    inbound = sheet("实际入库明细", fact_header, "成功入账的正常收货，良品及破损计入，拒收单列")
    outbound = sheet("实际出库明细", fact_header, "正常出库按实物复核及现有 POS 等实际出库来源")
    others = sheet("其他库存业务", fact_header, "退货、回补、调整、转货及待核对分别展示")
    pending = sheet(
        "待核对明细", fact_header + " 核对事项", "业务时间；未记录时间仅在全部历史中纳入"
    )
    groups = ["按日统计", "按周统计", "按月统计", "按年统计", "商品汇总", "客户汇总", "业务员汇总"]
    aggregates = [defaultdict(lambda: [Decimal(0), Decimal(0), 0, 0]) for _ in groups]
    monthly = defaultdict(lambda: defaultdict(dict))
    ranking = defaultdict(lambda: defaultdict(Decimal))
    overview = defaultdict(lambda: [Decimal(0), 0, 0])
    quantities = defaultdict(Decimal)
    with localcontext() as ctx:
        ctx.prec = 80
        for r in facts(report):
            when = stamp(r["posted_at"])
            day = date.fromisoformat(when[:10]) if when else None
            cat, currency = r["category"], r["currency"]
            amount = r["amount"]
            qty = abs(r["qty_delta"])
            category = CATEGORIES.get(cat, cat)
            product = (str(r["product_id"]), r["product_name"])
            row = [
                r["id"],
                r["document_no"],
                r["document_key"],
                when or "时间未记录",
                product[0],
                r["product_code"],
                product[1],
                r["warehouse_name"],
                r["batch_no"],
                category,
                r["qty_delta"],
                r["base_unit_name"],
                currency,
                amount,
                r["posting_status"],
                str(r["customer_key"]) if r["customer_key"] in customers else None,
                str(r["actor_key"]) if r["actor_key"] in agents else None,
                str(r["receipt_id"]) if r["receipt_id"] is not None else None,
            ]
            target = inbound if cat == "inbound" else outbound if cat == "outbound" else others
            target.append(row)
            notes = []
            if cat in ("inbound", "outbound") and amount is None:
                notes.append("缺采购价" if cat == "inbound" else "缺售价")
            if not when:
                notes.append("时间未记录")
            if cat == "unknown" or not r["document_no"]:
                notes.append("来源待核对")
            if r["dimensions_unverified"]:
                notes.append("批次明细待核对")
            if r["posting_status"] != "POSTED":
                notes.append("库存扣减待核对")
            if notes:
                pending.append([*row, "、".join(notes)])
            cust, actor = r["customer_key"], r["actor_key"]
            keys = calendar_keys(day, job) + [
                product,
                (str(cust), customers[cust]) if cust in customers else ("", "未关联"),
                (str(actor), agents[actor]) if actor in agents else ("", "未关联"),
            ]
            for summary_groups, key in zip(aggregates, keys):
                totals = summary_groups[
                    (*key, category, r["base_unit"], r["base_unit_name"], currency)
                ]
                totals[0] += qty
                totals[1] += amount or 0
                totals[2] += int(amount is not None)
                totals[3] += int(amount is None)
            if cat in ("inbound", "outbound"):
                quantities[category, r["base_unit"], r["base_unit_name"]] += qty
                totals = overview[currency, category]
                totals[0] += amount or 0
                totals[1] += int(amount is not None)
                totals[2] += int(amount is None)
                if amount is not None:
                    if day:
                        values = monthly[currency][day.strftime("%Y-%m")]
                        values[cat] = values.get(cat, 0) + amount
                    if cat == "outbound":
                        ranking[currency][product] += amount
    for i, (title, data) in enumerate(zip(groups, aggregates)):
        labels = "实际开始 实际结束" if i < 4 else "编号 名称"
        tab = sheet(
            title,
            labels + " 业务类型 单位编码 单位 币种 数量 已计价金额 已计价分录数 未计价分录数",
            "业务时间；数量按基本单位、金额按币种，缺价保留数量",
        )
        # ISO strings sort chronologically, including partially selected periods.
        ordered = sorted(
            data.items(),
            key=lambda item: (
                (item[0][2:], -item[1][1], str(item[0][0])) if i >= 4 else tuple(map(str, item[0]))
            ),
        )
        for key, (qty, amount, priced, missing) in ordered:
            tab.append([*key, qty, amount if priced else None, priced, missing])

    summary.column_dimensions["A"].width = 26
    summary.column_dimensions["B"].width = 35
    for col in "CDE":
        summary.column_dimensions[col].width = 19
    summary.append(["货主业务档案", _cell(summary, owner.name)])
    summary.append(["数据截至（北京时间）", as_of])
    summary.append(["范围", f"{job.date_from or '最早记录'} 至 {job.date_to or '最新记录'}"])
    heading(summary, ["币种", "业务类型", "已计价金额", "已计价分录数", "未计价分录数"])
    for (currency, category), (amount, priced, missing) in sorted(overview.items()):
        summary.append(
            [
                _cell(summary, v)
                for v in [currency, category, amount if priced else None, priced, missing]
            ]
        )
    summary.append([])
    heading(summary, ["业务类型", "单位", "收发数量"])
    for (category, _code, unit), qty in sorted(quantities.items()):
        summary.append([_cell(summary, v) for v in (category, unit, qty)])
    rank_sheet = sheet(
        "商品金额排名",
        "币种 名次 商品ID 商品名称 已计价销售金额",
        "出库商品按已知销售金额排序；全缺价明细在待核对表",
    )
    for currency, products in sorted(ranking.items()):
        for position, ((product_id, name), amount) in enumerate(
            sorted(products.items(), key=lambda x: (-x[1], int(x[0][0]))), 1
        ):
            rank_sheet.append([currency, position, product_id, name, amount])
    charts = sheet(
        "图表数据",
        "月份或商品 采购金额或销售排名金额 出库销售金额 币种",
        "金额按币种独立；仅已计价部分，详见经营总览",
    )
    chart_no = 0
    for currency in sorted(set(monthly) | set(ranking)):
        for title, rows, series in [
            (
                "月度收发金额",
                [
                    (month, values.get("inbound"), values.get("outbound"), currency)
                    for month, values in sorted(monthly[currency].items())
                ],
                2,
            ),
            (
                "出库商品金额前十",
                [
                    (f"{p[1]}（{p[0]}）", v, None, currency)
                    for p, v in sorted(
                        ranking[currency].items(), key=lambda item: (-item[1], int(item[0][0]))
                    )[:10]
                ],
                1,
            ),
        ]:
            if not rows:
                continue
            start = charts.parts[-1][1] + 2
            for row in rows:
                charts.append(row)
            chart = BarChart()
            chart.title = title + " · " + currency
            chart.add_data(
                Reference(
                    charts.ws,
                    min_col=2,
                    max_col=1 + series,
                    min_row=start,
                    max_row=start + len(rows) - 1,
                )
            )
            for s, name in zip(
                chart.series, ("采购金额", "销售金额") if series == 2 else ("销售金额",)
            ):
                s.tx = SeriesLabel(v=name)
            chart.set_categories(
                Reference(charts.ws, min_col=1, min_row=start, max_row=start + len(rows) - 1)
            )
            summary.add_chart(chart, f"H{1+chart_no*17}")
            chart_no += 1

    write_registers(sheet, job, owner, report.cutoff)
    index.column_dimensions["A"].width = 30
    index.column_dimensions["B"].width = 24
    index.column_dimensions["C"].width = 80
    index.freeze_panes = "A2"
    heading(index, ["工作表", "数据行数", "统计口径／数据范围"])
    index.append(
        ["经营总览", None, "北京时间 " + as_of + "；单号、SKU、批次按文本保留；高精度值以文本保留"]
    )
    for s in sheets:
        s.finish()
        for title, count in s.parts:
            link = _cell(index, title)
            link.hyperlink = Hyperlink(ref="A1", location=f"'{title}'!A1", display=title)
            link.font = Font(color="145392", underline="single")
            index.append([link, count, s.basis])
    book.save(path)
