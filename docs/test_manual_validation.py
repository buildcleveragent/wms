"""Document-only regression checks; run directly, without pytest/Django setup."""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from build_user_manual import BuildTarget, normalize_docx_layout, render_html
from PIL import Image
from validate_user_manuals import (
    check_body,
    check_docx,
    check_equal,
    check_links,
    check_markdown,
    check_pdf,
    markdown_body_blocks,
    pdf_body_text,
)
from weasyprint import HTML

MANIFEST = {"version": "V2.1", "baseline_date": "2026-09-07"}
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
BODY_NOTE = "本手册仅描述当前代码中具有有效权限、页面和业务闭环的功能。"


class ManualValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make_docx(self, text, *, referenced=True, image_content=None):
        path = self.root / "manual.docx"
        if image_content is None:
            buffer = io.BytesIO()
            Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
            image_content = buffer.getvalue()
        drawing = '<w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r>'
        with zipfile.ZipFile(path, "w") as package:
            package.writestr(
                "word/document.xml",
                "<w:document "
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r>"
                f"{drawing if referenced else ''}</w:p></w:body></w:document>",
            )
            package.writestr(
                "word/_rels/document.xml.rels",
                "<Relationships "
                'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="test/image" Target="media/image.png"/>'
                "</Relationships>",
            )
            package.writestr("word/media/image.png", image_content)
        return path

    def make_pdf(self, body, *, second_page_size="A4"):
        path = self.root / "manual.pdf"
        HTML(
            string=(
                "<html><head><style>"
                "@page {size:A4;} "
                f"@page other {{size:{second_page_size};}} "
                'body {font-family:"Noto Sans CJK SC";} '
                ".other {page:other;page-break-before:always;}"
                "</style></head><body><h1>使用指南</h1><p>V2.1 2026-09-07</p>"
                f"{body}</body></html>"
            )
        ).write_pdf(path)
        return path

    def make_layout_docx(self, toc_properties="", note_properties=""):
        path = self.root / "layout.docx"
        document = (
            f'<w:document xmlns:w="{WORD_NS}" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14">'
            '<w:body><w:p w14:paraId="00000001">'
            '<w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>使用指南</w:t></w:r></w:p>'
            f"<w:p>{toc_properties}<w:r><w:t>目</w:t></w:r><w:r><w:t>录</w:t></w:r></w:p>"
            f"<w:p>{note_properties}<w:r><w:t>{BODY_NOTE}</w:t></w:r></w:p>"
            '<w:p><w:r><w:t xml:space="preserve">正文 &amp; 库存核对 </w:t></w:r></w:p>'
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格内容</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>'
        )
        payloads = {
            "word/document.xml": document.encode(),
            "word/media/logo.png": b"image bytes preserved by layout-only operation",
            "word/_rels/document.xml.rels": b"<Relationships/>",
            "[Content_Types].xml": b"<Types/>",
            "docProps/custom.xml": b"<custom>unchanged</custom>",
        }
        with zipfile.ZipFile(path, "w") as package:
            for name, content in payloads.items():
                package.writestr(name, content)
        return path, payloads

    def test_links_resolve_files_anchors_and_ignore_code_examples(self):
        target = self.root / "说明.md"
        target.write_text("# 使用说明\n\n## 登录\n\n## 登录\n", encoding="utf-8")
        path = self.root / "index.md"
        path.write_text(
            "[说明](%E8%AF%B4%E6%98%8E.md#登录-1)\n\n" "```text\n[代码示例](missing.md)\n```\n",
            encoding="utf-8",
        )
        errors = []
        check_links(path, errors)
        self.assertEqual(errors, [])
        path.write_text("[坏锚点](说明.md#不存在)\n![丢失图片](missing.png)\n", encoding="utf-8")
        check_links(path, errors)
        self.assertTrue(any("missing heading anchor" in error for error in errors))
        self.assertTrue(any("missing link/image" in error for error in errors))

    def test_current_metadata_in_history_does_not_mask_stale_cover(self):
        path = self.root / "guide.md"
        path.write_text(
            "# 使用指南\n\nV2.0 / 2026-08-04\n\n## 修订记录\nV2.1 / 2026-09-07\n",
            encoding="utf-8",
        )
        errors = []
        check_markdown(path, MANIFEST, errors)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("missing current metadata" in error for error in errors))

    def test_current_sources_detect_stale_generated_copy(self):
        path = self.root / "manual.md"
        path.write_text("旧操作步骤\n", encoding="utf-8")
        errors = []
        check_equal(path, "新操作步骤\n", errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("differs from current sources", errors[0])

    def test_docx_external_and_broken_images_are_rejected(self):
        path = self.root / "manual.docx"
        with zipfile.ZipFile(path, "w") as package:
            package.writestr(
                "word/document.xml",
                "<w:document "
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>V2.1 2026-09-07 使用指南</w:t></w:r></w:p></w:body>"
                "</w:document>",
            )
            package.writestr(
                "word/_rels/document.xml.rels",
                "<Relationships "
                'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="test/image" Target="file:///old/logo.png" '
                'TargetMode="External"/>'
                '<Relationship Id="rId2" Type="test/image" Target="media/missing.png"/>'
                "</Relationships>",
            )
        errors = []
        check_docx(path, MANIFEST, ["使用指南"], errors)
        self.assertTrue(any("external image" in error for error in errors))
        self.assertTrue(any("relationship is broken" in error for error in errors))

    def test_body_collection_includes_paragraphs_lists_and_table_cells(self):
        blocks = markdown_body_blocks(
            "# 使用指南\n\n正文核对原客户。\n\n- 先确认库存\n  - 再提交销售\n\n"
            "| 条件 | 处理 |\n|---|---|\n| 缺少权限 | 联系管理员 |\n"
        )
        self.assertIn("正文核对原客户。", blocks)
        self.assertIn("先确认库存\n", blocks)
        self.assertIn("再提交销售", blocks)
        self.assertIn("联系管理员", blocks)
        self.assertNotIn("使用指南", blocks)

    def test_docx_directory_only_does_not_mask_missing_prose_and_table_content(self):
        path = self.make_docx("V2.1 2026-09-07 使用指南 登录 退货")
        errors = []
        check_docx(
            path,
            MANIFEST,
            ["使用指南", "登录", "退货"],
            errors,
            body_blocks=["登录后核对本人仓库。", "缺少权限时联系管理员"],
        )
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("missing rendered body content" in error for error in errors))

    def test_docx_orphan_media_does_not_count_as_an_embedded_picture(self):
        path = self.make_docx("V2.1 2026-09-07 使用指南", referenced=False)
        errors = []
        check_docx(path, MANIFEST, ["使用指南"], errors)
        self.assertTrue(any("missing referenced images" in error for error in errors))

    def test_docx_invalid_referenced_image_is_rejected(self):
        path = self.make_docx("V2.1 2026-09-07 使用指南", image_content=b"")
        errors = []
        check_docx(path, MANIFEST, ["使用指南"], errors)
        self.assertTrue(any("referenced image is invalid" in error for error in errors))

    def test_docx_with_body_and_valid_referenced_picture_passes(self):
        path = self.make_docx("V2.1 2026-09-07 使用指南 登录后核对本人仓库。")
        errors = []
        check_docx(path, MANIFEST, ["使用指南"], errors, body_blocks=["登录后核对本人仓库。"])
        self.assertEqual(errors, [])

    def test_pdf_later_non_a4_page_is_rejected(self):
        path = self.make_pdf(
            '<section class="other">第二页正文</section>', second_page_size="Letter"
        )
        errors = []
        check_pdf(path, MANIFEST, ["使用指南"], errors, body_blocks=["第二页正文"])
        self.assertEqual(len(errors), 1)
        self.assertIn("page 2 is not portrait A4", errors[0])

    def test_pdf_directory_only_does_not_mask_missing_body(self):
        path = self.make_pdf("<p>目录</p><p>登录</p><p>退货</p>")
        errors = []
        check_pdf(
            path,
            MANIFEST,
            ["使用指南", "登录", "退货"],
            errors,
            body_blocks=["登录后核对本人仓库。", "退款不能超过原金额"],
        )
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("missing rendered body content" in error for error in errors))

    def test_a4_pdf_prose_and_table_content_pass(self):
        path = self.make_pdf(
            '<p>登录后核对本人仓库。</p><section class="other">'
            "<table><tr><td>缺少权限</td><td>联系管理员</td></tr></table></section>"
        )
        errors = []
        check_pdf(
            path,
            MANIFEST,
            ["使用指南"],
            errors,
            body_blocks=["登录后核对本人仓库。", "联系管理员"],
        )
        self.assertEqual(errors, [])

    def test_pdf_running_header_and_footer_do_not_break_cross_page_prose(self):
        raw = "登录后核对\n第 2 页 / 共 8 页\n\f使用指南\nV2.1 · 2026-09-07\n本人仓库。"
        errors = []
        check_body(
            pdf_body_text(raw, MANIFEST, ["使用指南"]),
            ["登录后核对本人仓库。"],
            self.root / "manual.pdf",
            errors,
        )
        self.assertEqual(errors, [])

    def test_word_layout_breaks_are_idempotent_and_preserve_content_and_package_parts(self):
        path, original = self.make_layout_docx(
            '<w:pPr><w:pStyle w:val="Heading1"/><w:keepNext/><w:keepLines/>'
            '<w:bidi w:val="0"/><w:jc w:val="start"/><w:rPr/></w:pPr>'
        )
        normalize_docx_layout(path)
        with zipfile.ZipFile(path) as package:
            once = {name: package.read(name) for name in package.namelist()}
        normalize_docx_layout(path)
        with zipfile.ZipFile(path) as package:
            twice = {name: package.read(name) for name in package.namelist()}
        self.assertEqual(once, twice)
        self.assertEqual(set(once), set(original))
        for name in original:
            if name != "word/document.xml":
                self.assertEqual(once[name], original[name])

        old_root = ET.fromstring(original["word/document.xml"])
        new_root = ET.fromstring(once["word/document.xml"])
        old_text = [node.text for node in old_root.iter(f"{{{WORD_NS}}}t")]
        self.assertEqual([node.text for node in new_root.iter(f"{{{WORD_NS}}}t")], old_text)
        self.assertEqual(new_root.attrib, old_root.attrib)
        paragraphs = new_root.findall(f".//{{{WORD_NS}}}p")
        for paragraph in paragraphs:
            text = "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
            breaks = paragraph.findall(f"{{{WORD_NS}}}pPr/{{{WORD_NS}}}pageBreakBefore")
            self.assertEqual(len(breaks), 1 if text in {"目录", BODY_NOTE} else 0)
        # Remove only the intentional changes before comparing the complete XML trees.
        for index in (1, 2):
            properties = paragraphs[index].find(f"{{{WORD_NS}}}pPr")
            properties.remove(properties.find(f"{{{WORD_NS}}}pageBreakBefore"))
            if index == 2:
                paragraphs[index].remove(properties)
        self.assertEqual(ET.tostring(new_root), ET.tostring(old_root))

    def test_word_layout_places_page_break_in_paragraph_property_schema_order(self):
        path, _ = self.make_layout_docx(
            '<w:pPr><w:pStyle w:val="Heading1"/><w:keepNext/><w:keepLines/>'
            '<w:bidi w:val="0"/><w:jc w:val="start"/><w:rPr/></w:pPr>'
        )
        normalize_docx_layout(path)
        with zipfile.ZipFile(path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
        properties = root.findall(f".//{{{WORD_NS}}}p")[1].find(f"{{{WORD_NS}}}pPr")
        self.assertEqual(
            [node.tag.rsplit("}", 1)[-1] for node in properties],
            ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "bidi", "jc", "rPr"],
        )

    def test_word_layout_enables_preexisting_disabled_break_without_duplicate(self):
        path, _ = self.make_layout_docx(
            '<w:pPr><w:pageBreakBefore w:val="0"/></w:pPr>',
            '<w:pPr><w:pageBreakBefore w:val="false"/></w:pPr>',
        )
        normalize_docx_layout(path)
        normalize_docx_layout(path)
        with zipfile.ZipFile(path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
        breaks = root.findall(f".//{{{WORD_NS}}}pageBreakBefore")
        self.assertEqual(len(breaks), 2)
        for node in breaks:
            self.assertIn(node.get(f"{{{WORD_NS}}}val"), (None, "1", "true", "on"))

    def test_new_manifest_edition_reaches_html_cover_and_page_header(self):
        target = BuildTarget("示例手册", "仓管", self.root / "手册", "# 示例手册\n\n## 登录\n")
        # Use a DOCS_DIR output path so normal asset-relative rendering applies.
        from build_user_manual import DOCS_DIR

        target = BuildTarget(target.title, target.audience, DOCS_DIR / "示例", target.markdown)
        document = render_html(target, version="V3.7", baseline_date="2030-01-02")
        self.assertIn('content: "V3.7 · 2030-01-02"', document)
        self.assertIn("文档版本：V3.7", document)
        self.assertIn("适用基线：2030-01-02", document)
        self.assertNotIn("V2.0", document)
        self.assertNotIn("__DOCUMENT_EDITION__", document)


if __name__ == "__main__":
    unittest.main()
