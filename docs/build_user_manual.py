#!/usr/bin/env python3
"""Build the consolidated WMS manual and role-specific manuals.

Markdown deliverables are composed from shared and role-specific sources in
``docs/manuals/src``. HTML, PDF and self-contained DOCX files are then rendered
from those generated Markdown files.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mistune
from bs4 import BeautifulSoup
from PIL import Image
from weasyprint import HTML

from embed_docx_images import embed_images


DOCS_DIR = Path(__file__).resolve().parent
MANUALS_DIR = DOCS_DIR / "manuals"
SOURCE_DIR = MANUALS_DIR / "src"
MANIFEST_PATH = MANUALS_DIR / "manuals_manifest.json"
CONSOLIDATED_STEM = "金桥融通WMS操作手册"


STYLE = r"""
@page {
  size: A4;
  margin: 18mm 16mm 19mm 16mm;
  @top-left { content: string(document-title); color: #667085; font-size: 8.2pt; }
  @top-right { content: "V2.0 · 2026-08"; color: #667085; font-size: 8.2pt; }
  @bottom-center { content: "第 " counter(page) " 页 / 共 " counter(pages) " 页"; color: #7a8493; font-size: 8.2pt; }
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
.cover h1 { string-set: document-title content(); font-size: 27pt; line-height: 1.28; margin: 0 0 12mm; color: white; }
.cover .lead { font-size: 14pt; color: #e8f1ff; margin-bottom: 28mm; }
.cover-meta { border-top: 1px solid rgba(255,255,255,.45); padding-top: 9mm; width: 125mm; }
.cover-meta div { margin: 2.2mm 0; font-size: 10.5pt; }
.cover-foot { position: absolute; left: 26mm; right: 26mm; bottom: 25mm; color: #eaf2ff; font-size: 9pt; }
.toc { page-break-after: always; }
.toc h1 { color: #0f2f68; font-size: 23pt; border-bottom: 3px solid #155eef; padding-bottom: 4mm; }
.toc ul { margin: 0; padding-left: 0; list-style: none; }
.toc li { margin: 1.6mm 0; }
.toc li.level-3 { margin-left: 7mm; color: #4d5c73; font-size: 9.2pt; }
.toc a { color: inherit; text-decoration: none; }
h2 { page-break-before: always; font-size: 19pt; color: #0f2f68; margin: 0 0 7mm; border-bottom: 2px solid #bfd2f6; padding-bottom: 3mm; }
h2.first-section { page-break-before: auto; }
h3 { font-size: 13.5pt; color: #174a98; margin: 8mm 0 3mm; page-break-after: avoid; }
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


@dataclass(frozen=True)
class ManualSpec:
    slug: str
    label: str
    title: str
    group: str
    entry: str
    scope: str
    fragment: str
    output: str


@dataclass(frozen=True)
class BuildTarget:
    title: str
    audience: str
    output_stem: Path
    markdown: str


def load_manifest() -> tuple[dict[str, str], list[ManualSpec]]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    specs = [ManualSpec(**item) for item in data["manuals"]]
    return data, specs


def read_source(name: str) -> str:
    return (SOURCE_DIR / name).read_text(encoding="utf-8").strip()


def demote_headings(markdown: str) -> str:
    return re.sub(r"^(#{2,5})(?= )", lambda match: "#" + match.group(1), markdown, flags=re.M)


def login_image(spec: ManualSpec, *, consolidated: bool = False) -> str:
    prefix = "manual_assets" if consolidated else "../manual_assets"
    images = {
        "warehouse_operator": ("pda-login.png", "仓库 PDA 登录界面"),
        "warehouse_manager": ("web-login.png", "Web 管理后台登录界面"),
        "system_admin": ("web-login.png", "Web 管理后台登录界面"),
        "warehouse_boss": ("boss-login.png", "仓储经营分析中心登录界面"),
    }
    image = images.get(spec.slug)
    if not image:
        return ""
    filename, alt = image
    return f"\n![{alt}]({prefix}/{filename})\n"


def role_header(manifest: dict[str, str], spec: ManualSpec) -> str:
    return f"""# {spec.title}

**文档版本：** {manifest['version']}

**适用系统版本：** {manifest['baseline_date']} 当前工作区业务基线

**适用对象：** {spec.label}

**正式权限组：** `{spec.group}`

**主要入口：** {spec.entry}

**文档状态：** 交付版

> 本手册按当前业务代码、权限校验、客户端页面和自动化测试契约编写。实际菜单由角色、权限和显式数据范围共同决定。

## 使用范围

| 项目 | 本角色要求 |
|---|---|
| 角色 | {spec.label} |
| 正式权限组 | `{spec.group}` |
| 主要入口 | {spec.entry} |
| 数据范围 | {spec.scope} |

本册可独立用于培训和日常操作，不要求先阅读合订版。涉及其他岗位时，只说明交接点，不授权本角色代替其他岗位操作。
"""


def compose_role_manual(
    manifest: dict[str, str],
    spec: ManualSpec,
    common_safety: str,
    common_status: str,
    common_exceptions: str,
) -> str:
    fragment = read_source(spec.fragment)
    parts = [
        role_header(manifest, spec),
        common_safety,
        login_image(spec),
        fragment,
        common_status,
        common_exceptions,
    ]
    return "\n\n".join(part.strip() for part in parts if part.strip()) + "\n"


def compose_consolidated(
    manifest: dict[str, str],
    specs: list[ManualSpec],
    common_safety: str,
    common_status: str,
    common_exceptions: str,
) -> str:
    role_rows = "\n".join(
        f"| {spec.label} | `{spec.group}` | {spec.entry} | {spec.scope} |"
        for spec in specs
    )
    parts = [
        f"""# {manifest['system_name']}操作手册

**文档版本：** {manifest['version']}

**适用系统版本：** {manifest['baseline_date']} 当前工作区业务基线

**适用对象：** 仓库操作员、仓库管理员、货主业务员、货主管理员、系统管理员、仓库老板

**文档状态：** 交付版

> 本合订版与六本角色分册由同一组源内容构建。培训和岗位发放优先使用对应角色分册。

## 系统组成与角色入口

- **仓库 PDA（wmspda）**：现场收货、上架、代办出库、拣货、复核、库存和仓库报表。
- **货主业务端（wmsownersale）**：授权仓库选单、货主开单、审核、库存、履约、销售报表和账单。
- **仓储经营分析中心（wmsbossbilling）**：老板只读查看经营、库存库容、收入计费和预警。
- **Web 控制台 / Django Admin**：仓库管理、系统配置、基础资料、权限和审计。

![角色与系统入口](manual_assets/role-entry-map.png)

| 角色 | 正式权限组 | 主要入口 | 数据范围 |
|---|---|---|---|
{role_rows}

“仓库主管”只作为企业岗位称谓。需要单据确认和任务管理时使用 `WMS::仓库管理员`；旧组名“仓库主管”会被兼容代码识别为仓库老板。
""",
        common_safety,
        common_status,
    ]

    for spec in specs:
        parts.append(f"## {spec.label}操作指南")
        image = login_image(spec, consolidated=True)
        if image:
            parts.append(image)
        parts.append(demote_headings(read_source(spec.fragment)))

    parts.append(common_exceptions)
    return "\n\n".join(part.strip() for part in parts if part.strip()) + "\n"


def slugify(text: str, index: int) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return f"section-{index}-{cleaned}" if cleaned else f"section-{index}"


def render_html(target: BuildTarget, *, version: str, baseline_date: str) -> str:
    markdown = mistune.create_markdown(plugins=["table", "task_lists"])
    soup = BeautifulSoup(markdown(target.markdown), "html.parser")
    first_h1 = soup.find("h1")
    if first_h1:
        first_h1.extract()

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

    first_section = soup.find("h2")
    if first_section:
        first_section["class"] = ["first-section"]

    toc_lines = ["<section class='toc'><h1>目录</h1><ul>"]
    for level, heading_id, title in toc_items:
        toc_lines.append(
            f"<li class='level-{level}'><a href='#{html.escape(heading_id)}'>{html.escape(title)}</a></li>"
        )
    toc_lines.append("</ul></section>")

    logo_path = Path("../static/img/aaa.png")
    relative_depth = len(target.output_stem.parent.relative_to(DOCS_DIR).parts)
    if relative_depth:
        logo_path = Path(*([".."] * (relative_depth + 1))) / "static/img/aaa.png"

    cover = f"""
    <section class="cover">
      <img class="cover-logo" src="{logo_path.as_posix()}" alt="金桥融通" />
      <h1>{html.escape(target.title)}</h1>
      <div class="lead">岗位操作 · 权限边界 · 异常处理</div>
      <div class="cover-meta">
        <div>文档版本：{html.escape(version)}</div>
        <div>适用基线：{html.escape(baseline_date)} 当前工作区</div>
        <div>文档状态：交付版</div>
        <div>主要读者：{html.escape(target.audience)}</div>
      </div>
      <div class="cover-foot">实际菜单和数据范围以账号角色、权限及显式货主/仓库范围为准</div>
    </section>
    """
    body = str(soup).replace("☐", "□")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(target.title)}</title>
  <style>{STYLE}</style>
</head>
<body>
{cover}
{''.join(toc_lines)}
<div class="document-note">本手册仅描述当前代码中具有有效权限、页面和业务闭环的功能。</div>
{body}
</body>
</html>"""


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
            source.resize(size, Image.Resampling.LANCZOS).save(
                assets / target_name, format="PNG", optimize=True
            )


def build_word_html(document: str, *, base_dir: Path) -> str:
    soup = BeautifulSoup(document, "html.parser")
    cover = soup.select_one(".cover")
    if cover:
        cover["style"] = (
            "page-break-after:always; min-height:900px; padding:70px 60px; "
            "color:#17355f; background-color:#eef4ff; position:relative;"
        )
    for image in soup.find_all("img"):
        source = image.get("src", "")
        if not source or source.startswith(("data:", "http://", "https://", "file://")):
            continue
        path = (base_dir / source).resolve()
        if path.name.endswith("-word.png"):
            word_path = path
        else:
            candidate = path.with_name(f"{path.stem}-word{path.suffix}")
            word_path = candidate if candidate.exists() else path
        image["src"] = word_path.as_uri()
        classes = set(image.get("class", []))
        width = "600" if classes.intersection({"role-map", "web-shot"}) else "280"
        image["width"] = width
        image["style"] = f"width:{width}px; height:auto;"
    return str(soup)


def convert_docx(word_html: Path, docx_path: Path) -> None:
    converter = shutil.which("libreoffice") or shutil.which("soffice")
    if not converter:
        raise RuntimeError("LibreOffice is required to build DOCX files")
    with tempfile.TemporaryDirectory(prefix="wms-manual-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        runtime_dir = temp_dir / "runtime"
        runtime_dir.mkdir(mode=0o700)
        input_html = temp_dir / f"{docx_path.stem}.html"
        input_html.write_text(word_html.read_text(encoding="utf-8"), encoding="utf-8")
        docx_path.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "SAL_USE_VCLPLUGIN": "svp",
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "TMPDIR": str(temp_dir),
            }
        )
        result = subprocess.run(
            [
                converter,
                f"-env:UserInstallation={(temp_dir / 'lo-profile').as_uri()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--nolockcheck",
                "--norestore",
                "--convert-to",
                "docx:Office Open XML Text",
                "--outdir",
                str(docx_path.parent),
                str(input_html),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        if result.returncode != 0 or not docx_path.is_file():
            raise RuntimeError(
                "LibreOffice DOCX conversion failed: "
                + (result.stderr or result.stdout or "no output")
            )
    embed_images(docx_path)


def build_target(target: BuildTarget, *, version: str, baseline_date: str) -> None:
    target.output_stem.parent.mkdir(parents=True, exist_ok=True)
    markdown_path = target.output_stem.with_suffix(".md")
    html_path = target.output_stem.with_suffix(".html")
    word_html_path = target.output_stem.with_name(target.output_stem.name + "_Word源.html")
    pdf_path = target.output_stem.with_suffix(".pdf")
    docx_path = target.output_stem.with_suffix(".docx")

    markdown_path.write_text(target.markdown, encoding="utf-8")
    document = render_html(target, version=version, baseline_date=baseline_date)
    html_path.write_text(document, encoding="utf-8")
    word_html_path.write_text(
        build_word_html(document, base_dir=html_path.parent), encoding="utf-8"
    )
    HTML(filename=str(html_path), base_url=str(html_path.parent)).write_pdf(str(pdf_path))
    convert_docx(word_html_path, docx_path)
    print(f"Built {markdown_path}, {docx_path}, {pdf_path}")


def write_index(manifest: dict[str, str], specs: list[ManualSpec]) -> None:
    rows = []
    for spec in specs:
        quoted_stem = spec.output.replace(" ", "%20")
        rows.append(
            f"| {spec.label} | `{spec.group}` | "
            f"[Markdown]({quoted_stem}.md) · [Word]({quoted_stem}.docx) · [PDF]({quoted_stem}.pdf) |"
        )
    content = f"""# WMS 角色操作手册索引

**版本：** {manifest['version']}

**业务基线：** {manifest['baseline_date']} 当前工作区代码

请按实际岗位领取对应手册。每本分册均包含该角色所需的登录、安全、状态、操作、异常处理和检查表，可独立使用。

| 角色 | 正式权限组 | 手册 |
|---|---|---|
{chr(10).join(rows)}

完整合订版位于上级目录：

- [Markdown](../{CONSOLIDATED_STEM}.md)
- [Word](../{CONSOLIDATED_STEM}.docx)
- [PDF](../{CONSOLIDATED_STEM}.pdf)

维护与核对资料：

- [角色—权限—动作矩阵](ROLE_MATRIX.md)
- [本次业务基线](BASELINE.md)

专项指南：

- [POS 使用手册](../pos_user_manual.md)
- [商城小程序使用教程](../sales-miniapp-user-guide.md)

角色命名注意：企业岗位“仓库主管”如需管理仓库作业，应使用 `WMS::仓库管理员`。旧用户组“仓库主管”会按兼容逻辑识别为仓库老板。
"""
    (MANUALS_DIR / "README.md").write_text(content, encoding="utf-8")


def parse_args(specs: list[ManualSpec]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="build all role manuals and consolidated manual")
    parser.add_argument("--role", choices=[spec.slug for spec in specs], help="build one role manual")
    parser.add_argument("--consolidated", action="store_true", help="build only the consolidated manual")
    return parser.parse_args()


def main() -> None:
    manifest, specs = load_manifest()
    args = parse_args(specs)
    build_all = args.all or not (args.role or args.consolidated)
    common_safety = read_source("common_safety.md")
    common_status = read_source("common_status.md")
    common_exceptions = read_source("common_exceptions.md")
    build_word_images()
    write_index(manifest, specs)

    targets: list[BuildTarget] = []
    if build_all or args.role:
        selected = specs if build_all else [spec for spec in specs if spec.slug == args.role]
        for spec in selected:
            targets.append(
                BuildTarget(
                    title=spec.title,
                    audience=spec.label,
                    output_stem=MANUALS_DIR / spec.output,
                    markdown=compose_role_manual(
                        manifest,
                        spec,
                        common_safety,
                        common_status,
                        common_exceptions,
                    ),
                )
            )

    if build_all or args.consolidated:
        targets.append(
            BuildTarget(
                title=f"{manifest['system_name']}操作手册",
                audience="六类 WMS 角色",
                output_stem=DOCS_DIR / CONSOLIDATED_STEM,
                markdown=compose_consolidated(
                    manifest,
                    specs,
                    common_safety,
                    common_status,
                    common_exceptions,
                ),
            )
        )

    for target in targets:
        build_target(
            target,
            version=manifest["version"],
            baseline_date=manifest["baseline_date"],
        )


if __name__ == "__main__":
    main()
