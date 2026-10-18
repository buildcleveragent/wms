#!/usr/bin/env python3
"""Read-only acceptance checks for generated manuals and specialist guides.

Run from the repository root with ``.venv/bin/python docs/validate_user_manuals.py``.
No Django settings or database connection is used.
"""

from __future__ import annotations

import io
import posixpath
import re
import subprocess
import sys
import urllib.parse
import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree as ET

import mistune
from bs4 import BeautifulSoup
from build_user_manual import (
    DOCS_DIR,
    MANUALS_DIR,
    build_word_html,
    compose_index,
    compose_targets,
    load_manifest,
    render_html,
)
from PIL import Image

SPECIALIST_GUIDES = (
    "pos_user_manual.md",
    "pda-return-user-guide.md",
    "android-user-guide.md",
    "sales-miniapp-user-guide.md",
    "billing_services_guide.md",
)
SUPPORT_FILES = ("README.md", "BASELINE.md", "ROLE_MATRIX.md", "CONTENT_REVIEW.md", "VALIDATION.md")
REQUIRED_SECTIONS = (
    "登录、账号与通用安全",
    "常见状态与控制点",
    "异常处理与问题上报",
    "日常自检与培训验收",
)
BANNED_PATTERNS = (
    r"\bTODO\b",
    r"待补(?:充|截图)",
    r"截图位",
    r"仓库管理员一键确认.*会连续完成货主",
    r"当前已通过.*(?:测试|验收)",
    r"(?:部分退款退货|部分退款、部分退货).*(?:后续扩展|暂不支持)",
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
VML_NS = "urn:schemas-microsoft-com:vml"
MARKDOWN = mistune.create_markdown(plugins=["table", "task_lists"])


def compact(text: str) -> str:
    return re.sub(r"[\s\u200b\u00ad]+", "", text.replace("☐", "□"))


def markdown_soup(text: str) -> BeautifulSoup:
    return BeautifulSoup(MARKDOWN(text), "html.parser")


def markdown_anchors(text: str) -> set[str]:
    """Resolve GitHub-style heading fragments, including duplicate headings."""
    soup = markdown_soup(text)
    anchors = {str(node["id"]) for node in soup.select("[id]")}
    counts: dict[str, int] = {}
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        base = re.sub(r"[^\w\- ]", "", heading.get_text().lower()).replace(" ", "-")
        number = counts.get(base, 0)
        counts[base] = number + 1
        anchors.add(f"{base}-{number}" if number else base)
    return anchors


def check_links(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    soup = markdown_soup(text) if path.suffix == ".md" else BeautifulSoup(text, "html.parser")
    for node in soup.select("a[href], img[src]"):
        url = str(node.get("href") if node.name == "a" else node.get("src"))
        parts = urllib.parse.urlsplit(url)
        if parts.scheme in {"http", "https", "mailto", "data"}:
            continue  # Remote reachability is deliberately outside this offline check.
        if parts.scheme or parts.netloc:
            errors.append(f"unsupported local link {url}: {path}")
            continue
        relative = urllib.parse.unquote(parts.path)
        target = (path.parent / relative).resolve() if relative else path.resolve()
        if not target.exists():
            errors.append(f"missing link/image {url}: {path}")
            continue
        anchor = urllib.parse.unquote(parts.fragment)
        if not anchor or node.name == "img":
            continue
        if target.suffix in {".md", ".html"}:
            target_text = target.read_text(encoding="utf-8")
            if target.suffix == ".md":
                anchors = markdown_anchors(target_text)
            else:
                anchors = {
                    str(tag["id"])
                    for tag in BeautifulSoup(target_text, "html.parser").select("[id]")
                }
            if anchor not in anchors:
                errors.append(f"missing heading anchor {url}: {path}")
        elif re.fullmatch(r"L\d+(?:-L\d+)?", anchor) and target.is_file():
            last_line = int(anchor.rsplit("L", 1)[-1])
            if last_line > len(target.read_text(encoding="utf-8").splitlines()):
                errors.append(f"source line outside file {url}: {path}")


def check_metadata(text: str, path: Path, manifest: dict, errors: list[str]) -> None:
    for label in (manifest["version"], manifest["baseline_date"]):
        if compact(label) not in compact(text):
            errors.append(f"missing current metadata {label}: {path}")


def check_markdown(path: Path, manifest: dict, errors: list[str], *, role: bool = False) -> None:
    if not path.is_file():
        errors.append(f"missing Markdown: {path}")
        return
    text = path.read_text(encoding="utf-8")
    header = re.split(r"^## ", text, maxsplit=1, flags=re.M)[0]
    check_metadata(header, path, manifest, errors)
    if role:
        for section in REQUIRED_SECTIONS:
            if section not in text:
                errors.append(f"missing section {section}: {path}")
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            errors.append(f"placeholder or obsolete claim /{pattern}/: {path}")
    check_links(path, errors)


def check_equal(path: Path, expected: str, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing generated file: {path}")
    elif path.read_text(encoding="utf-8") != expected:
        errors.append(f"generated file differs from current sources; rebuild: {path}")


def check_headings(text: str, headings: list[str], path: Path, errors: list[str]) -> None:
    normalized = compact(text)
    for heading in headings:
        if compact(heading) not in normalized:
            errors.append(f"missing rendered heading {heading}: {path}")


def markdown_body_blocks(text: str) -> list[str]:
    """Collect prose and table cells independently of the generated table of contents."""
    soup = markdown_soup(text)
    blocks = []
    for node in soup.find_all(["p", "li", "th", "td", "pre"]):
        # A nested list/paragraph is checked independently; do not include it twice.
        if node.find(["p", "li", "th", "td", "pre"]):
            clone = BeautifulSoup(str(node), "html.parser").find(node.name)
            for child in clone.find_all(["p", "ul", "ol", "table", "pre"]):
                child.decompose()
            value = clone.get_text()
        else:
            value = node.get_text()
        if compact(value):
            blocks.append(value)
    return list(dict.fromkeys(blocks))


def check_body(text: str, blocks: Sequence[str], path: Path, errors: list[str]) -> None:
    normalized = compact(text)
    for block in blocks:
        if compact(block) not in normalized:
            excerpt = re.sub(r"\s+", " ", block).strip()[:90]
            errors.append(f"missing rendered body content {excerpt}: {path}")


def check_docx(
    path: Path,
    manifest: dict,
    headings: list[str],
    errors: list[str],
    *,
    body_blocks: Sequence[str] = (),
    expected_images: int = 1,
) -> None:
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            root = ET.fromstring(package.read("word/document.xml"))
            text = "".join(node.text or "" for node in root.iter(f"{{{WORD_NS}}}t"))
            check_metadata(text, path, manifest, errors)
            check_headings(text, headings, path, errors)
            check_body(text, body_blocks, path, errors)
            rel_root = ET.fromstring(package.read("word/_rels/document.xml.rels"))
            image_relationships = {}
            for node in rel_root.findall(f"{{{REL_NS}}}Relationship"):
                if not node.get("Type", "").endswith("/image"):
                    continue
                if node.get("TargetMode") == "External":
                    errors.append(f"DOCX has external image links: {path}")
                    continue
                target = urllib.parse.unquote(node.get("Target", ""))
                media = posixpath.normpath(
                    target.lstrip("/") if target.startswith("/") else f"word/{target}"
                )
                if media not in names:
                    errors.append(f"DOCX image relationship is broken: {path}: {media}")
                    continue
                image_relationships[node.get("Id")] = media
            references = []
            for node in root.iter():
                if node.tag == f"{{{DRAWING_NS}}}blip":
                    references.append(node.get(f"{{{DOCUMENT_REL_NS}}}embed"))
                elif node.tag == f"{{{VML_NS}}}imagedata":
                    references.append(node.get(f"{{{DOCUMENT_REL_NS}}}id"))
            if len(references) < expected_images:
                errors.append(
                    f"DOCX missing referenced images: expected {expected_images}, "
                    f"found {len(references)}: {path}"
                )
            for reference in set(references):
                media = image_relationships.get(reference)
                if not media:
                    errors.append(f"DOCX drawing has no embedded image relationship: {path}")
                    continue
                try:
                    with Image.open(io.BytesIO(package.read(media))) as image:
                        image.verify()
                except (OSError, ValueError, SyntaxError) as exc:
                    errors.append(f"DOCX referenced image is invalid: {path}: {media}: {exc}")
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        errors.append(f"cannot inspect DOCX {path}: {exc}")


def pdf_tool(args: list[str], errors: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"PDF check unavailable {args[0]}: {exc}")
        return ""
    if result.returncode:
        errors.append(f"PDF check failed {args[0]}: {result.stderr.strip()}")
        return ""
    return result.stdout


def pdf_body_text(text: str, manifest: dict, headings: list[str]) -> str:
    """Remove running matter that may interrupt a paragraph at a page boundary."""
    edition = compact(f"{manifest['version']} · {manifest['baseline_date']}")
    title = compact(headings[0]) if headings else ""
    lines = []
    for line in text.splitlines():
        value = compact(line)
        if value in {edition, title} or re.fullmatch(r"第\d+页/共\d+页", value):
            continue
        lines.append(line)
    return "\n".join(lines)


def check_pdf(
    path: Path,
    manifest: dict,
    headings: list[str],
    errors: list[str],
    *,
    body_blocks: Sequence[str] = (),
) -> None:
    info = pdf_tool(["pdfinfo", str(path)], errors)
    if not info:
        return
    count = re.search(r"^Pages:\s+([1-9]\d*)\s*$", info, flags=re.M)
    if not count:
        errors.append(f"PDF must contain pages: {path}")
        return
    page_count = int(count.group(1))
    sizes = pdf_tool(["pdfinfo", "-f", "1", "-l", str(page_count), str(path)], errors)
    page_sizes = re.findall(
        r"^Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts", sizes, flags=re.M
    )
    if {int(number) for number, _, _ in page_sizes} != set(range(1, page_count + 1)):
        errors.append(f"PDF page dimensions incomplete: {path}")
    for number, width, height in page_sizes:
        if abs(float(width) - 595.276) > 1 or abs(float(height) - 841.890) > 1:
            errors.append(f"PDF page {number} is not portrait A4: {path}")
    text = pdf_tool(["pdftotext", "-raw", str(path), "-"], errors)
    check_metadata(text, path, manifest, errors)
    check_headings(text, headings, path, errors)
    check_body(pdf_body_text(text, manifest, headings), body_blocks, path, errors)
    if not re.search(r"[\u4e00-\u9fff]", text):
        errors.append(f"PDF contains no extractable Chinese text: {path}")


def main() -> int:
    manifest, specs = load_manifest()
    errors: list[str] = []
    targets = compose_targets(manifest, specs)
    for target in targets:
        stem = target.output_stem
        markdown = stem.with_suffix(".md")
        check_equal(markdown, target.markdown, errors)
        check_markdown(markdown, manifest, errors, role=True)
        document = render_html(
            target, version=manifest["version"], baseline_date=manifest["baseline_date"]
        )
        html_path = stem.with_suffix(".html")
        check_equal(html_path, document, errors)
        if html_path.is_file():
            check_links(html_path, errors)
        word_html = stem.with_name(stem.name + "_Word源.html")
        check_equal(word_html, build_word_html(document, base_dir=stem.parent), errors)
        headings = [
            heading.get_text(" ", strip=True)
            for heading in markdown_soup(target.markdown).find_all(["h1", "h2", "h3"])
        ]
        body_blocks = markdown_body_blocks(target.markdown)
        check_docx(
            stem.with_suffix(".docx"),
            manifest,
            headings,
            errors,
            body_blocks=body_blocks,
            expected_images=len(BeautifulSoup(document, "html.parser").find_all("img")),
        )
        check_pdf(stem.with_suffix(".pdf"), manifest, headings, errors, body_blocks=body_blocks)
    for name in SPECIALIST_GUIDES:
        check_markdown(DOCS_DIR / name, manifest, errors)
    for name in SUPPORT_FILES:
        path = MANUALS_DIR / name
        if not path.is_file():
            errors.append(f"missing support document: {path}")
        else:
            check_links(path, errors)
            check_metadata(path.read_text(encoding="utf-8"), path, manifest, errors)
    check_equal(MANUALS_DIR / "README.md", compose_index(manifest, specs), errors)
    if errors:
        print("Manual validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"Validated {len(targets)} manual sets (Markdown, HTML, Word HTML, embedded DOCX, A4 PDF), "
        f"{len(SPECIALIST_GUIDES)} specialist guides and {len(SUPPORT_FILES)} support documents; "
        "source consistency, edition metadata, body coverage, image references, "
        "every PDF page size, headings and local links checked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
