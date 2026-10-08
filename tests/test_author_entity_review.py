import unittest
from backend.reporting.citations import CitationRegistry
from gpt_researcher.evaluation.evidence_samples import extract_body_candidates
from gpt_researcher.evaluation.independent_judge import build_samples


class AuthorEntityReviewTests(unittest.TestCase):
    def test_editor_resolves_citation_without_changing_repair_anchor(self):
        from backend.reporting.finalization import claim_blocks
        report = '## 分析\nGTF维修120台[甲作者, 2026]。\n## 证据来源列表\n- [URL1] 甲作者 2026 https://example.com/a'
        blocks = claim_blocks(report)
        self.assertEqual(blocks[0]['text'], 'GTF维修120台[甲作者, 2026]。')
        self.assertEqual(blocks[0]['locators'], ['https://example.com/a'])
        self.assertNotIn('2026', blocks[0]['numbers'])

    def test_author_year_group_and_missing_are_not_silently_lost(self):
        text = '## 分析\nGTF[甲作者, 2026; 乙作者, 2025]。\n## 证据来源列表\n- [URL1] 甲作者. 文献甲. 2026. https://example.com/a\n- [URL2] 乙作者. 文献乙. 2025. https://example.com/b'
        registry = CitationRegistry(text)
        result = registry.convert('GTF[甲作者, 2026; 乙作者, 2025]。未知[丙作者, 2025]。')
        self.assertEqual(len(registry.public), 2)
        self.assertIn('[丙作者, 2025]', result)
        self.assertEqual(registry.missing, ['[丙作者, 2025]'])

    def test_same_author_same_year_does_not_guess_a_suffix(self):
        registry = CitationRegistry('## 证据来源列表\n- [URL1] 甲作者 2026 https://example.com/a\n- [URL2] 甲作者 2026 https://example.com/b')
        self.assertEqual(registry.convert('[甲作者, 2026a]'), '[甲作者, 2026a]')
        self.assertTrue(registry.missing)

    def test_uncited_entity_can_be_judged_but_is_not_a_public_citation(self):
        report = '## 分析\nPW1100G在2025年维修120台。'
        candidates = extract_body_candidates(report)
        self.assertNotIn('2025年', [s['name'] for s in candidates])
        self.assertEqual(extract_body_candidates('## 分析\n2025年发生变化。'), [])
        catalog = {'sources': [{'locator': 'engine.pdf', 'pages': [{'page': 2, 'text': 'PW1100G在2025年维修120台。'}]}]}
        rows = build_samples(report, {'extracted_entities': candidates}, catalog)
        self.assertTrue(rows)
        self.assertTrue(all(s['kind'] == 'entity' and s['evidence'] for s in rows))
        self.assertTrue(all(s['evidence_basis'] == 'saved_source_retrieval' for s in rows))
        self.assertEqual(build_samples(report, {'extracted_entities': candidates}, {'sources': []})[0]['evidence'], [])
