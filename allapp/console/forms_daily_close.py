from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.locations.models import Warehouse

REFERENCE_TYPES = {
    "入库单": "inbound",
    "出库单": "outbound",
    "盘点单": "count",
    "调整单": "adjustment",
    "账单": "bill",
    "账期": "period",
    "外部凭证": "external",
}
REFERENCE_LABELS = {value: key for key, value in REFERENCE_TYPES.items()}
REFERENCE_LABELS["billing_impact"] = "计费影响"
ACTION_LABELS = {
    "RECHECK": "重新核查",
    "WAREHOUSE_SUBMIT": "仓管提交复核",
    "FINANCE_APPROVE": "财务签核",
    "FINANCE_RETURN": "财务退回",
    "OPENING_SUBMIT": "提交期初",
    "OPENING_APPROVE": "签核期初",
    "OPENING_RETURN": "退回期初",
}


class DateInput(forms.DateInput):
    input_type = "date"


class CloseFilters(forms.Form):
    warehouse = forms.ChoiceField(label="仓库", required=False)
    owner = forms.ChoiceField(label="货主", required=False)
    start = forms.DateField(label="起始日期", required=False, widget=DateInput)
    end = forms.DateField(label="截止日期", required=False, widget=DateInput)
    quality = forms.ChoiceField(label="数据质量", required=False)
    review_state = forms.ChoiceField(label="复核状态", required=False)

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            self.add_error("end", "截止日期须在起始日期当天或之后。")
        return data


class PilotForm(forms.Form):
    warehouse = forms.ModelChoiceField(label="仓库", queryset=Warehouse.objects.none())
    start_date = forms.DateField(label="首个业务日", widget=DateInput)
    enabled = forms.BooleanField(label="启用试点", required=False)


class ManualRunForm(forms.Form):
    binding = forms.ModelChoiceField(
        label="货主 / 仓库", queryset=OwnerWarehouseBinding.objects.none()
    )
    business_date = forms.DateField(label="业务日", widget=DateInput)


class NoteForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    note = forms.CharField(
        label="复核意见", max_length=4000, widget=forms.Textarea(attrs={"rows": 3})
    )


class FinanceApprovalForm(NoteForm):
    billing_impact = forms.ChoiceField(
        label="计费影响",
        choices=[
            ("", "请选择"),
            ("NO_CHANGE", "无需账务处理"),
            ("FOLLOW_UP", "需按原流程核对账单或调整"),
        ],
    )
    note = forms.CharField(
        label="复核意见",
        help_text="说明计费影响、依据及后续处理。",
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class EvidenceForm(NoteForm):
    references = forms.CharField(
        label="关联单据或外部凭证",
        required=False,
        max_length=6000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "盘点单 123\n外部凭证 PD-001"}),
        help_text="每行填写类型和编号，中间用空格分隔。入库单、出库单、盘点单、调整单、账单、账期填写系统记录 ID，外部凭证填写档号。",
    )

    def clean_references(self):
        refs = []
        for index, line in enumerate(self.cleaned_data["references"].splitlines(), 1):
            if not line.strip():
                continue
            pieces = line.strip().split(maxsplit=1)
            if len(pieces) != 2 or pieces[0] not in REFERENCE_TYPES:
                raise ValidationError(f"第 {index} 行须填写单据类型及编号。")
            kind, value = REFERENCE_TYPES[pieces[0]], pieces[1]
            if kind == "external":
                refs.append({"type": kind, "number": value})
            elif value.isascii() and value.isdigit() and len(value) <= 18 and int(value) > 0:
                refs.append({"type": kind, "id": int(value)})
            else:
                raise ValidationError(f"第 {index} 行的系统记录 ID 须为正整数。")
        return refs


class FinanceReviewApprovalForm(FinanceApprovalForm, EvidenceForm):
    """A finance reviewer may also cite the affected bill, period or adjustment."""


class OpeningForm(EvidenceForm):
    candidate_digest = forms.CharField(widget=forms.HiddenInput, max_length=64)
    occupancy = forms.CharField(
        label="补录历史占用",
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="按上表行号，每行依次填写行号、预留、锁定、残损，以空格分隔，例如「1 2 0 0」。将下划线替换为凭证上的数量。请同时填写外部档号并上传签署的日终占用明细。",
    )
    complete = forms.BooleanField(
        label="我已逐项核对完整范围、日界过账及在库、预留、锁定和残损的依据。"
    )

    def __init__(self, *args, rows, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = [dict(row) for row in rows]
        self.missing = {i for i, row in enumerate(rows, 1) if row["allocated_qty"] is None}
        if self.missing:
            self.initial["occupancy"] = "\n".join(f"{i} _ _ _" for i in sorted(self.missing))
        else:
            del self.fields["occupancy"]

    def clean_occupancy(self):
        lines = self.cleaned_data["occupancy"].splitlines()
        quantity = forms.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal(0))
        seen = set()
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if (
                len(parts) != 4
                or not parts[0].isascii()
                or not parts[0].isdigit()
                or len(parts[0]) > 10
            ):
                raise ValidationError("每行须包含有效行号和三个明确的占用数量。")
            index = int(parts[0])
            if index not in self.missing or index in seen:
                raise ValidationError(f"行号 {index} 重复或不在待补录范围。")
            row = self.rows[index - 1]
            try:
                held = [quantity.clean(value) for value in parts[1:]]
            except ValidationError as exc:
                raise ValidationError(f"行号 {index} 的数量须为非负数，最多四位小数。") from exc
            available = Decimal(row["onhand_qty"]) - sum(held)
            if available < 0:
                raise ValidationError(f"行号 {index} 的预留、锁定和残损合计超过期末在库。")
            for name, value in zip(("allocated_qty", "locked_qty", "damaged_qty"), held):
                row[name] = str(value)
            row["available_qty"] = str(available)
            seen.add(index)
        if seen != self.missing:
            raise ValidationError("请补齐全部待核维度的占用数量。")
        return self.cleaned_data["occupancy"]
