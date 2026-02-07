# allapp/inbound/export_views.py
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, Border, Side

from allapp.tasking.models import WmsTask, WmsTaskLine


def export_receive_task_excel(request, task_id):
    # 1) 拿到当前入库任务 + 明细
    task = get_object_or_404(
        WmsTask.objects.select_related("owner", "warehouse", "created_by"),
        pk=task_id,
        task_type=WmsTask.TaskType.RECEIVE,
    )

    lines = (
        WmsTaskLine.objects
        .filter(task_id=task.id)
        .select_related("product", "to_location")
        .order_by("id")
    )

    # 2) 创建工作簿/工作表
    wb = Workbook()
    ws = wb.active
    ws.title = "入库单"

    # 一些样式
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")
    right = Alignment(horizontal="right", vertical="center")
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    title_font = Font(size=14, bold=True)
    header_font = Font(bold=True)

    # 列宽
    ws.column_dimensions["A"].width = 6   # 行号
    ws.column_dimensions["B"].width = 18  # SKU
    ws.column_dimensions["C"].width = 30  # 品名
    ws.column_dimensions["D"].width = 18  # 规格
    ws.column_dimensions["E"].width = 8   # 单位
    ws.column_dimensions["F"].width = 14  # 批号
    ws.column_dimensions["G"].width = 12  # 效期
    ws.column_dimensions["H"].width = 12  # 库位
    ws.column_dimensions["I"].width = 10  # 数量

    row = 1

    # 3) 标题行：入库单 + 单号
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=9)
    cell = ws.cell(row=row, column=1, value=f"入 库 单（{task.task_no}）")
    cell.alignment = center
    cell.font = title_font
    row += 2  # 空一行

    # 4) 任务头信息
    ws.cell(row=row, column=1, value="货主：").alignment = left
    ws.cell(row=row, column=2, value=task.owner.name if task.owner else "").alignment = left

    ws.cell(row=row, column=4, value="仓库：").alignment = left
    ws.cell(row=row, column=5, value=task.warehouse.name if task.warehouse else "").alignment = left

    ws.cell(row=row, column=7, value="日期：").alignment = left
    ws.cell(row=row, column=8, value=task.created_at.date().strftime("%Y-%m-%d")).alignment = left
    row += 1

    ws.cell(row=row, column=1, value="类型：").alignment = left
    ws.cell(row=row, column=2, value=task.get_task_type_display()).alignment = left

    ws.cell(row=row, column=4, value="制单人：").alignment = left
    ws.cell(row=row, column=5, value=(task.created_by.username if task.created_by else "")).alignment = left

    ws.cell(row=row, column=7, value="备注：").alignment = left
    ws.cell(row=row, column=8, value=task.posting_note or "").alignment = left
    row += 2  # 再空一行

    # 5) 表头
    headers = ["行号", "SKU", "品名", "规格", "单位", "批号", "效期", "库位", "数量"]
    for col, title in enumerate(headers, start=1):
        c = ws.cell(row=row, column=col, value=title)
        c.font = header_font
        c.alignment = center
        c.border = border
    row += 1

    # 6) 明细行
    for idx, line in enumerate(lines, start=1):
        ws.cell(row=row, column=1, value=idx).alignment = center
        ws.cell(row=row, column=1).border = border

        ws.cell(row=row, column=2, value=line.product.sku if line.product else "").alignment = left
        ws.cell(row=row, column=2).border = border

        ws.cell(row=row, column=3, value=line.product.name if line.product else "").alignment = left
        ws.cell(row=row, column=3).border = border

        ws.cell(row=row, column=4, value=line.product.spec if line.product else "").alignment = left
        ws.cell(row=row, column=4).border = border

        ws.cell(row=row, column=5, value=getattr(line.product, "base_unit_name", "")).alignment = center
        ws.cell(row=row, column=5).border = border

        ws.cell(row=row, column=6, value="").alignment = left
        ws.cell(row=row, column=6).border = border

        ws.cell(
            row=row,
            column=7,
            value=line.exp_date.strftime("%Y-%m-%d") if getattr(line, "exp_date", None) else "",
        ).alignment = center
        ws.cell(row=row, column=7).border = border

        ws.cell(
            row=row,
            column=8,
            value=line.to_location.code if getattr(line, "location", None) else "",
        ).alignment = left
        ws.cell(row=row, column=8).border = border

        ws.cell(row=row, column=9, value=float(line.qty_plan)).alignment = right
        ws.cell(row=row, column=9).border = border

        row += 1

    # 7) 输出为 Excel 响应
    filename = f"receive_{task.task_no}.xlsx"
    resp = HttpResponse(
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(resp)
    return resp
