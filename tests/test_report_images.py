"""Real PDF image extraction and omitted-figure regression checks."""
import asyncio
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import fitz
from PIL import Image


class ReportImageTests(unittest.TestCase):
    def test_long_chinese_query_keeps_the_engine_identifier_for_figure_ranking(self):
        from gpt_researcher.document.local_image_extractor import _query_tokens
        query='GTF发动机粉末金属问题维修保障市场影响研究技术机制受影响机队适航要求维修网络备发升级航司运营影响跨来源比较时间预测属性'
        self.assertIn('gtf',_query_tokens(query))

    def test_normalized_figure_source_removes_repeated_caption_but_preserves_prose(self):
        from backend.reporting.image_evidence import normalize_figure_sources
        images=[{'markdown_path':'/outputs/report_images/a.png','source_file':'gtf.pdf','page':2,'caption_matched':True}]
        raw='前文。\n\n![GTF现场](/outputs/report_images/a.png)\n\n图题：GTF现场。  \n图源：wrong.pdf，第9页。[原文1]\n\n后文。'
        output=normalize_figure_sources(raw,images)
        self.assertNotIn('图题：',output)
        self.assertNotIn('wrong.pdf',output)
        self.assertIn('gtf.pdf，第 2 页',output)
        self.assertIn('前文。',output)
        self.assertIn('后文。',output)
        self.assertEqual(normalize_figure_sources(output,images),output)

    def test_figure_normalization_keeps_substantive_qualifications(self):
        from backend.reporting.image_evidence import normalize_figure_sources
        images=[{'markdown_path':'/outputs/a.png','source_file':'gtf.pdf','page':2,'caption_matched':True}]
        raw='![维修设施](/outputs/a.png)\n\n图题：这是2025年计划扩建的设施，并非已投产。\n\n图源：gtf.pdf，第2页。照片不能证明产能提升。'
        result=normalize_figure_sources(raw,images)
        self.assertIn('并非已投产',result)
        self.assertIn('照片不能证明产能提升',result)
        from backend.reporting.finalization import claim_blocks
        self.assertTrue(all(b['locators']==['gtf.pdf'] for b in claim_blocks(result)))
        result=normalize_figure_sources(raw.replace('，第2页',''),images)
        self.assertIn('照片不能证明产能提升',result)

    def test_embedded_jpeg2000_is_encoded_as_png_and_caption_is_per_image(self):
        from gpt_researcher.document.local_image_extractor import extract_local_report_images
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc = fitz.open()
            page = doc.new_page(width=600, height=700)
            for i, caption in enumerate(['Figure 1 GTF maintenance', 'Figure 2 PW127XT engine']):
                bitmap = Image.effect_noise((350, 220), 40).convert('RGB')
                encoded = BytesIO()
                bitmap.save(encoded, format='JPEG2000')
                top = 60 + i * 290
                page.insert_image(fitz.Rect(60, top, 410, top + 220), stream=encoded.getvalue())
                page.insert_text((60, top + 238), caption)
            pdf = root / 'engines.pdf'
            doc.save(pdf)
            doc.close()
            images = extract_local_report_images([SimpleNamespace(source_path=str(pdf))], 'GTF maintenance',
                                                 output_root=root / 'images', max_total=6)
            embedded = [item for item in images if item['kind'] == 'embedded_image']
            self.assertEqual(len(embedded), 2)
            self.assertEqual({i['caption'] for i in embedded}, {'Figure 1 GTF maintenance', 'Figure 2 PW127XT engine'})
            for item in embedded:
                self.assertTrue(item['caption_matched'])
                self.assertEqual(Path(item['image_path']).suffix, '.png')
                self.assertTrue(Path(item['image_path']).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))

    def test_caption_of_neighboring_figure_is_not_reused(self):
        from gpt_researcher.document.local_image_extractor import _find_image_caption
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((320, 260), 'Figure 2 Other engine')
        self.assertEqual(_find_image_caption(page, fitz.Rect(40, 40, 240, 240)), '')
        doc.close()

    def service_and_image(self, root):
        from three_agent_service import ThreeAgentService, ThreeAgentRequestData
        service = ThreeAgentService(ThreeAgentRequestData(task='GTF发动机维修网络与升级'))
        image_path = root / 'gtf.png'
        Image.new('RGB', (250, 180), 'gray').save(image_path)
        paper = SimpleNamespace(file_name='gtf.pdf', source_path=str(root / 'gtf.pdf'))
        service.selected_local_papers = [paper]
        service.report_images = [{'source_file':'gtf.pdf', 'page':2, 'image_path':str(image_path),
                                 'markdown_path':'/outputs/report_images/gtf.png', 'caption':'GTF维修现场',
                                 'caption_matched':True, 'kind':'embedded_image'}]
        return service

    def test_omitted_image_is_added_in_matching_body_section_once(self):
        with TemporaryDirectory() as directory:
            service = self.service_and_image(Path(directory))
            raw = '# 报告\n\n## 1 引言\n背景。\n\n## 2 维修网络\nGTF维修能力分析。\n\n## 参考文献\n- [原文1] gtf.pdf\n'
            result = service.ensure_report_images_inserted(raw)
            self.assertIn('![GTF维修现场]', result)
            self.assertLess(result.index('![GTF维修现场]'), result.index('## 参考文献'))
            self.assertGreater(result.index('![GTF维修现场]'), result.index('GTF维修能力分析。'))
            self.assertIn('[原文: gtf.pdf]', result)
            self.assertEqual(service.ensure_report_images_inserted(result), result)

    def test_unrelated_unmatched_or_missing_image_is_not_forced_into_report(self):
        with TemporaryDirectory() as directory:
            service = self.service_and_image(Path(directory))
            raw = '# 报告\n\n## 2 维修网络\nGTF维修能力分析。\n\n## 参考文献\n'
            for change in [{'caption':'PW127XT维修现场'}, {'caption_matched':False},
                           {'image_path':str(Path(directory)/'missing.png')}, {'source_file':'unknown.pdf'}]:
                with self.subTest(change=change):
                    with patch.dict(service.report_images[0], change):
                        self.assertEqual(service.ensure_report_images_inserted(raw), raw)

    def test_run_checks_images_before_evaluation_and_export(self):
        class StopAfterImageCheck(Exception):
            pass
        with TemporaryDirectory() as directory:
            service = self.service_and_image(Path(directory))
            raw = '# 报告\n\n## 2 维修网络\nGTF维修能力分析。\n\n## 参考文献\n'
            def evaluate(text):
                self.assertIn('![GTF维修现场]', text)
                raise StopAfterImageCheck()
            with patch.object(service, 'pre_search_abstracts', new=AsyncMock()), \
                 patch.object(service, 'planner_agent', return_value=[]), \
                 patch.object(service, 'research_agent', new=AsyncMock(return_value=[])), \
                 patch.object(service, 'collect_report_images'), \
                 patch.object(service, 'writer_agent', new=AsyncMock(return_value=raw)), \
                 patch('three_agent_service.evaluate_public_url_sources', side_effect=evaluate):
                with self.assertRaises(StopAfterImageCheck):
                    asyncio.run(service.run())

    def test_shared_figure_source_is_not_duplicate_body_prose(self):
        from backend.reporting.content_depth import review_content
        source = '\n\n图源：同一篇文献的完整文件名.pdf，第2页。[原文1]\n\n'
        raw = '## 3 维修\n维修能力应结合实际时隙评估。' + source + '## 4 升级\n构型切换需要跟踪部件供给。' + source
        self.assertEqual(review_content(raw)['duplicate_paragraphs'], 0)

    def test_generic_english_overlap_cannot_override_a_different_engine_model(self):
        with TemporaryDirectory() as directory:
            service = self.service_and_image(Path(directory))
            service.request.task = 'GTF发动机 maintenance 维修网络'
            service.report_images[0]['caption'] = 'PW127XT发动机维修现场 maintenance'
            raw = '# 报告\n\n## 2 维修网络\nGTF维修能力分析。\n\n## 参考文献\n'
            self.assertEqual(service.ensure_report_images_inserted(raw), raw)

    def test_references_of_any_heading_level_remain_after_the_figure(self):
        with TemporaryDirectory() as directory:
            service = self.service_and_image(Path(directory))
            for level in [1, 4, 6]:
                heading = '#' * level + ' 参考文献'
                raw = '# 报告\n\n## 2 维修网络\nGTF维修能力分析。\n\n' + heading + '\n- 文献记录\n'
                result = service.ensure_report_images_inserted(raw)
                self.assertIn('![GTF维修现场]', result)
                self.assertLess(result.index('![GTF维修现场]'), result.index(heading))


if __name__ == '__main__':
    unittest.main()
