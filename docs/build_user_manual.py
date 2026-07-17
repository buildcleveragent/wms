#!/usr/bin/env python3
"""Build the WMS user manual HTML and PDF from the Markdown source."""

from __future__ import annotations

import html
import re
from pathlib import Path

import mistune
from bs4 import BeautifulSoup
from PIL import Image
from weasyprint import HTML


DOCS_DIR = Path(__file__).resolve().parent
SOURCE = DOCS_DIR / "金桥融通WMS操作手册.md"
HTML_OUTPUT = DOCS_DIR / "金桥融通WMS操作手册.html"
WORD_HTML_OUTPUT = DOCS_DIR / "金桥融通WMS操作手册_Word源.html"
PDF_OUTPUT = DOCS_DIR / "金桥融通WMS操作手册.pdf"


STYLE = r"""
@page {
  size: A4;
  margin: 18mm 16mm 19mm 16mm;
  @top-left {
    content: "金桥融通仓库管理系统（WMS）操作手册";
    color: #667085;
    font-size: 8.5pt;
  }
  @top-right {
    content: "V1.0 · 2026-07";
    color: #667085;
    font-size: 8.5pt;
  }
  @bottom-center {
    content: "第 " counter(page) " 页 / 共 " counter(pages) " 页";
    color: #7a8493;
    font-size: 8.5pt;
  }
}
@page cover {
  margin: 0;
  @top-left { content: none; }
  @top-right { content: none; }
  @bottom-center { content: none; }
}
* { box-sizing: border-box; }
html { font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; color: #172033; }
body { margin: 0; font-size: 10pt; line-height: 1.7; }
.cover {
  page: cover;
  page-break-after: always;
  height: 297mm;
  padding: 43mm 26mm 25mm;
  color: white;
  background: linear-gradient(150deg, #0f2f68 0%, #155eef 58%, #65a5ff 100%);
  position: relative;
}
.cover::after {
  content: "";
  position: absolute;
  right: -44mm;
  bottom: -42mm;
  width: 150mm;
  height: 150mm;
  border-radius: 50%;
  background: rgba(255,255,255,.10);
}
.cover-logo { width: 23mm; height: 23mm; object-fit: contain; margin-bottom: 25mm; }
.cover h1 { font-size: 28pt; line-height: 1.28; margin: 0 0 12mm; color: white; }
.cover .lead { font-size: 14pt; color: #e8f1ff; margin-bottom: 28mm; }
.cover-meta { border-top: 1px solid rgba(255,255,255,.45); padding-top: 9mm; width: 118mm; }
.cover-meta div { margin: 2.2mm 0; font-size: 10.5pt; }
.cover-foot { position: absolute; left: 26mm; bottom: 25mm; color: #eaf2ff; font-size: 9pt; }
.toc { page-break-after: always; }
.toc h1 { color: #0f2f68; font-size: 23pt; border-bottom: 3px solid #155eef; padding-bottom: 4mm; }
.toc ul { margin: 0; padding-left: 0; list-style: none; }
.toc li { margin: 1.6mm 0; }
.toc li.level-3 { margin-left: 7mm; color: #4d5c73; font-size: 9.2pt; }
.toc a { color: inherit; text-decoration: none; }
h2 { page-break-before: always; font-size: 20pt; color: #0f2f68; margin: 0 0 7mm; border-bottom: 2px solid #bfd2f6; padding-bottom: 3mm; }
h2.first-section { page-break-before: auto; }
h3 { font-size: 14pt; color: #174a98; margin: 8mm 0 3mm; page-break-after: avoid; }
h4 { font-size: 11.5pt; color: #243b63; margin: 5mm 0 2mm; page-break-after: avoid; }
p { margin: 2.4mm 0; orphans: 3; widows: 3; }
ul, ol { margin: 2.2mm 0 3mm; padding-left: 7mm; }
li { margin: 1.2mm 0; }
strong { color: #122c57; }
code { font-family: "Noto Sans Mono CJK SC", monospace; background: #eef3fb; padding: .2mm 1mm; border-radius: 1mm; }
blockquote { margin: 4mm 0; padding: 3.5mm 5mm; border-left: 4px solid #3274dd; background: #f2f6fd; color: #35445d; }
blockquote p { margin: 0; }
hr { border: 0; border-top: 1px solid #d7dee9; margin: 7mm 0; }
table { width: 100%; border-collapse: collapse; margin: 4mm 0 6mm; font-size: 8.5pt; page-break-inside: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th { background: #eaf1fd; color: #143a75; font-weight: 700; }
th, td { border: 1px solid #bdc9da; padding: 2.2mm 2.4mm; vertical-align: top; }
img { display: block; max-width: 100%; height: auto; margin: 5mm auto 7mm; page-break-inside: avoid; }
img.role-map { width: 96%; }
img.mobile-shot { width: 62mm; border: 1px solid #ccd5e2; border-radius: 3mm; box-shadow: 0 2mm 7mm rgba(20,40,80,.10); }
img.web-shot { width: 150mm; border: 1px solid #ccd5e2; box-shadow: 0 2mm 7mm rgba(20,40,80,.10); }
.caption { text-align: center; color: #68758a; font-size: 8.5pt; margin-top: -5mm; margin-bottom: 6mm; }
.document-note { margin: 0 0 7mm; padding: 3mm 4mm; color: #34445d; background: #f6f8fb; border: 1px solid #d8e0eb; }
.checkbox { font-family: "Noto Sans Symbols 2", "Noto Sans CJK SC", sans-serif; }
"""


def slugify(text: str, index: int) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return f"section-{index}-{cleaned}" if cleaned else f"section-{index}"


def build_html() -> str:
    markdown = mistune.create_markdown(plugins=["table", "task_lists"])
    rendered = markdown(SOURCE.read_text(encoding="utf-8"))
    soup = BeautifulSoup(rendered, "html.parser")

    first_h1 = soup.find("h1")
    if first_h1:
        first_h1.extract()

    # Remove the document-control paragraphs from the body; they are rendered on the cover.
    for _ in range(5):
        node = soup.find(["p", "hr"])
        if not node:
            break
        if node.name == "hr":
            node.extract()
            break
        node.extract()

    toc_items: list[tuple[int, str, str]] = []
    for index, heading in enumerate(soup.find_all(["h2", "h3"]), start=1):
        heading_id = slugify(heading.get_text(" ", strip=True), index)
        heading["id"] = heading_id
        toc_items.append((int(heading.name[1]), heading_id, heading.get_text(" ", strip=True)))

    for image in soup.find_all("img"):
        alt = image.get("alt", "界面截图")
        src = image.get("src", "")
        if "role-entry-map" in src:
            image["class"] = ["role-map"]
        elif "web-login" in src:
            image["class"] = ["web-shot"]
        else:
            image["class"] = ["mobile-shot"]
        caption = soup.new_tag("p")
        caption["class"] = ["caption"]
        caption.string = f"图：{alt}"
        image.insert_after(caption)

    for empty_quote in soup.find_all("blockquote"):
        if not empty_quote.get_text(strip=True):
            empty_quote.decompose()

    first_section = soup.find("h2")
    if first_section:
        first_section["class"] = ["first-section"]

    toc_lines = ["<section class='toc'><h1>目录</h1><ul>"]
    for level, heading_id, title in toc_items:
        toc_lines.append(
            f"<li class='level-{level}'><a href='#{html.escape(heading_id)}'>{html.escape(title)}</a></li>"
        )
    toc_lines.append("</ul></section>")

    cover = """
    <section class="cover">
      <img class="cover-logo" src="../static/img/aaa.png" alt="金桥融通" />
      <h1>金桥融通仓库管理系统<br />（WMS）操作手册</h1>
      <div class="lead">仓储交付 · 六角色岗位操作与异常处理</div>
      <div class="cover-meta">
        <div>文档版本：V1.0</div>
        <div>适用版本：2026 年 7 月交付基线</div>
        <div>文档状态：交付版</div>
        <div>编制日期：2026 年 7 月 16 日</div>
      </div>
      <div class="cover-foot">适用角色：仓库操作员 · 仓库管理员 · 货主业务员 · 货主管理员 · 系统管理员 · 老板</div>
    </section>
    """

    body = str(soup).replace("☐", "□")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>金桥融通仓库管理系统（WMS）操作手册</title>
  <style>{STYLE}</style>
</head>
<body>
{cover}
{''.join(toc_lines)}
<div class="document-note">本手册以当前可验收功能为准。实际菜单由账号角色、所属货主和所属仓库共同决定。</div>
{body}
</body>
</html>"""


def build_word_html(document: str) -> str:
    """Add inline, Writer-friendly sizing and colors to the HTML source."""
    soup = BeautifulSoup(document, "html.parser")
    cover = soup.select_one(".cover")
    if cover:
        cover["style"] = (
            "page-break-after:always; min-height:900px; padding:70px 60px; "
            "color:#17355f; background-color:#eef4ff; position:relative;"
        )
        title = cover.find("h1")
        if title:
            title["style"] = "color:#0f2f68; font-size:34px; line-height:1.35;"
        lead = cover.select_one(".lead")
        if lead:
            lead["style"] = "color:#355a8f; font-size:20px; margin-bottom:55px;"
        meta = cover.select_one(".cover-meta")
        if meta:
            meta["style"] = (
                "color:#334968; border-top:1px solid #9fb8db; "
                "padding-top:24px; width:520px;"
            )
        foot = cover.select_one(".cover-foot")
        if foot:
            foot["style"] = (
                "position:static; margin-top:120px; color:#536b8d; font-size:13px;"
            )

    cover_logo = soup.select_one(".cover-logo")
    if cover_logo:
        cover_logo["width"] = "96"
        cover_logo["height"] = "96"
        cover_logo["style"] = "width:96px; height:96px; object-fit:contain; margin-bottom:70px;"

    for image in soup.find_all("img"):
        classes = set(image.get("class", []))
        if "role-map" in classes:
            image["src"] = "manual_assets/role-entry-map-word.png"
            image["width"] = "600"
            image["style"] = "width:600px; height:auto;"
        elif "mobile-shot" in classes:
            source = Path(image.get("src", ""))
            image["src"] = str(source.with_name(f"{source.stem}-word.png"))
            image["width"] = "280"
            image["style"] = "width:280px; height:auto;"
        elif "web-shot" in classes:
            image["src"] = "manual_assets/web-login-word.png"
            image["width"] = "600"
            image["style"] = "width:600px; height:auto;"

    return str(soup)


def build_word_images() -> None:
    assets = DOCS_DIR / "manual_assets"
    variants = (
        ("role-entry-map.png", "role-entry-map-word.png", 600),
        ("pda-login.png", "pda-login-word.png", 280),
        ("boss-login.png", "boss-login-word.png", 280),
        ("web-login.png", "web-login-word.png", 600),
    )
    for source_name, target_name, max_width in variants:
        with Image.open(assets / source_name) as source:
            source.load()
            ratio = min(1.0, max_width / source.width)
            size = (max(1, round(source.width * ratio)), max(1, round(source.height * ratio)))
            resized = source.resize(size, Image.Resampling.LANCZOS)
            resized.save(assets / target_name, format="PNG", optimize=True)


def main() -> None:
    document = build_html()
    HTML_OUTPUT.write_text(document, encoding="utf-8")
    build_word_images()
    WORD_HTML_OUTPUT.write_text(build_word_html(document), encoding="utf-8")
    HTML(filename=str(HTML_OUTPUT), base_url=str(DOCS_DIR)).write_pdf(str(PDF_OUTPUT))
    print(f"Wrote {HTML_OUTPUT}")
    print(f"Wrote {WORD_HTML_OUTPUT}")
    print(f"Wrote {PDF_OUTPUT}")


if __name__ == "__main__":
    main()
