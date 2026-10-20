import copy
import unittest

from gpt_researcher.evaluation.manual_cleanup import clean_review_rows, text_problem


class ManualCleanupTests(unittest.TestCase):
    def row(self, **kwargs):
        return {'id': 'E1', 'kind': 'entity', 'name': 'GTF', 'claim': 'GTF维修120台[1]。',
                'source': 'https://example.test/a', 'body_location': '分析，第1段',
                'evidence': [{'text': 'GTF维修120台。', 'source': 'https://example.test/a', 'page': 1}],
                **kwargs}

    def test_normal_technical_and_multilingual_text_is_preserved(self):
        for value in ['GTF Advantage维修120台。', 'FAA-2025-0480', 'AD 2025-26-04',
                      'ΔT = 25 °C; η ≥ 90%', 'René 95 / Inconel 718',
                      'MRO output +23% in Q1 2026', 'https://example.test/a?scm=10001',
                      '出现一个替换字符�，其余内容完整可读。']:
            with self.subTest(value=value):
                self.assertEqual(text_problem(value), '')

    def test_empty_and_strong_corruption_signals(self):
        for value in ['', ' \n\t\u200b\ufeff', '---', '???']:
            self.assertEqual(text_problem(value), 'empty')
        for value in ['GTF' + '\ufffd' * 12, '\ue000' * 10, '锟斤拷' * 3,
                      '航空发动机技术研究'.encode('utf-8').decode('latin1'),
                      '测试报告'.encode('utf-8').decode('gb18030')]:
            self.assertEqual(text_problem(value), 'garbled')

    def test_remove_invalid_rows_without_saving_removed_contents(self):
        original = [self.row(), self.row(id='E2', claim=' \u200b'),
                    self.row(id='E3', name='\ufffd' * 10)]
        before = copy.deepcopy(original)
        rows, audit, cleanup = clean_review_rows(original, [])
        self.assertEqual([r['id'] for r in rows], ['E1'])
        self.assertEqual(cleanup['removed_rows'], 2)
        self.assertEqual(audit, [])
        self.assertNotIn('events', cleanup)
        self.assertEqual(original, before)

    def test_missing_sources_delete_whole_rows_even_when_excerpt_exists(self):
        for source in ['', None, ' \n\t\u200b\ufeff']:
            rows, audit, cleanup = clean_review_rows([self.row(source=source)], [])
            self.assertEqual(rows, [])
            self.assertEqual(audit, [])
            self.assertEqual(cleanup['missing_source_rows'], 1)
            self.assertNotIn('events', cleanup)

    def test_nonempty_source_keeps_row_with_empty_excerpt(self):
        for source in ['https://example.test/a', '发动机研究.pdf']:
            rows, audit, cleanup = clean_review_rows([self.row(source=source, evidence=[])], [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(audit, [])
            self.assertEqual(cleanup['removed_rows'], 0)
            self.assertEqual(cleanup['missing_evidence_rows'], 1)

    def test_garbled_excerpt_is_removed_without_dropping_fact(self):
        bad = {'text': '\ufffd' * 15, 'source': 'https://example.test/a'}
        row = self.row(evidence=[bad, {'text': '  '}, {'text': 'GTF维修120台。'}])
        rows, audit, cleanup = clean_review_rows([row], [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['evidence'], [{'text': 'GTF维修120台。'}])
        self.assertEqual(cleanup['removed_excerpts'], 2)
        self.assertEqual(cleanup['missing_evidence_rows'], 0)
        self.assertNotIn('events', cleanup)
        self.assertEqual(audit, [])

    def test_all_excerpts_garbled_leaves_fact_pending(self):
        rows, _, cleanup = clean_review_rows([
            self.row(evidence=[{'text': '\ufffd' * 10}])], [])
        self.assertEqual(rows[0]['evidence'], [])
        self.assertIn('保留待核验', rows[0]['review_note'])
        self.assertEqual(cleanup['missing_evidence_rows'], 1)


if __name__ == '__main__':
    unittest.main()
