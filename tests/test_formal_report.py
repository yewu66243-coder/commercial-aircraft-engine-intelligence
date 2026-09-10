import importlib
import unittest


BASE = """# GTF发动机研究

## 摘要
本报告分析已收集的GTF发动机资料，比较技术问题与维修安排，并讨论证据适用范围。

**关键词：** GTF；发动机；维修保障

## 引言
本研究关注发动机维修问题。

## 资料来源与研究方法
使用本次提供的资料进行比较。

## 技术问题
证据A指出维修周期为35天[URL8]。同一资料说明范围[URL8]。另一份文件记载型号PW1100G[原文2]。

## 综合讨论与研究局限
统计口径不同，该比较仍需核实。

## 结论与建议
相关结论限于上述资料覆盖的范围[原文2]。

## 证据来源列表
- [原文2] 原始论文.pdf
- [URL8] https://example.org/source
"""


class FormalReportTests(unittest.TestCase):
    def prepare(self, text=BASE, **kwargs):
        try:
            module = importlib.import_module('backend.reporting.formal_report')
        except ModuleNotFoundError:
            module = None
        self.assertIsNotNone(module, 'Formal report preparation has not been implemented')
        return module.prepare_formal_report(text, task='GTF发动机研究', **kwargs)

    def test_citations_follow_first_appearance_and_repeat_consistently(self):
        result = self.prepare()
        self.assertIn('35天[[1]]', result.markdown)
        self.assertIn('范围[[1]]', result.markdown)
        self.assertIn('PW1100G[[2]]', result.markdown)
        self.assertIn('[1]', result.citation_map)
        self.assertEqual(result.citation_map['[1]']['original_ids'], ['[URL8]'])
        self.assertIn('[EB/OL]', result.markdown)
        self.assertIn('https://example.org/source', result.markdown)
        self.assertNotIn('[URL8]', result.markdown)
        self.assertNotIn('查看原始资料', result.markdown)

    def test_duplicate_urls_share_a_reference(self):
        text = BASE.replace('型号PW1100G[原文2]', '型号PW1100G[URL9]')
        text += '\n- [URL9] https://example.org/source\n'
        result = self.prepare(text)
        self.assertIn('PW1100G[[1]]', result.markdown)
        self.assertEqual(len(result.citation_map), 2)

    def test_missing_reference_is_visible_and_cannot_pass_quality(self):
        result = self.prepare(BASE.replace('[URL8]', '[URL99]', 1))
        self.assertNotEqual(result.quality['status'], 'ready')
        self.assertTrue(result.quality['missing_source_ids'])
        self.assertIn('来源信息缺失', result.markdown)

    def test_internal_statistics_removed_but_limitations_preserved(self):
        text = BASE + '\n## 本次运行统计与溯源检测\n| 运行编号 | secret-run |\n'
        text += '\n## 证据不足信息与后续核验建议\n恢复时间仍无充分证据，不宜作确定判断。\n'
        result = self.prepare(text)
        self.assertNotIn('secret-run', result.markdown)
        self.assertIn('恢复时间仍无充分证据', result.markdown)
        self.assertNotIn('## 本次运行统计', result.markdown)

    def test_local_source_metadata_and_link_are_preserved(self):
        result = self.prepare(sources=[{'file_name': '原始论文.pdf', 'title': '发动机维修研究', 'author': '张某', 'source_type': '报告'}])
        self.assertIn('张某', result.markdown)
        self.assertIn('发动机维修研究', result.markdown)
        self.assertIn('[R]', result.markdown)
        self.assertIn('[R].本地资料.', result.markdown)
        self.assertNotIn('/api/local-library/papers/', result.markdown)
        self.assertNotIn('2024', result.markdown)

    def test_online_journal_reference_uses_gbt_7714_2025_shape(self):
        text = BASE.replace(
            '- [URL8] https://example.org/source',
            '- [URL8] 张骁雄,丁松,范强,等. 多分支特征增强的航空发动机剩余寿命预测方法. 计算机集成制造系统,1-27. https://doi.org/10.13196/j.cims.2026.0129',
        )
        result = self.prepare(text)
        self.assertIn('张骁雄,丁松,范强,等.多分支特征增强的航空发动机剩余寿命预测方法[J/OL].计算机集成制造系统,1-27[', result.markdown)
        self.assertIn('[https://doi.org/10.13196/j.cims.2026.0129](https://doi.org/10.13196/j.cims.2026.0129).', result.markdown)

    def test_inline_source_forms_become_numbered_references(self):
        result = self.prepare(BASE.replace('[URL8]', '[来源URL: https://example.org/source]', 1).replace('[原文2]', '[原文: 原始论文.pdf]', 1))
        self.assertNotIn('[来源URL:', result.markdown)
        self.assertNotIn('[原文:', result.markdown)
        self.assertIn('35天[[1]]', result.markdown)

    def test_interleaved_citation_syntax_retains_reading_order(self):
        text = BASE.replace('型号PW1100G[原文2]', '型号PW1100G[原文: 原始论文.pdf]')
        result = self.prepare(text)
        self.assertIn('35天[[1]]', result.markdown)
        self.assertIn('PW1100G[[2]]', result.markdown)

    def test_invalid_url_definition_requires_review(self):
        result = self.prepare(BASE.replace('https://example.org/source', '子任务分析'))
        self.assertTrue(result.quality['missing_source_ids'])

    def test_internal_entity_audit_is_not_published(self):
        result = self.prepare(BASE + '\n## 核心实体与参数清单（内部核验）\n| 实体/参数 | 数值 |\n| --- | --- |\n| audit_only | 1 |')
        self.assertNotIn('audit_only', result.markdown)

    def test_legacy_entity_audit_is_not_published(self):
        for suffix in ('', '（参与实体评估）'):
            result = self.prepare(BASE + '\n## 核心实体与参数清单' + suffix + '\n| audit_only | 1 |')
            self.assertNotIn('audit_only', result.markdown)

    def test_abstract_gate_matches_approved_length(self):
        for count in (200, 550):
            result = self.prepare(BASE.replace('本报告分析已收集的GTF发动机资料，比较技术问题与维修安排，并讨论证据适用范围。', '文' * count))
            self.assertTrue(any('摘要篇幅' in w for w in result.quality['warnings']))

    def test_unknown_and_substring_local_sources_require_review(self):
        for description in ('来源信息不详', 'invented.pdf', 'data.pdf'):
            text = BASE.replace('[原文2]', '[2]').replace('原始论文.pdf', description)
            result = self.prepare(text, sources=[{'file_name': 'a.pdf', 'title': '错误绑定标题'}])
            self.assertFalse(result.citation_map['[2]']['resolved'])
            self.assertTrue(result.quality['missing_source_ids'])
            self.assertNotIn('错误绑定标题', result.markdown)

    def test_explicit_unknown_local_source_requires_review(self):
        result = self.prepare(BASE.replace('[原文2]', '[原文: invented.pdf]', 1))
        self.assertIn('[原文: invented.pdf]', result.quality['missing_source_ids'])

    def test_linked_claim_text_is_preserved(self):
        result = self.prepare(BASE.replace('35天[URL8]', '[35天](https://example.org/source)', 1))
        self.assertIn('维修周期为35天[[1]]', result.markdown)

    def test_missing_analysis_adds_quality_warning(self):
        text = BASE.replace('## 技术问题', '## 引言')
        result = self.prepare(text)
        self.assertTrue(any('专题分析' in w for w in result.quality['warnings']))

    def test_structure_and_title_are_normalized_without_changing_numbers(self):
        text = BASE.replace('# GTF发动机研究', '# GTF发动机技术问题，重点关注PW1100G与35天维修周期和其他要求')
        text = text.replace('## 技术问题', '## 二、技术问题\n\n**技术问题**')
        result = self.prepare(text)
        self.assertNotIn('重点关注', result.markdown.splitlines()[0])
        self.assertIn('## 1 引言', result.markdown)
        self.assertIn('## 2 资料来源与研究方法', result.markdown)
        self.assertIn('## 3 技术问题', result.markdown)
        self.assertNotIn('**技术问题**', result.markdown)
        self.assertIn('35天', result.markdown)
        self.assertIn('PW1100G', result.markdown)

    def test_fallback_is_explicit_draft(self):
        result = self.prepare(metadata={'generation_status': 'draft', 'generation_warning': '综合写作失败'})
        self.assertEqual(result.quality['status'], 'draft')
        self.assertIn('草稿', result.markdown)

    def test_verification_markers_move_to_audit_without_losing_qualifications(self):
        text = BASE.replace('35天[URL8]', '35天[URL8]（该点待后续核验）', 1)
        text = text.replace('统计口径不同，该比较仍需核实。', '统计口径不同，该比较仍需核实。是否来自同一供应源尚不明确（污染来源是否相同，该点待后续核验）。')
        result = self.prepare(text)
        self.assertNotIn('该点待后续核验', result.markdown)
        self.assertIn('35天[[1]](#ref-1)。', result.markdown)
        self.assertIn('统计口径不同，该比较仍需核实。', result.markdown)
        self.assertIn('（污染来源是否相同）', result.markdown)
        self.assertEqual(len(result.verification_notes), 2)
        self.assertIn('35天', result.verification_notes[0]['context'])

    def test_ascii_verification_markers_are_removed_and_internal_notes_saved(self):
        text = BASE.replace('35天[URL8]', '35天[URL8] ( 待进一步核验 )', 1)
        text += '\n## 待核验事项（内部核验）\n需要获取原始批次清单。\n'
        result = self.prepare(text)
        self.assertNotIn('待进一步核验', result.markdown)
        self.assertNotIn('需要获取原始批次清单', result.markdown)
        self.assertEqual(len(result.verification_notes), 2)

    def test_missing_sections_are_not_misrepresented_as_complete(self):
        result = self.prepare('# 发动机研究\n\n## 分主题发现\n仅有材料摘录。')
        self.assertNotEqual(result.quality['status'], 'ready')
        self.assertIn('## 摘要', result.markdown)
        self.assertIn('资料来源与研究方法', result.markdown)
        self.assertTrue(result.quality['warnings'])

    def test_contents_and_caption_numbers_come_from_actual_content(self):
        text = BASE.replace('## 技术问题', '## 技术问题\n\n![叶片示意](/outputs/report_images/a.png)\n\n*图源：原始论文.pdf，第2页。*\n\n| 型号 | 周期 |\n| --- | --- |\n| PW1100G | 35天 |\n')
        result = self.prepare(text, metadata={'include_toc': True})
        self.assertIn('## 目录', result.markdown)
        self.assertIn('图 1', result.markdown)
        self.assertIn('表 1', result.markdown)
        self.assertIn('第2页', result.markdown)


if __name__ == '__main__':
    unittest.main()
