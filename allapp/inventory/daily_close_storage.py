"""Private supporting documents for inventory close reviews."""

import os
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from PIL import Image, UnidentifiedImageError

KEY_PATTERN = re.compile(r"[1-9][0-9]*/[0-9a-f]{32}\.(?:jpeg|png|pdf)")
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 5
FORMATS = {
    ".pdf": ("pdf", "application/pdf"),
    ".jpg": ("jpeg", "image/jpeg"),
    ".jpeg": ("jpeg", "image/jpeg"),
    ".png": ("png", "image/png"),
}


@dataclass(frozen=True)
class PreparedFile:
    file_name: str
    extension: str
    content_type: str
    data: bytes


def private_close_root():
    root = Path(getattr(settings, "PRIVATE_CLOSE_ROOT", settings.BASE_DIR / "private_close"))
    root = root.resolve()
    public_paths = [settings.MEDIA_ROOT, settings.STATIC_ROOT]
    public_paths += [
        value[1] if isinstance(value, (list, tuple)) else value
        for value in settings.STATICFILES_DIRS
    ]
    for public in filter(None, public_paths):
        public = Path(public).resolve()
        if root.is_relative_to(public) or public.is_relative_to(root):
            raise ImproperlyConfigured("日结凭据目录必须与公开媒体和静态目录分开。")
    return root


def evidence_path(storage_key, *, close_id=None):
    if not isinstance(storage_key, str) or not KEY_PATTERN.fullmatch(storage_key):
        raise ValidationError("日结凭据路径无效。")
    if close_id is not None and storage_key.partition("/")[0] != str(close_id):
        raise ValidationError("凭据不属于此日结。")
    path = private_close_root() / storage_key
    if path.parent.is_symlink() or path.is_symlink():
        raise ValidationError("日结凭据路径无效。")
    return path


def prepare_files(files):
    prepared = []
    for upload in files:
        if len(prepared) == MAX_FILES:
            raise ValidationError(f"每次最多上传 {MAX_FILES} 个凭据。")
        name = Path(str(upload.name).replace("\\", "/")).name
        if not name or len(name) > 180 or any(not ch.isprintable() for ch in name):
            raise ValidationError("凭据文件名无效。")
        kind = FORMATS.get(Path(name).suffix.lower())
        if kind is None:
            raise ValidationError("凭据支持 PDF、JPEG 和 PNG。")
        extension, mime = kind
        if upload.size > MAX_FILE_BYTES:
            raise ValidationError("单个凭据最大 5 MiB。")
        data = upload.read(MAX_FILE_BYTES + 1)
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValidationError("凭据为空或超过 5 MiB。")
        if extension == "pdf":
            # A PDF points from its EOF marker to a cross-reference table or stream.
            trailer = re.search(rb"startxref\s+([0-9]{1,10})\s+%%EOF\s*\Z", data[-1024:])
            if not re.match(rb"%PDF-[12]\.\d", data) or not trailer:
                raise ValidationError("凭据内容与 PDF 格式不符。")
            offset = int(trailer[1])
            if not re.match(rb"(?:xref\b|\d+\s+\d+\s+obj\b)", data[offset : offset + 64]):
                raise ValidationError("凭据内容与 PDF 格式不符。")
        else:
            try:
                with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as img:
                    if img.format.lower() != extension or img.width * img.height > 24_000_000:
                        raise ValidationError("凭据图片格式或尺寸不符。")
                    img.verify()
                with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as img:
                    img.load()
            except (
                OSError,
                ValueError,
                SyntaxError,
                UnidentifiedImageError,
                Image.DecompressionBombError,
            ):
                raise ValidationError("凭据图片内容无效。") from None
        prepared.append(PreparedFile(name, extension, mime, data))
    return prepared


def write_evidence(close_id, prepared_file):
    root = private_close_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = f"{close_id}/{uuid.uuid4().hex}.{prepared_file.extension}"
    path = evidence_path(key, close_id=close_id)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(prepared_file.data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return key
