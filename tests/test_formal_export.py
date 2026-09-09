"""Offline regression tests for the shared Chinese research-report exporters."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Mm, Pt
from PIL import Image

from backend.utils import write_md_to_pdf, write_md_to_word
from backend.reporting.document_export import build_report_html


SAMPLE = """# 发动机技术与运营影响研究

## 摘要

本报告分析发动机可靠性与维修保障。Research evidence 为结论提供支持。

**关键词：** 发动机；维修保障；MRO

## 目录

- [1 引言](#sec-1)
- [1.1 研究范围](#sec-1-1)

<a id="sec-1"></a>

## 1 引言

首段正文含有 **重点内容**，并依据公开资料提出判断[1]。\x01保留可读文本。

<a id="sec-1-1"></a>

### 1.1 研究范围

表 1 维修活动概况

| 活动 | 说明 |
| --- | --- |
| 检查 | 按照文献要求开展检查，保留实际条件与研究边界。 |
| 维修 | MRO 服务 |

![图 1 研究示意](/outputs/report_images/test.png)

来源：研究资料[1]。

## 参考文献

[1] Example Organization. Technical report. 2025. [来源链接](https://example.org/report?item=1&lang=en)
"""

CITED_SAMPLE = """# 来源追溯检查

## 1 引言

研究依据[[1]](#ref-1)，并结合资料[[2]](#ref-2)。

## 参考文献

<a id="ref-1"></a>
[1] 来源甲。[原始资料](/api/local-library/papers/report.pdf/open)

<a id="ref-2"></a>
[2] 来源乙。[网页资料](https://example.org/source)
"""


class FormalExportTests(unittest.TestCase):
    def setUp(self):
        self.previous_cwd = Path.cwd()
        self.temp_dir = tempfile.TemporaryDirectory()
        os.chdir(self.temp_dir.name)
        Path("outputs/report_images").mkdir(parents=True)
        Image.new("RGB", (1200, 300), "white").save("outputs/report_images/test.png")

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.temp_dir.cleanup()

    def make_doc(self):
        result = asyncio.run(write_md_to_word(SAMPLE, "中文 报告"))
        self.assertEqual(result, quote("outputs/中文 报告.docx"))
        return Document("outputs/中文 报告.docx")

    def test_word_has_a4_typography_and_real_navigation(self):
        doc = self.make_doc()
        section = doc.sections[0]
        self.assertAlmostEqual(section.page_width, Mm(210), delta=635)
        self.assertAlmostEqual(section.page_height, Mm(297), delta=635)
        self.assertAlmostEqual(section.top_margin, Mm(25), delta=635)
        self.assertAlmostEqual(section.left_margin, Mm(25), delta=635)
        self.assertTrue(section.different_first_page_header_footer)
        self.assertEqual(doc.paragraphs[4].style.name, "Title")
        self.assertIn("规范论文格式报告", "\n".join(p.text for p in doc.paragraphs[:8]))
        self.assertEqual(doc.styles["Normal"].font.size, Pt(12))
        self.assertEqual(doc.styles["Normal"].paragraph_format.line_spacing, 1.5)
        self.assertEqual(doc.styles["Normal"].paragraph_format.first_line_indent, Pt(24))
        self.assertEqual(doc.styles["Report Abstract"].paragraph_format.first_line_indent, 0)
        self.assertEqual(doc.styles["Report Keywords"].paragraph_format.first_line_indent, 0)
        fonts = doc.styles["Normal"]._element.rPr.rFonts
        self.assertEqual(fonts.get(qn("w:eastAsia")), "SimSun")
        self.assertEqual(fonts.get(qn("w:ascii")), "Times New Roman")
        xml = doc._element.xml
        self.assertIn('w:name="sec-1"', xml)
        self.assertIn('TOC \\o "1-2" \\h \\z \\u', xml)
        update_fields = doc.settings._element.find(qn("w:updateFields"))
        self.assertIsNotNone(update_fields)
        self.assertEqual(update_fields.get(qn("w:val")), "true")
        self.assertTrue(any("PAGE" in element.text for element in section.footer._element.iter(qn("w:instrText"))))
        self.assertTrue(section.header.paragraphs[0].text)

    def test_word_preserves_citations_images_and_three_line_tables(self):
        doc = self.make_doc()
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual(len(doc.inline_shapes), 1)
        image = doc.inline_shapes[0]
        self.assertLessEqual(image.width, Mm(160))
        self.assertAlmostEqual(image.width / image.height, 4, places=2)
        table = doc.tables[0]
        self.assertIsNotNone(table.rows[0]._tr.trPr)
        self.assertIsNotNone(table.rows[0]._tr.trPr.find(qn("w:tblHeader")))
        self.assertEqual(table.cell(1, 1).text, "按照文献要求开展检查，保留实际条件与研究边界。")
        borders = table._tbl.tblPr.find(qn("w:tblBorders"))
        self.assertIsNotNone(borders)
        self.assertEqual(borders.find(qn("w:insideV")).get(qn("w:val")), "single")
        self.assertEqual(borders.find(qn("w:insideV")).get(qn("w:color")), "D9D9D9")
        header_shading = table.cell(0, 0)._tc.tcPr.find(qn("w:shd"))
        self.assertIsNotNone(header_shading)
        self.assertEqual(header_shading.get(qn("w:fill")), "D9EAF7")
        captions = [p for p in doc.paragraphs if p.text.startswith("图 1")]
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0].paragraph_format.first_line_indent, 0)
        refs = [p for p in doc.paragraphs if p.text.startswith("[1]")]
        self.assertEqual(len(refs), 1)
        self.assertLess(refs[0].paragraph_format.first_line_indent, 0)
        self.assertTrue(any(r.target_ref.startswith("https://example.org/report") for r in doc.part.rels.values()))
        with ZipFile("outputs/中文 报告.docx") as archive:
            self.assertNotIn(b"\x01", archive.read("word/document.xml"))

    def test_pdf_is_a4_and_contains_readable_report_content(self):
        result = asyncio.run(write_md_to_pdf(SAMPLE, "研究 pdf"))
        self.assertEqual(result, quote("outputs/研究 pdf.pdf"))
        from pypdf import PdfReader
        pdf = PdfReader("outputs/研究 pdf.pdf")
        self.assertGreater(len(pdf.pages), 0)
        self.assertAlmostEqual(float(pdf.pages[0].mediabox.width), 595.28, delta=1)
        text = "\n".join(page.extract_text() for page in pdf.pages)
        self.assertIn("研究范围", text)
        self.assertIn("维修活动概况", text)
        self.assertNotIn("<a id", text)
        self.assertTrue(any(page.get("/Annots") for page in pdf.pages))

    def test_export_failure_keeps_the_download_api_empty_string_contract(self):
        with patch("backend.utils.render_word", side_effect=OSError("cannot write")):
            self.assertEqual(asyncio.run(write_md_to_word(SAMPLE, "failed")), "")
        with patch("backend.utils.render_pdf", side_effect=OSError("cannot write")):
            self.assertEqual(asyncio.run(write_md_to_pdf(SAMPLE, "failed")), "")

    def test_each_citation_has_its_own_real_reference_destination(self):
        html = build_report_html(CITED_SAMPLE)
        for target in ("ref-1", "ref-2"):
            self.assertIn(f'id="{target}"', html)
        result = asyncio.run(write_md_to_word(CITED_SAMPLE, "anchors"))
        self.assertEqual(result, "outputs/anchors.docx")
        doc = Document(result)
        bookmarks = {node.get(qn("w:name")): node for node in doc._element.iter(qn("w:bookmarkStart"))}
        for number in (1, 2):
            target = f"ref-{number}"
            self.assertIn(target, bookmarks)
            paragraph_text = "".join(bookmarks[target].getparent().itertext())
            self.assertIn(f"[{number}]", paragraph_text)
        result = asyncio.run(write_md_to_pdf(CITED_SAMPLE, "anchors"))
        self.assertEqual(result, "outputs/anchors.pdf")
        from pypdf import PdfReader
        pdf = PdfReader(result)
        for target in ("ref-1", "ref-2"):
            self.assertIn(target, pdf.named_destinations)
        links = [str(annotation.get_object().get("/Dest")) for page in pdf.pages for annotation in page.get("/Annots", [])]
        self.assertIn("ref-1", links)
        self.assertIn("ref-2", links)

    def test_local_source_links_are_clickable_after_downloading_exports(self):
        expected = "http://127.0.0.1:8000/api/local-library/papers/report.pdf/open"
        with patch.dict(os.environ, {"REPORT_EXPORT_BASE_URL": "http://127.0.0.1:8000"}):
            self.assertIn(expected, build_report_html(CITED_SAMPLE))
            result = asyncio.run(write_md_to_word(CITED_SAMPLE, "local"))
            pdf_path = asyncio.run(write_md_to_pdf(CITED_SAMPLE, "local"))
        doc = Document(result)
        self.assertTrue(any(relation.target_ref == expected for relation in doc.part.rels.values()))
        from pypdf import PdfReader
        pdf = PdfReader(pdf_path)
        links = [str(annotation.get_object().get("/A", {}).get("/URI", ""))
                 for page in pdf.pages for annotation in page.get("/Annots", [])]
        self.assertIn(expected, links)

    def test_heading_runs_inherit_their_real_heading_sizes_and_fonts(self):
        doc = self.make_doc()
        for name, size in (("Title", 22), ("Heading 1", 15), ("Heading 2", 13)):
            heading = next(p for p in doc.paragraphs if p.style.name == name)
            self.assertEqual(heading.style.font.size, Pt(size))
            self.assertEqual(heading.style._element.rPr.rFonts.get(qn("w:eastAsia")), "SimHei")
            self.assertTrue(all(run.font.size is None for run in heading.runs))
            self.assertTrue(all(run.font.name is None for run in heading.runs))

    def test_title_does_not_inherit_the_template_blue_border(self):
        doc = self.make_doc()
        for name in ("Title", "Normal", "Heading 1", "Heading 2"):
            self.assertEqual(list(doc.styles[name]._element.iter(qn("w:pBdr"))), [])
        self.assertEqual(list(doc.paragraphs[0]._p.iter(qn("w:pBdr"))), [])

    def test_word_body_uses_real_line_spacing_without_document_grid(self):
        doc = self.make_doc()
        for section in doc.sections:
            self.assertIsNone(section._sectPr.find(qn("w:docGrid")))
        snap = doc.styles["Normal"]._element.pPr.find(qn("w:snapToGrid"))
        self.assertIsNotNone(snap)
        self.assertEqual(snap.get(qn("w:val")), "0")
        self.assertEqual(doc.styles["Normal"].paragraph_format.line_spacing, 1.5)

    def test_manual_break_lines_are_not_expanded_across_the_page(self):
        doc = self.make_doc()
        compat = doc.settings._element.find(qn("w:compat"))
        self.assertIsNotNone(compat)
        setting = compat.find(qn("w:doNotExpandShiftReturn"))
        self.assertIsNotNone(setting)
        self.assertNotEqual(setting.get(qn("w:val")), "0")

    def test_soft_markdown_breaks_flow_but_explicit_line_breaks_are_preserved(self):
        text = "# 换行检查\n\n第一行\n续写内容。\n\n强制换行  \n保留下一行。"
        path = asyncio.run(write_md_to_word(text, "breaks"))
        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        self.assertIn("第一行 续写内容。", paragraphs)
        self.assertIn("强制换行\n保留下一行。", paragraphs)

    def test_pdf_short_table_moves_together_and_long_table_still_flows(self):
        from pypdf import PdfReader
        prefix = "# 表格分页检查\n\n" + "一段用于检查页面余量的正文。\n\n" * 25
        short_table = "| 项目 | 说明 |\n| --- | --- |\n| FIRST_ROW | 第一行 |\n| MIDDLE_ROW | 中间行 |\n| LAST_ROW | 最后一行 |\n"
        path = asyncio.run(write_md_to_pdf(prefix + short_table, "short-table"))
        pages = [page.extract_text() for page in PdfReader(path).pages]
        first_page = next(i for i, text in enumerate(pages) if "FIRST_ROW" in text)
        last_page = next(i for i, text in enumerate(pages) if "LAST_ROW" in text)
        self.assertEqual(first_page, last_page)
        long_table = "| 项目 | 说明 |\n| --- | --- |\n" + "\n".join(f"| ITEM_{number:03d} | 行数据 |" for number in range(70))
        path = asyncio.run(write_md_to_pdf("# 长表分页检查\n\n" + long_table, "long-table"))
        pages = [page.extract_text() for page in PdfReader(path).pages]
        self.assertGreater(len(pages), 1)
        self.assertTrue(all("项目" in text for text in pages))
        self.assertTrue(all(f"ITEM_{number:03d}" in "\n".join(pages) for number in range(70)))

    def test_export_creates_missing_output_directory_and_filters_xml_noncharacters(self):
        # Empty outputs and characters forbidden by XML 1.0 are common inputs
        # when reports are assembled from OCR or extracted documents.
        os.chdir(Path(self.temp_dir.name) / "outputs" / "report_images")
        result = asyncio.run(write_md_to_word("# 报告\n\n正文\ufffe\uffff\ud800结束。", "new"))
        self.assertEqual(result, "outputs/new.docx")
        doc = Document("outputs/new.docx")
        self.assertIn("正文结束。", "\n".join(p.text for p in doc.paragraphs))


if __name__ == "__main__":
    unittest.main()
