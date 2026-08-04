#!/usr/bin/env python3
"""Static acceptance checks for generated WMS role manuals."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


DOCS_DIR = Path(__file__).resolve().parent
MANUALS_DIR = DOCS_DIR / "manuals"
MANIFEST_PATH = MANUALS_DIR / "manuals_manifest.json"
CONSOLIDATED_STEM = DOCS_DIR / "金桥融通WMS操作手册"
FORMATS = (".md", ".docx", ".pdf")
REQUIRED_SECTIONS = (
    "登录、账号与通用安全",
    "常见状态与控制点",
    "异常处理与问题上报",
    "日常自检与培训验收",
)
BANNED_PATTERNS = (
    r"TODO",
    r"待补(?:充|截图)",
    r"截图位",
    r"仓库管理员一键确认.*会连续完成货主",
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def check_markdown(path: Path, *, version: str, group: str | None, errors: list[str]) -> None:
    if not path.is_file():
        fail(errors, f"missing Markdown: {path}")
        return
    content = path.read_text(encoding="utf-8")
    if version not in content:
        fail(errors, f"missing version {version}: {path}")
    if group and group not in content:
        fail(errors, f"missing role group {group}: {path}")
    for section in REQUIRED_SECTIONS:
        if section not in content:
            fail(errors, f"missing section {section}: {path}")
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, content, flags=re.I):
            fail(errors, f"banned placeholder or obsolete wording /{pattern}/: {path}")
    for image in re.findall(r"!\[[^]]*]\(([^)]+)\)", content):
        if image.startswith(("http://", "https://", "data:")):
            continue
        resolved = (path.parent / urllib.parse.unquote(image)).resolve()
        if not resolved.is_file():
            fail(errors, f"missing image {image}: {path}")


def check_docx(path: Path, errors: list[str]) -> None:
    if not path.is_file():
        fail(errors, f"missing DOCX: {path}")
        return
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            if "word/document.xml" not in names:
                fail(errors, f"invalid DOCX package: {path}")
                return
            rel_name = "word/_rels/document.xml.rels"
            rel_root = ET.fromstring(package.read(rel_name))
            external_images = [
                node
                for node in rel_root.findall(f"{{{REL_NS}}}Relationship")
                if node.get("Type", "").endswith("/image")
                and node.get("TargetMode") == "External"
            ]
            if external_images:
                fail(errors, f"DOCX still has external image links: {path}")
            if not any(name.startswith("word/media/") for name in names):
                fail(errors, f"DOCX has no embedded media: {path}")
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        fail(errors, f"cannot inspect DOCX {path}: {exc}")


def check_pdf(path: Path, errors: list[str]) -> None:
    if not path.is_file():
        fail(errors, f"missing PDF: {path}")
        return
    result = subprocess.run(
        ["pdfinfo", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        fail(errors, f"pdfinfo failed for {path}: {result.stderr.strip()}")
        return
    if "Pages:" not in result.stdout or "Page size:" not in result.stdout:
        fail(errors, f"PDF metadata incomplete: {path}")
    if "A4" not in result.stdout:
        fail(errors, f"PDF is not reported as A4: {path}")


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    errors: list[str] = []
    for support_file in ("README.md", "BASELINE.md", "ROLE_MATRIX.md"):
        if not (MANUALS_DIR / support_file).is_file():
            fail(errors, f"missing support document: {support_file}")
    manual_stems = [MANUALS_DIR / item["output"] for item in manifest["manuals"]]
    all_stems = manual_stems + [CONSOLIDATED_STEM]

    for item, stem in zip(manifest["manuals"], manual_stems):
        for suffix in FORMATS:
            if not stem.with_suffix(suffix).is_file():
                fail(errors, f"missing deliverable: {stem.with_suffix(suffix)}")
        check_markdown(
            stem.with_suffix(".md"),
            version=manifest["version"],
            group=item["group"],
            errors=errors,
        )

    check_markdown(
        CONSOLIDATED_STEM.with_suffix(".md"),
        version=manifest["version"],
        group=None,
        errors=errors,
    )
    consolidated = CONSOLIDATED_STEM.with_suffix(".md").read_text(encoding="utf-8")
    for item in manifest["manuals"]:
        if item["label"] not in consolidated or item["group"] not in consolidated:
            fail(errors, f"consolidated manual is missing {item['label']} role metadata")

    for stem in all_stems:
        check_docx(stem.with_suffix(".docx"), errors)
        check_pdf(stem.with_suffix(".pdf"), errors)

    if errors:
        print("Manual validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(all_stems)} manual sets: Markdown, DOCX and A4 PDF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
