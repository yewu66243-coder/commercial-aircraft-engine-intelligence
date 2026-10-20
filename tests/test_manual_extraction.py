import unittest

from gpt_researcher.evaluation.manual_extraction import extract_published_rows, cited_catalog


class PublishedReviewExtractionTests(unittest.TestCase):
    def rows(self, report, mapping=None, catalog=None):
        return extract_published_rows(report, mapping or {}, catalog or {'sources': []})

    def test_unused_catalog_mapping_and_bibliography_do_not_create_subjects(self):
        report = ('# 报告\n## 目录\n- LEAP-1A [2]\n## 分析\nGTF维修120台[[1]](#ref-1)。\n'
                  '## 参考文献\n[1] 正文来源 https://example.test/used\n'
                  '[2] LEAP-1A 999台 https://example.test/unused\n')
        catalog = {'sources': [{'locator': 'https://example.test/unused',
                               'pages': [{'text': 'PW1900G维修999台。'}]}]}
        rows = self.rows(report, {'[1]': {'url': 'https://example.test/used'},
                                  '[2]': {'url': 'https://example.test/unused'}}, catalog)
        self.assertEqual({r['name'] for r in rows if r['kind'] == 'entity'}, {'GTF'})
        fact = next(r for r in rows if r['kind'] == 'entity')
        self.assertEqual((fact['attribute'], fact['value'], fact['unit']), ('维修', '120', '台'))
        self.assertEqual([r['source'] for r in rows if r['kind'] == 'source'], ['https://example.test/used'])
        self.assertEqual(cited_catalog(rows, catalog)['sources'], [])
        self.assertTrue(all(r['claim'] == 'GTF维修120台[[1]](#ref-1)。' for r in rows))
        self.assertTrue(all('分析' in r['body_location'] for r in rows))

    def test_uncited_entities_do_not_borrow_candidate_sources(self):
        catalog = {'sources': [{'locator': 'https://example.test/found',
                               'pages': [{'text': 'PW1100G维修200台。'}]}]}
        rows = self.rows('## 分析\nPW1100G维修200台。', catalog=catalog)
        self.assertTrue(rows)
        self.assertTrue(all(r['kind'] == 'entity' and r['source'] == '' and r['evidence'] == [] for r in rows))

    def test_direct_markdown_bare_autolink_and_reference_style_links(self):
        text = ('## 分析\nGTF见[网页](https://example.test/a_(b))。\n'
                'GTF见 https://example.test/b。\nGTF见 <https://example.test/c>。\n'
                'GTF见[网页][official]。\n\n[official]: https://example.test/d\n')
        rows = self.rows(text)
        urls = {r['source'] for r in rows if r['kind'] == 'source'}
        self.assertEqual(urls, {'https://example.test/a_(b)', 'https://example.test/b',
                                'https://example.test/c', 'https://example.test/d'})

    def test_bibliography_resolves_only_actual_numeric_citation_without_map(self):
        rows = self.rows('## 分析\nGTF维修120台[1]。\n## 参考文献\n'
                         '[1] 网页 https://example.test/a\n[2] 网页 https://example.test/b')
        sources = [r for r in rows if r['kind'] == 'source']
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['source'], 'https://example.test/a')
        self.assertEqual(sources[0]['refs'], ['[1]'])

    def test_local_doi_is_not_mislabeled_as_public_web_evidence(self):
        text = ('## 分析\nGTF维修120台[1]。\n## 参考文献\n'
                '[1] 论文 https://doi.org/10.123/test')
        rows = self.rows(text, {'[1]': {'file_name': 'engine.pdf'}})
        self.assertFalse(any(r['kind'] == 'source' for r in rows))
        self.assertTrue(all(r['source'] == 'engine.pdf' for r in rows))

    def test_printed_public_reference_wins_over_stale_mapping_url(self):
        rows = self.rows('## 分析\nGTF[1]。\n## 参考文献\n[1] https://example.test/published',
                         {'[1]': {'url': 'https://example.test/stale'}})
        self.assertEqual([r['source'] for r in rows if r['kind'] == 'source'], ['https://example.test/published'])

    def test_abstract_tables_and_nested_headings_in_excluded_sections(self):
        text = ('# 报告\n## 摘要\nGTF维修120台。\n## 目录\n### PW9999G\nPW9999G[2]。\n'
                '## 分析\n| 型号 | 台数 |\n|---|---|\n| PW1100G | 200台[1] |\n'
                '## 内部核验\n### 子节\nPW8888G[2]。\n## 参考文献\n[2] https://example.test/b')
        rows = self.rows(text, {'[1]': {'url': 'https://example.test/a'},
                                '[2]': {'url': 'https://example.test/b'}})
        names = {r['name'] for r in rows if r['kind'] == 'entity'}
        self.assertEqual(names, {'GTF', 'PW1100G'})
        table = next(r for r in rows if r['name'] == 'PW1100G')
        self.assertIn('200台[1]', table['claim'])
        self.assertIn('表格行', table['body_location'])
        self.assertEqual([r['source'] for r in rows if r['kind'] == 'source'], ['https://example.test/a'])

    def test_images_keywords_code_and_url_slugs_do_not_generate_entities(self):
        text = ('## 分析\n**关键词：** PW8888G\n'
                '![PW7777G](https://example.test/PW7777G)\n'
                '图源：PW6666G https://example.test/PW6666G\n'
                '```\nPW5555G https://example.test/PW5555G\n```\n'
                '详情[网页](https://example.test/PW9999G)。')
        rows = self.rows(text)
        self.assertFalse(any(r['kind'] == 'entity' for r in rows))
        self.assertEqual([r['source'] for r in rows], ['https://example.test/PW9999G'])

    def test_same_url_different_claims_are_separate_but_duplicates_are_not(self):
        text = ('## 分析\nGTF维修120台[1]。\nGTF维修120台[1]。\nGTF维修200台[1]。')
        rows = self.rows(text, {'[1]': {'url': 'https://example.test/a'}})
        sources = [r for r in rows if r['kind'] == 'source']
        self.assertEqual(len(sources), 2)
        self.assertIn('\n', sources[0]['body_location'])

    def test_numeric_label_on_external_link_is_not_a_different_bibliography_citation(self):
        rows = self.rows('## 分析\nGTF见[1](https://example.test/direct)。',
                         {'[1]': {'url': 'https://example.test/other'}})
        self.assertEqual([r['source'] for r in rows if r['kind'] == 'source'], ['https://example.test/direct'])

    def test_citation_after_full_stop_is_not_borrowed_by_next_sentence(self):
        rows = self.rows('## 分析\nGTF维修120台。[[1]](#ref-1)PW1100G维修200台。',
                         {'[1]': {'url': 'https://example.test/a'}})
        entity = next(r for r in rows if r['name'] == 'PW1100G')
        self.assertEqual(entity['source'], '')
        self.assertEqual([r['claim'] for r in rows if r['kind'] == 'source'],
                         ['GTF维修120台。[[1]](#ref-1)'])

    def test_longest_names_do_not_emit_truncated_aliases(self):
        rows = self.rows('## 分析\nGTF Advantage、E-Jet E2、Trent 1000、LEAP-1B、Catalyst、A320neo、A220、737-8采用RBS并修订ALS与ESN记录。')
        names = {r['name'] for r in rows}
        self.assertTrue({'GTF Advantage', 'E-Jet E2', 'Trent 1000', 'LEAP-1B', 'Catalyst', 'A320neo', 'A220', '737-8', 'RBS', 'ALS', 'ESN'} <= names)
        self.assertFalse({'GTF', 'E-Jet', 'LEAP'} & names)
        self.assertIn('GTF', {r['name'] for r in self.rows('## 分析\nGTF维修完成。')})

    def test_complete_units_dates_and_qualifiers(self):
        rows = self.rows('## 分析\nGTF-2型燃油油量表测试仪于1996年6月1日生效，检定周期12个月。\n'
                         'LEAP预计收益提升3个百分点。\nCFM56累计缺发4862天。\n'
                         'GTF维修55次。\nGTF维修125工时。\nGTF预计维修周期6—12个月。')
        quantities = {(r['value'], r['unit']) for r in rows if r.get('value')}
        self.assertTrue({('12', '个月'), ('3', '个百分点'), ('4862', '天'), ('55', '次'), ('125', '工时'), ('6—12', '个月')} <= quantities)
        self.assertFalse(any(v in {'1996', '6', '1'} for v, _ in quantities))
        fact = next(r for r in rows if r.get('value') == '12')
        self.assertEqual(fact['name'], 'GTF-2型燃油油量表测试仪')
        self.assertEqual(fact['attribute'], '检定周期')
        self.assertTrue(any('预计' in r['conditions'] for r in rows))

    def test_ambiguous_object_is_not_guessed_or_dropped(self):
        rows = self.rows('## 分析\nGTF与LEAP共维修120台。')
        fact = next(r for r in rows if r.get('value') == '120')
        self.assertEqual(fact['name'], '对象待确认')
        self.assertIn('不自动绑定', fact['review_note'])

    def test_questions_recommendations_audited_and_embedded_facts_retained(self):
        audit = []
        rows = extract_published_rows('## 分析\n建议提前核对LEAP轮盘。\nGTF是否需要送修？\n'
                                     '建议核对GTF，GTF已维修120台[1]。',
                                     {'[1]': {'url': 'https://example.test/a'}}, {}, audit=audit)
        self.assertEqual([item['statement_type'] for item in audit], ['建议', '研究问题'])
        self.assertTrue(any(r.get('value') == '120' for r in rows))
        self.assertTrue(any(r['kind'] == 'source' for r in rows))

    def test_same_fact_duplicates_merge_but_forecast_actual_differ(self):
        rows = self.rows('## 分析\nGTF维修120台[1]。\nGTF维修120台[2]。\nGTF预计维修120台[1]。',
                         {'[1]': {'url': 'https://example.test/a'}, '[2]': {'url': 'https://example.test/b'}})
        entities = [r for r in rows if r['kind'] == 'entity']
        self.assertEqual(len(entities), 2)
        self.assertEqual(len(entities[0]['occurrences']), 2)
        self.assertEqual(entities[0]['refs'], ['[1]', '[2]'])

    def test_explicit_clauses_and_ambiguous_trailing_group(self):
        mapping = {'[1]': {'url': 'https://example.test/a'}, '[2]': {'url': 'https://example.test/b'}}
        rows = self.rows('## 分析\nGTF维修120台[1]；LEAP维修200台[2]。', mapping)
        sources = [r for r in rows if r['kind'] == 'source']
        self.assertEqual(len(sources), 2)
        self.assertNotIn('LEAP', sources[0]['claim'])
        self.assertNotIn('GTF', sources[1]['claim'])
        grouped = self.rows('## 分析\nGTF维修120台；LEAP维修200台[1][2]。', mapping)
        self.assertEqual(len([r for r in grouped if r['kind'] == 'source']), 2)
        self.assertTrue(all('对应待人工确认' in r['citation_assignment'] for r in grouped))

    def test_unique_stable_identifiers_align_only_existing_cited_sources(self):
        mapping = {'[1]': {'url': 'https://example.test/a'}, '[2]': {'url': 'https://example.test/b'}}
        catalog = {'sources': [{'locator': 'https://example.test/a', 'pages': [{'text': 'CAD1990-B737-06规定3日内检查。'}]},
                               {'locator': 'https://example.test/b', 'pages': [{'text': 'CAD1999-MULT-14规定600飞行小时内检查。'}]}]}
        rows = self.rows('## 分析\nCAD1990-B737-06要求3日内检查；CAD1999-MULT-14要求600飞行小时内检查[1][2]。', mapping, catalog)
        sources = [r for r in rows if r['kind'] == 'source']
        self.assertEqual([r['refs'] for r in sources], [['[1]'], ['[2]']])
        self.assertTrue(all('待人工复核' in r['citation_assignment'] for r in sources))
        catalog['sources'][0]['pages'][0]['text'] += 'CAD1999-MULT-14'
        rows = self.rows('## 分析\nCAD1990-B737-06要求3日；CAD1999-MULT-14要求600飞行小时[1][2]。', mapping, catalog)
        self.assertTrue(all('对应待人工确认' in r['citation_assignment'] for r in rows))

    def test_numeric_table_headers_supply_context_and_units(self):
        rows = self.rows('## 分析\n| 备发数量 | 缺发事件 | 缺发天数 | 租金（万美元） | 支持率（%） |\n'
                         '|---|---|---|---|---|\n| 1 | 55 | 4862 | 1325.5 | 99.533 |')
        self.assertEqual({(r['attribute'], r['value'], r['unit']) for r in rows},
                         {('缺发事件', '55', '次'), ('缺发天数', '4862', '天'),
                          ('租金（万美元）', '1325.5', '万美元'), ('支持率（%）', '99.533', '%')})

    def test_text_table_cells_and_period_units_are_not_dropped_or_invented(self):
        rows = self.rows('## 分析\n| 设备 | 周期 | 批准状态 | 数量 |\n|---|---|---|---|\n'
                         '| GTF-2 | 12个月周期 | 未批准 | 3 |')
        self.assertEqual(len(rows), 3)
        self.assertEqual(next(r for r in rows if r['attribute'] == '周期')['unit'], '个月')
        self.assertEqual(next(r for r in rows if r['attribute'] == '批准状态')['value'], '未批准')
        self.assertEqual(next(r for r in rows if r['attribute'] == '数量')['unit'], '')

    def test_url_punctuation_does_not_split_citation_destination(self):
        rows = self.rows('## 分析\nGTF见[网页](https://example.test/a;b)。')
        self.assertEqual([r['source'] for r in rows if r['kind'] == 'source'], ['https://example.test/a;b'])

    def test_cross_clause_object_inheritance_requires_confirmation(self):
        rows = self.rows('## 分析\nGTF运行中，维修120台。')
        fact = next(r for r in rows if r.get('value') == '120')
        self.assertIn('待人工确认', fact['association'])
        self.assertIn('跨分句', fact['review_note'])

    def test_scope_description_is_audited_but_embedded_observation_is_kept(self):
        audit = []
        rows = extract_published_rows('## 分析\n本文以2023—2026年公开资料为基础，重点考察GTF、LEAP。\n'
                                     '本文以公开资料为基础，GTF已维修120台。', {}, {}, audit=audit)
        self.assertEqual([r['statement_type'] for r in audit], ['研究范围说明'])
        self.assertEqual({r['value'] for r in rows}, {'120'})

    def test_same_url_repeated_with_different_reference_numbers_deduplicates(self):
        rows = self.rows('## 分析\nGTF维修120台[1]。\nGTF维修120台[2]。',
                         {'[1]': {'url': 'https://example.test/a'}, '[2]': {'url': 'https://example.test/a'}})
        sources = [r for r in rows if r['kind'] == 'source']
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['refs'], ['[1]', '[2]'])
        self.assertEqual(len(sources[0]['occurrences']), 2)


if __name__ == '__main__':
    unittest.main()
