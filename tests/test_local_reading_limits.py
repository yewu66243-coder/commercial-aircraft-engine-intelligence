"""UI reading limit must apply independently and equally to each selected pool."""
from collections import Counter
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gpt_researcher.document.local_index import prepare_local_docs_for_query


class LocalReadingLimitsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict('os.environ', {
            'LOCAL_DOCS_PATH': str(self.root),
            'LOCAL_DOCS_INDEX_PATH': str(self.root / 'papers_index.json'),
            'LOCAL_DOCS_POOL_PATH': str(self.root / 'all_papers_pool'),
            'LOCAL_PATENT_INDEX_PATH': str(self.root / 'patents_index.json'),
            'LOCAL_PATENT_POOL_PATH': str(self.root / 'all_patent_pool'),
            'LOCAL_DOCS_TEMP_PATH': str(self.root / 'selected'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for prefix, folder, index in [
            ('paper', 'all_papers_pool', 'papers_index.json'),
            ('user', 'user_docs', 'user_docs_index.json'),
            ('patent', 'all_patent_pool', 'patents_index.json'),
        ]:
            pool = self.root / folder
            pool.mkdir()
            entries = []
            for number in range(22):
                name = f'{prefix}_{number:02d}.txt'
                (pool / name).write_text(f'calibration document {number}', encoding='utf-8')
                entries.append({'title': f'calibration document {number}', 'file_name': name,
                                'abstract': 'calibration procedure and measurements'})
            (self.root / index).write_text(json.dumps(entries), encoding='utf-8')

    def select(self, limit, **scopes):
        return prepare_local_docs_for_query('calibration', max_docs=limit, **scopes)

    async def test_each_pool_honors_one_two_ten_and_twenty(self):
        for limit in (1, 2, 10, 20):
            with self.subTest(limit=limit):
                selection = self.select(limit)
                counts = Counter(item.source_type for item in selection.selected)
                self.assertEqual(counts, {'论文': limit, '用户资料': limit, '专利': limit})
                files = [p for p in Path(selection.doc_path).iterdir() if p.suffix == '.txt']
                self.assertEqual(len(files), 3 * limit)
                sidecar = json.loads((Path(selection.doc_path) / 'selected_sources.json').read_text(encoding='utf-8'))
                self.assertEqual(len(sidecar), 3 * limit)

    async def test_disabled_pools_are_not_selected(self):
        selection = self.select(10, include_papers=False, include_user_docs=True, include_patents=False)
        self.assertEqual(Counter(item.source_type for item in selection.selected), {'用户资料': 10})

    async def test_relevant_document_shortage_does_not_pad_from_other_pools(self):
        index = self.root / 'user_docs_index.json'
        entries = json.loads(index.read_text(encoding='utf-8'))
        for entry in entries[3:]:
            entry.update(title='unrelated document', abstract='other topic')
        index.write_text(json.dumps(entries), encoding='utf-8')
        selection = self.select(10)
        self.assertEqual(Counter(item.source_type for item in selection.selected),
                         {'论文': 10, '用户资料': 3, '专利': 10})

    async def test_service_propagates_frontend_limit_without_old_clamps(self):
        from three_agent_service import ThreeAgentService
        for limit in (1, 2, 10, 20):
            with self.subTest(limit=limit):
                service = ThreeAgentService.__new__(ThreeAgentService)
                service.request = SimpleNamespace(max_search_results=limit)
                service.expanded_task_query = 'calibration'
                service.selected_search_scopes = lambda: {'papers', 'user_docs', 'patents'}
                service.demand_profile_summary = lambda: 'test'
                service._log = Mock()
                await service.pre_search_abstracts()
                self.assertEqual(Counter(item.source_type for item in service.selected_local_papers),
                                 {'论文': limit, '用户资料': limit, '专利': limit})

    async def test_subtopic_reuses_selected_directory_and_web_query_limit(self):
        from three_agent_service import ThreeAgentService
        service = ThreeAgentService.__new__(ThreeAgentService)
        service.request = SimpleNamespace(max_search_results=10)
        service.local_doc_path = str(self.root / 'selected')
        service._log = Mock()
        service._active_model_runtime = lambda: SimpleNamespace(configured=False)
        service.selected_search_scopes = lambda: {'papers', 'user_docs', 'patents'}
        service.effective_report_source = lambda: 'local'
        service.effective_query_domains = lambda: []
        researcher = SimpleNamespace(cfg=SimpleNamespace(), conduct_research=AsyncMock(),
                                     write_report=AsyncMock(return_value='research draft'),
                                     get_research_sources=lambda: [], get_research_context=lambda: [])
        with patch('three_agent_service.GPTResearcher', return_value=researcher):
            await service._research_one('calibration', 1)
        self.assertEqual(researcher.cfg.doc_path, service.local_doc_path)
        self.assertEqual(researcher.cfg.max_search_results_per_query, 10)


if __name__ == '__main__':
    unittest.main()
