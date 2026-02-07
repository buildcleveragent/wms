from decimal import Decimal, InvalidOperation
from django import forms

class TrimDecimalWidget(forms.NumberInput):
    """
    展示时去掉无意义的尾随0；0.000 -> 0，12.340 -> 12.34。
    仅影响UI渲染，不改变提交/校验与存储。
    """
    def format_value(self, value):
        if value in (None, ''):
            return ''
        try:
            d = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return super().format_value(value)
        # normalize 可能变科学计数法，用 'f' 固定为常规小数
        s = format(d.normalize(), 'f')
        return s or '0'
