# core/utils/normalizers.py
from typing import Any

def strip_to_none(v: Any) -> Any:
    """字符串去空白；若为空串返回 None；其他类型原样返回。"""
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v
