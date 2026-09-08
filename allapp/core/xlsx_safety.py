"""Bound XLSX input before openpyxl expands and parses the archive."""

import io
import zipfile

MAX_COMPRESSED_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 300


def validate_xlsx_archive(data):
    """Return validated bytes or raise ValueError before workbook parsing."""
    if not data or len(data) > MAX_COMPRESSED_BYTES:
        raise ValueError("Excel 文件为空或超过 5 MB。")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError("Excel 文件条目过多。")
            if sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("Excel 解压后内容超过 50 MB。")
            if any(item.flag_bits & 1 for item in entries):
                raise ValueError("不支持加密 Excel 文件。")
            if archive.testzip() is not None:
                raise ValueError("Excel 文件已损坏。")
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("Excel 文件无法解析。") from exc
    return data
