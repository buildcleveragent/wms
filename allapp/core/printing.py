from __future__ import annotations

import re

_LENGTH_RE = re.compile(
    r"^(?:0|(?:\d+(?:\.\d+)?|\.\d+)(?:px|pt|mm|cm|in|em|rem|%))$",
    re.IGNORECASE,
)
_NAMED_PAGE_RE = re.compile(
    r"^(?:a[3-6]|letter|legal|tabloid)(?:\s+(?:portrait|landscape))?$",
    re.IGNORECASE,
)
_FONT_NAME_RE = re.compile(r"^[\w -]+$", re.UNICODE)


def _text(value) -> str:
    return str(value or "").strip()


def _length(value) -> str | None:
    value = _text(value)
    return value if _LENGTH_RE.fullmatch(value) else None


def _box(value) -> str | None:
    parts = _text(value).split()
    if 1 <= len(parts) <= 4 and all(_LENGTH_RE.fullmatch(part) for part in parts):
        return " ".join(parts)
    return None


def _page_size(value) -> str | None:
    value = _text(value)
    if _NAMED_PAGE_RE.fullmatch(value):
        return value
    parts = value.split()
    if 1 <= len(parts) <= 2 and all(_LENGTH_RE.fullmatch(part) for part in parts):
        return " ".join(parts)
    return None


def _line_height(value) -> str | None:
    value = _text(value)
    if _LENGTH_RE.fullmatch(value):
        return value
    try:
        return value if float(value) > 0 else None
    except (TypeError, ValueError):
        return None


def _font_family(value) -> str | None:
    families = [part.strip() for part in _text(value).split(",")]
    if not families or any(
        not family or not _FONT_NAME_RE.fullmatch(family) for family in families
    ):
        return None
    return ", ".join(families)


_NORMALIZERS = {
    "page_size_css": _page_size,
    "page_margin": _box,
    "sheet_width": _length,
    "sheet_padding_top": _length,
    "sheet_padding_right": _length,
    "sheet_padding_bottom": _length,
    "sheet_padding_left": _length,
    "font_family": _font_family,
    "body_font_size": _length,
    "company_font_size": _length,
    "title_font_size": _length,
    "meta_font_size": _length,
    "table_font_size": _length,
    "table_header_font_size": _length,
    "money_font_size": _length,
    "footer_font_size": _length,
    "body_line_height": _line_height,
    "meta_line_height": _line_height,
    "table_line_height": _line_height,
    "money_line_height": _line_height,
    "footer_line_height": _line_height,
    "table_cell_padding": _box,
}


def normalize_print_css(config, defaults: dict[str, str]) -> dict[str, str]:
    """Return CSS-safe print settings, falling back one field at a time."""

    normalized = {}
    for field, fallback in defaults.items():
        normalizer = _NORMALIZERS[field]
        value = getattr(config, field, None) if config is not None else None
        normalized[field] = normalizer(value) or fallback
    return normalized
