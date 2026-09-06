"""Source-grounded automatic editorial finishing, without manual sample edits."""
import asyncio
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from contextlib import ExitStack


def report(value='35%', abstract='甲' * 360):
    return (f'# GTF维修研究\n\n## 摘要\n{abstract}\n\n**关键词：** GTF；维修；产能\n\n'
            '## 1 引言\n研究GTF维修产能与机队的关系。\n\n## 2 资料来源与研究方法\n按本地文献归纳。\n\n'
            f'## 3 维修网络\n2025年维修产能提高{value}，应结合时点比较。[原文1]\n\n'
            '## 4 综合讨论与研究局限\n计划与实际需要分开讨论。\n\n## 5 结论与建议\n持续跟踪维修表现。\n\n'
            '## 证据来源列表\n- [原文1] gtf.pdf\n')


class FinalizationTests(unittest.TestCase):
    def module(self, name):
        self.assertIsNotNone(importlib.util.find_spec('backend.reporting.' + name), name + ' is missing')
        return __import__('backend.reporting.' + name, fromlist=['*'])

    def source(self):
        return {'locator':'gtf.pdf', 'kind':'local', 'file_name':'gtf.pdf', 'title':'GTF',
                'pages':[{'page':1,'text':'2025年GTF维修产能提高35%，计划扩建维修设施。'}]}

    def test_report_editor_round_limit_defaults_to_three_and_is_bounded(self):
        import three_agent_service
        self.assertEqual(three_agent_service.report_editor_max_rounds({}),3)
        self.assertEqual(three_agent_service.report_editor_max_rounds({'REPORT_EDITOR_MAX_ROUNDS':'2'}),2)
        self.assertEqual(three_agent_service.report_editor_max_rounds({'REPORT_EDITOR_MAX_ROUNDS':'0'}),1)
        self.assertEqual(three_agent_service.report_editor_max_rounds({'REPORT_EDITOR_MAX_ROUNDS':'9'}),5)
        self.assertEqual(three_agent_service.report_editor_max_rounds({'REPORT_EDITOR_MAX_ROUNDS':'invalid'}),3)

    def test_catalog_reads_actual_pdf_pages_and_preserves_locators_with_a_budget(self):
        module = self.module('source_grounding')
        import fitz
        with TemporaryDirectory() as directory:
            path = Path(directory)/'gtf.pdf'
            with fitz.open() as doc:
                for text in ['GTF maintenance 2025 capacity increased 35 percent.', 'GTF Advantage supply plan 2026.']:
                    page=doc.new_page(); page.insert_text((40,40),text)
                doc.save(path)
            catalog=module.build_source_catalog([SimpleNamespace(file_name='gtf.pdf',source_path=str(path))],[],allow_web=False)
            self.assertEqual([p['page'] for p in catalog['sources'][0]['pages']],[1,2])
            packed=module.pack_sources(catalog, 'GTF maintenance', budget=800)
            self.assertIn('gtf.pdf',packed)
            self.assertIn('第1页',packed)
            self.assertLessEqual(len(packed),800)

    def test_generated_draft_urls_are_not_registered_as_retrieved_sources(self):
        module=self.module('source_grounding')
        catalog=module.build_source_catalog([], [{'draft':'[URL1] https://fabricated.example/test','sources':[]}],allow_web=False)
        self.assertEqual(catalog['sources'],[])

    def test_pdf_columns_keep_their_original_reading_order(self):
        module=self.module('source_grounding')
        import fitz
        with TemporaryDirectory() as directory:
            path=Path(directory)/'columns.pdf'
            with fitz.open() as doc:
                page=doc.new_page()
                page.insert_text((40,40),'GTF maintenance first line\ncontinued evidence second line')
                page.insert_text((330,40),'UNRELATED RIGHT COLUMN\nmore unrelated material')
                doc.save(path)
            result=module.build_source_catalog([SimpleNamespace(file_name=path.name,source_path=str(path))],[])
            text=result['sources'][0]['pages'][0]['text']
            self.assertIn('GTF maintenance first line\ncontinued evidence second line',text)

    def test_exact_source_with_page_suffix_is_resolved_without_accepting_another_file(self):
        module=self.module('finalization')
        blocks=module.claim_blocks(report())
        review={'checks':[{'id':b['id'],'verdict':'supported','evidence':[
            {'locator':'gtf.pdf 第1页','quote':'2025年GTF维修产能提高35%'}]} for b in blocks],'issues':[]}
        self.assertTrue(module.validate_review(review,blocks,[self.source()])['passed'])
        review['checks'][0]['evidence'][0]['locator']='other.pdf 第1页'
        self.assertFalse(module.validate_review(review,blocks,[self.source()])['passed'])

    def test_large_review_is_bounded_and_covers_every_block_plus_whole_report(self):
        module=self.module('finalization')
        blocks=[{'id':f'B{i}','text':'材料中的内容','locators':['gtf.pdf'],'numbers':[]} for i in range(1,16)]
        prompt='<report>全文</report>\n<blocks>'+json.dumps(blocks)+'</blocks>'
        seen=[]
        async def complete(text,stage):
            import re
            selected=json.loads(re.search(r'<blocks>(.*?)</blocks>',text,re.S)[1])
            seen.append(len(selected))
            return json.dumps({'checks':[{'id':b['id'],'verdict':'supported','evidence':[]} for b in selected],'issues':[]})
        result=asyncio.run(module.collect_review(prompt,complete,blocks))
        self.assertEqual(sorted(c['id'] for c in result['checks']),sorted(b['id'] for b in blocks))
        self.assertTrue(all(n<=6 for n in seen))
        self.assertIn(0,seen)  # Separate full-report coherence/abstract/method/image check.

    def test_small_scoped_review_excludes_unrelated_full_report_text(self):
        module=self.module('finalization')
        blocks=[{'id':'B1','section':'风险专题','text':'2025年提高35%。[原文1]',
                 'locators':['gtf.pdf'],'numbers':['2025','35%']}]
        prompt=('<report>无关章节唯一全文内容</report><original_sources>old</original_sources>'
                '<blocks>'+json.dumps(blocks,ensure_ascii=False)+'</blocks>')
        async def complete(request,stage):
            self.assertNotIn('无关章节唯一全文内容',request)
            self.assertIn('2025年提高35%',request)
            return json.dumps({'checks':[{'id':'B1','verdict':'supported','evidence':[]}],
                               'issues':[]})
        asyncio.run(module.collect_review(
            prompt,complete,blocks,originals=[self.source()],scoped=True))

    def test_contract_flags_unknown_source_and_number_absent_from_cited_original(self):
        module=self.module('finalization')
        sources=[SimpleNamespace(file_name='gtf.pdf')]
        checks=module.check_contract(report('99%'), 'GTF',sources,[self.source()],[], 'research_report')
        self.assertTrue(any(i['kind']=='number_not_located' for i in checks['issues']))
        checks=module.check_contract(report().replace('gtf.pdf','invented.pdf'),'GTF',sources,[self.source()],[],'research_report')
        self.assertTrue(any(i['kind']=='unknown_source' for i in checks['issues']))

    def test_review_requires_all_blocks_and_quotes_from_the_actual_cited_source(self):
        module=self.module('finalization')
        blocks=module.claim_blocks(report())
        result=module.validate_review({'checks':[],'issues':[]},blocks,[self.source()])
        self.assertFalse(result['passed'])
        review={'checks':[{'id':b['id'],'verdict':'supported','evidence':[{'locator':'gtf.pdf','quote':'编造的原文'}]} for b in blocks],'issues':[]}
        result=module.validate_review(review,blocks,[self.source()])
        self.assertFalse(result['passed'])
        self.assertTrue(any(i['kind']=='invalid_quote' for i in result['issues']))

    def test_review_can_reference_immutable_source_passage_without_retyping_it(self):
        module=self.module('finalization')
        passages=module.source_passages([self.source()])
        blocks=module.claim_blocks(report())
        review={'checks':[{'id':b['id'],'verdict':'supported','evidence':[
            {'passage_id':passages[0]['id']}]} for b in blocks],'issues':[]}
        checked=module.validate_review(review,blocks,[self.source()])
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['verified_evidence'][0]['spans'],[self.source()['pages'][0]['text']])
        review['checks'][0]['evidence'][0]['passage_id']='invented'
        self.assertFalse(module.validate_review(review,blocks,[self.source()])['passed'])

    def test_passage_reference_still_requires_cited_source_and_semantic_support(self):
        module=self.module('finalization')
        other={**self.source(),'locator':'other.pdf'}
        passages=module.source_passages([self.source(),other])
        blocks=module.claim_blocks(report())
        review={'checks':[{'id':b['id'],'verdict':'supported','evidence':[
            {'passage_id':passages[-1]['id']}]} for b in blocks],'issues':[]}
        self.assertFalse(module.validate_review(review,blocks,[self.source(),other])['passed'])
        review['checks'][0].update(verdict='unsupported',evidence=[{'passage_id':passages[0]['id']}])
        self.assertFalse(module.validate_review(review,blocks,[self.source(),other])['passed'])

    def test_active_passage_protocol_rejects_legacy_quote_fallback(self):
        module=self.module('finalization')
        blocks=module.claim_blocks(report())
        review={'checks':[{'id':b['id'],'verdict':'supported','evidence':[
            {'locator':'gtf.pdf','quote':'2025年GTF维修产能提高35%'}]} for b in blocks],'issues':[]}
        self.assertFalse(module.validate_review(review,blocks,[self.source()],require_passages=True)['passed'])
        review['checks'][0]['evidence']=[{'passage_id':module.source_passages([self.source()])[0]['id']}]
        self.assertTrue(module.validate_review(review,blocks,[self.source()],require_passages=True)['passed'])

    def test_exact_passage_id_string_is_a_safe_shorthand(self):
        module=self.module('finalization')
        blocks=module.claim_blocks(report())
        pid=module.source_passages([self.source()])[0]['id']
        review={'checks':[{'id':b['id'],'verdict':'supported','evidence':[pid]} for b in blocks],'issues':[]}
        self.assertTrue(module.validate_review(review,blocks,[self.source()],require_passages=True)['passed'])
        for invalid in ['invented', '2025年GTF维修产能提高35%']:
            review['checks'][0]['evidence']=[invalid]
            self.assertFalse(module.validate_review(review,blocks,[self.source()],require_passages=True)['passed'])

    def test_source_labels_and_page_locators_are_not_claimed_quantities(self):
        module=self.module('finalization')
        self.assertEqual(module._numbers('原文3“2025年提高35%”，原文4称扩容40%。原文1第2页图注。'),
                         {'2025','35%','40%'})

    def test_review_cannot_use_real_quote_from_a_different_uncited_source(self):
        module=self.module('finalization')
        blocks=module.claim_blocks(report())
        catalog=[self.source(),{**self.source(),'locator':'other.pdf','file_name':'other.pdf'}]
        review={'checks':[{'id':b['id'],'verdict':'supported','evidence':[{'locator':'other.pdf','quote':'2025年GTF维修产能提高35%'}]} for b in blocks],'issues':[]}
        self.assertFalse(module.validate_review(review,blocks,catalog)['passed'])

    def test_editor_accepts_evidence_correction_instead_of_forcing_the_wrong_number_to_remain(self):
        module=self.module('finalization')
        self.assertTrue(module.usable_edit(report('99%'),report('35%')))
        self.assertFalse(module.usable_edit(report(),'# 只有标题'))

    def test_targeted_repair_changes_only_exact_unique_spans(self):
        module=self.module('finalization')
        draft=report('99%')
        fixed=module.apply_targeted_repair(draft,{'replacements':[{'old':'提高99%','new':'提高35%'}]})
        self.assertEqual(fixed,report('35%'))
        self.assertEqual(module.apply_targeted_repair(draft,{'replacements':[]}),draft)
        for replacements in [[{'old':'not present','new':'x'}],
                             [{'old':'GTF','new':'x'}],
                             [{'old':'提高99%','new':'x'},{'old':'99%','new':'y'}]]:
            with self.assertRaises(ValueError):module.apply_targeted_repair(draft,{'replacements':replacements})

    def test_missing_evidence_is_rechecked_without_changing_the_report_or_dropping_editorial_issues(self):
        module=self.module('finalization')
        blocks=module.claim_blocks(report())
        review={'checks':[{'id':'B1','verdict':'supported','evidence':[]}],
                'issues':[{'section':'结论','reason':'仍需限定推断'}]}
        calls=[]
        async def complete(prompt,stage):
            calls.append(stage)
            self.assertIn('<numeric_candidate_passages>',prompt)
            self.assertIn(module.source_passages([self.source()])[0]['id'],prompt)
            return json.dumps({'checks':[{'id':'B1','verdict':'supported','evidence':[
                module.source_passages([self.source()])[0]['id']]}],'issues':[]})
        revised,trace=asyncio.run(module.recheck_missing_evidence(review,blocks,[self.source()],
            '<blocks>'+json.dumps(blocks)+'</blocks>',complete,lambda *args:None))
        self.assertEqual(calls,['review'])
        self.assertEqual(revised['issues'],review['issues'])
        self.assertEqual(trace['blocks'],['B1'])
        self.assertEqual(module.validate_review(revised,blocks,[self.source()],require_passages=True)['issues'],
                         [{'kind':'editorial_review','section':'结论','reason':'仍需限定推断'}])

    def test_numeric_evidence_retry_rejects_a_wrong_page_and_retries_once(self):
        module=self.module('finalization')
        source={**self.source(),'pages':[
            {'page':1,'text':'2025年开始扩建维修设施。'},
            {'page':2,'text':'2025年GTF维修产能提高35%。'},
        ]}
        blocks=module.claim_blocks(report())
        passages=module.source_passages([source])
        wrong,right=passages[0]['id'],passages[1]['id']
        review={'checks':[{'id':'B1','verdict':'supported','evidence':[wrong]}],'issues':[]}
        calls=[]
        async def complete(prompt,stage):
            calls.append(stage)
            chosen=wrong if len(calls)==1 else right
            return json.dumps({'checks':[{'id':'B1','verdict':'supported','evidence':[chosen]}],
                               'issues':[]})
        revised,trace=asyncio.run(module.recheck_missing_evidence(review,blocks,[source],
            '<blocks>'+json.dumps(blocks)+'</blocks>',complete,lambda *args:None))
        self.assertEqual(calls,['review','review'])
        self.assertEqual(len(trace['attempts']),2)
        self.assertTrue(module.validate_review(revised,blocks,[source],require_passages=True)['passed'])

    def test_unsupported_verdict_is_never_upgraded_by_evidence_retry(self):
        module=self.module('finalization')
        async def run(value):
            blocks=module.claim_blocks(report(value))
            review={'checks':[{'id':'B1','verdict':'unsupported','reason':'原文片段未出现这些数字','evidence':[]}],'issues':[]}
            calls=[]
            async def complete(prompt,stage):
                calls.append(stage)
                return json.dumps({'checks':[{'id':'B1','verdict':'supported','evidence':[
                    module.source_passages([self.source()])[0]['id']]}],'issues':[]})
            revised,trace=await module.recheck_missing_evidence(review,blocks,[self.source()],
                '<blocks>'+json.dumps(blocks)+'</blocks>',complete,lambda *args:None)
            return calls,module.validate_review(revised,blocks,[self.source()],require_passages=True)
        calls,checked=asyncio.run(run('35%'))
        self.assertEqual(calls,[])
        self.assertFalse(checked['passed'])
        calls,checked=asyncio.run(run('99%'))
        self.assertEqual(calls,[])
        self.assertFalse(checked['passed'])

    def test_each_review_batch_receives_only_its_cited_source_files(self):
        module=self.module('finalization')
        sources=[self.source(),{**self.source(),'locator':'other.pdf','file_name':'other.pdf'}]
        blocks=[{'id':f'B{i}','text':'2025年提高35%','locators':['gtf.pdf' if i<6 else 'other.pdf'],
                 'numbers':['2025','35%']} for i in range(7)]
        async def complete(prompt,stage):
            import re
            selected=json.loads(re.search(r'<blocks>(.*?)</blocks>',prompt,re.S)[1])
            packet=[json.loads(row) for row in re.search(r'<original_sources>(.*?)</original_sources>',prompt,re.S)[1].strip().splitlines()]
            expected={loc for b in selected for loc in b['locators']} if selected else {'gtf.pdf','other.pdf'}
            self.assertEqual({p['locator'] for p in packet},expected)
            return json.dumps({'checks':[{'id':b['id'],'verdict':'supported','evidence':[]} for b in selected],'issues':[]})
        asyncio.run(module.collect_review('<original_sources>old</original_sources><blocks>[]</blocks><report>全文</report>',
            complete,blocks,originals=sources))

    def test_semantic_rejection_cannot_be_overridden_by_an_evidence_retry(self):
        module=self.module('finalization')
        source={**self.source(),'pages':[{'page':1,'text':'2025年35%的部件受影响，但该比例不能证明维修产能提高。'}]}
        blocks=module.claim_blocks(report())
        async def unexpected(*args):raise AssertionError('Semantic rejection must reach the editor unchanged')
        for reason in ['原文证据不足以支持产能提高这一因果结论',
                       '来源不足以证明产能提高',
                       '原文未出现产能提高，相反该比例不能证明产能提高',
                       '原文片段未提供足够证据支持产能提高这一结论']:
            review={'checks':[{'id':'B1','verdict':'unsupported','reason':reason,'evidence':[]}],'issues':[]}
            revised,trace=asyncio.run(module.recheck_missing_evidence(review,blocks,[source],
                '<blocks>'+json.dumps(blocks)+'</blocks>',unexpected,lambda *args:None))
            self.assertEqual(revised,review)
            self.assertFalse(module.validate_review(revised,blocks,[source],require_passages=True)['passed'])

    def test_duplicate_review_cannot_override_an_unsupported_claim(self):
        module=self.module('finalization')
        blocks=module.claim_blocks(report())
        proof={'locator':'gtf.pdf','quote':'2025年GTF维修产能提高35%'}
        checks=[{'id':blocks[0]['id'],'verdict':v,'evidence':[proof]} for v in ['unsupported','supported']]
        self.assertFalse(module.validate_review({'checks':checks,'issues':[]},blocks,[self.source()])['passed'])

    def test_failed_editor_keeps_the_complete_draft_and_marks_review_unfinished(self):
        module=self.module('finalization')
        async def fail(*args,**kwargs): raise RuntimeError('offline simulation')
        text,audit=asyncio.run(module.finalize_report(report(),task='GTF',report_type='research_report',
             sources=[SimpleNamespace(file_name='gtf.pdf')],catalog={'sources':[self.source()],'errors':[]},
             images=[],method_context='本地资料归纳',complete=fail,log=lambda *args:None))
        self.assertEqual(text,report())
        self.assertEqual(audit['status'],'incomplete')
        self.assertFalse(audit['passed'])

    def test_repair_is_rechecked_and_is_not_marked_passed_with_remaining_content_gaps(self):
        module=self.module('finalization')
        calls=[]
        async def complete(prompt,stage):
            calls.append(stage)
            if stage=='edit': return report('99%')
            if stage=='repair': return json.dumps({'replacements':[{'old':'提高99%','new':'提高35%'}]})
            blocks=module.claim_blocks(report())
            return json.dumps({'checks':[{'id':b['id'],'verdict':'supported',
                'evidence':[{'passage_id':module.source_passages([self.source()])[0]['id']}]} for b in blocks],'issues':[]},ensure_ascii=False)
        text,audit=asyncio.run(module.finalize_report(report('99%'),task='GTF',report_type='research_report',
             sources=[SimpleNamespace(file_name='gtf.pdf')],catalog={'sources':[self.source()],'errors':[]},
             images=[],method_context='本地资料归纳',complete=complete,log=lambda *args:None,max_rounds=2))
        self.assertEqual(calls,['edit','review','repair','review'])
        self.assertIn('提高35%',text)
        self.assertNotIn('提高99%',text)
        self.assertFalse(audit['passed'])  # The deliberately short fixture still fails content depth.
        self.assertFalse(any(i['kind']=='quote_numbers' for i in audit['remaining_issues']))

    def test_pdf_whitespace_does_not_change_percentage_and_thousand_separator(self):
        module=self.module('finalization')
        self.assertEqual(module._numbers('产能提高35 %，检查2,500台'),{'35%','2500'})

    def test_resume_uses_only_the_remaining_round_and_requires_the_saved_text(self):
        module=self.module('finalization')
        calls=[]
        prior={'rounds':[{'report':report()},{'report':report()}],'remaining_issues':[],'passed':False,
               'reason':'Prior connection failure'}
        async def complete(prompt,stage):
            calls.append(stage)
            if stage=='repair':return json.dumps({'replacements':[]})
            return json.dumps({'checks':[{'id':'B1','verdict':'supported','evidence':[
                {'passage_id':module.source_passages([self.source()])[0]['id']}]}],'issues':[]})
        kwargs=dict(task='GTF',report_type='research_report',sources=[SimpleNamespace(file_name='gtf.pdf')],
                    catalog={'sources':[self.source()],'errors':[]},images=[],method_context='本地资料',
                    complete=complete,log=lambda *args:None,previous_audit=prior,max_rounds=3)
        text,audit=asyncio.run(module.finalize_report(report(),**kwargs))
        self.assertEqual(calls,['repair','review'])
        self.assertEqual(len(audit['rounds']),3)
        self.assertNotIn('reason',audit)
        self.assertEqual(len(prior['rounds']),2)
        with self.assertRaises(ValueError):asyncio.run(module.finalize_report(report('99%'),**kwargs))

    def test_saved_ten_round_report_gets_one_bounded_final_repair(self):
        module=self.module('finalization')
        prior={'version':'source-editor-v4','rounds':[{'report':report()} for _ in range(10)],
               'remaining_issues':[],'passed':False}
        calls=[]
        async def complete(prompt,stage):
            calls.append(stage)
            if stage=='repair':return json.dumps({'replacements':[]})
            return json.dumps({'checks':[{'id':'B1','verdict':'supported','evidence':[
                module.source_passages([self.source()])[0]['id']]}],'issues':[]})
        _,audit=asyncio.run(module.finalize_report(report(),task='GTF',report_type='research_report',
            sources=[SimpleNamespace(file_name='gtf.pdf')],catalog={'sources':[self.source()],'errors':[]},
            images=[],method_context='本地资料',complete=complete,log=lambda *args:None,
            previous_audit=prior,max_rounds=11))
        self.assertEqual(calls,['repair','review'])
        self.assertEqual(len(audit['rounds']),11)

    def test_default_policy_runs_no_more_than_three_review_rounds(self):
        module=self.module('finalization')
        passage_id=module.source_passages([self.source()])[0]['id']
        review_calls=[]
        repair_calls=0

        async def complete(prompt,stage):
            nonlocal repair_calls
            if stage=='edit':
                return report()
            if stage=='repair':
                repair_calls += 1
                current=report() if repair_calls==1 else report().replace(
                    '持续跟踪维修表现。', '持续跟踪维修表现。' + '补充' * (repair_calls-1))
                old='持续跟踪维修表现。' + '补充' * (repair_calls-1)
                return json.dumps({'replacements':[{'old':old,'new':old+'补充'}]},ensure_ascii=False)
            import re
            selected=json.loads(re.search(r'<blocks>\n?(.*?)\n?</blocks>',prompt,re.S)[1])
            review_calls.append([block['id'] for block in selected])
            return json.dumps({'checks':[{'id':block['id'],'verdict':'supported',
                'evidence':[{'passage_id':passage_id}]} for block in selected],
                'issues':[{'section':'全文','reason':'保留一项用于验证轮次上限'}]},ensure_ascii=False)

        _,audit=asyncio.run(module.finalize_report(report(),task='GTF',report_type='research_report',
            sources=[SimpleNamespace(file_name='gtf.pdf')],catalog={'sources':[self.source()],'errors':[]},
            images=[],method_context='本地资料',complete=complete,log=lambda *args:None))
        self.assertGreaterEqual(len(review_calls),1)
        self.assertLessEqual(len(review_calls),3)
        self.assertEqual(audit['max_rounds'],3)
        self.assertLessEqual(len(audit['rounds']),3)
        self.assertTrue(all(item['duration_seconds']>=0 for item in audit['rounds']))

    def test_unchanged_targeted_repair_stops_before_another_review(self):
        module=self.module('finalization')
        passage_id=module.source_passages([self.source()])[0]['id']
        calls=[]

        async def complete(prompt,stage):
            calls.append(stage)
            if stage=='edit':
                return report()
            if stage=='repair':
                return json.dumps({'replacements':[]})
            blocks=module.claim_blocks(report())
            return json.dumps({'checks':[{'id':block['id'],'verdict':'supported',
                'evidence':[{'passage_id':passage_id}]} for block in blocks],
                'issues':[{'section':'全文','reason':'仍需调整'}]},ensure_ascii=False)

        _,audit=asyncio.run(module.finalize_report(report(),task='GTF',report_type='research_report',
            sources=[SimpleNamespace(file_name='gtf.pdf')],catalog={'sources':[self.source()],'errors':[]},
            images=[],method_context='本地资料',complete=complete,log=lambda *args:None))
        self.assertEqual(calls,['edit','review','repair'])
        self.assertEqual(len(audit['rounds']),1)
        self.assertEqual(audit['stop_reason'],'no_effective_change')
        self.assertEqual(audit['attempts'][-1]['result'],'no_effective_change')
        self.assertGreaterEqual(audit['attempts'][-1]['duration_seconds'],0)

    def test_followup_review_selects_issue_block_and_neighbors(self):
        module=self.module('finalization')
        blocks=[{'id':f'B{i}','section':f'章节{i}','text':f'段落{i}','locators':['gtf.pdf'],'numbers':[]}
                for i in range(1,6)]
        selected=module.select_risk_blocks(blocks,[{'section':'B3','reason':'需修订'}])
        self.assertEqual([block['id'] for block in selected],['B2','B3','B4'])

    def test_any_unmapped_global_issue_forces_full_followup_context(self):
        module=self.module('finalization')
        blocks=[{'id':f'B{i}','section':f'章节{i}','text':f'段落{i}','locators':['gtf.pdf'],'numbers':[]}
                for i in range(1,6)]
        selected=module.select_risk_blocks(blocks,[
            {'block':'B3','reason':'局部问题'},
            {'section':'摘要','reason':'全局同步问题'},
        ])
        self.assertEqual(selected,blocks)

    def test_followup_review_falls_back_to_full_when_claim_structure_changes(self):
        module=self.module('finalization')
        before=[{'id':f'B{i}','section':f'章节{i}','text':f'段落{i}','locators':['gtf.pdf'],'numbers':[]}
                for i in range(1,4)]
        after=before[:1]+[
            {'id':'B2','section':'章节2','text':'拆分段落上','locators':['gtf.pdf'],'numbers':[]},
            {'id':'B3','section':'章节2','text':'拆分段落下','locators':['gtf.pdf'],'numbers':[]},
            {'id':'B4','section':'章节3','text':'段落3','locators':['gtf.pdf'],'numbers':[]},
        ]
        selected,reason=module.select_followup_review_blocks(
            before,after,[{'block':'B2','reason':'需修订'}])
        self.assertEqual(selected,after)
        self.assertEqual(reason,'full_structure_changed')

    def test_followup_review_covers_the_entire_touched_section_when_ids_stay_stable(self):
        module=self.module('finalization')
        before=[{'id':f'B{i}','section':'同一专题','text':f'原段落{i}','locators':['gtf.pdf'],'numbers':[]}
                for i in range(1,8)]
        after=[{**block,'text':('修订段落4' if block['id']=='B4' else block['text'])}
               for block in before]
        selected,reason=module.select_followup_review_blocks(
            before,after,[{'block':'B4','reason':'需修订'}])
        self.assertEqual(selected,after)
        self.assertEqual(reason,'risk_sections')

    def test_followup_repair_context_excludes_unrelated_sections_and_sources(self):
        module=self.module('finalization')
        draft=(report().replace('持续跟踪维修表现。',
            '持续跟踪维修表现。另一章节唯一内容。'))
        focus=module.select_risk_blocks(module.claim_blocks(draft),[
            {'block':'B1','reason':'核对维修产能'}])
        other={**self.source(),'locator':'other.pdf','file_name':'other.pdf','title':'Other',
               'pages':[{'page':1,'text':'另一来源唯一证据。'}]}
        context=module.build_targeted_repair_context(draft,focus,[self.source(),other])
        self.assertIn('2025年维修产能提高35%',context['report'])
        self.assertNotIn('另一章节唯一内容',context['report'])
        self.assertIn('gtf.pdf',context['evidence'])
        self.assertNotIn('另一来源唯一证据',context['evidence'])
        self.assertLess(len(context['evidence']),65000)

    def test_bounded_editor_returns_the_best_reviewed_round_not_a_worse_last_round(self):
        module=self.module('finalization')
        better=report('35%')
        worse=report('99%')
        prior={'version':'source-editor-v4','rounds':[
            {'round':1,'report':better,'contract':{'issues':[]},
             'review_validation':{'issues':[{'kind':'editorial_review','section':'B1','reason':'一项'}]}},
            {'round':2,'report':worse,'contract':{'issues':[{'kind':'number_not_located','reason':'新增错误'}]},
             'review_validation':{'issues':[{'kind':'editorial_review','section':'B1','reason':str(i)} for i in range(3)]}},
        ],'remaining_issues':[{'kind':'number_not_located','reason':'新增错误'}],
               'passed':False,'status':'needs_review'}
        async def unexpected(*args):raise AssertionError('No round remains')
        text,audit=asyncio.run(module.finalize_report(worse,task='GTF',report_type='research_report',
            sources=[SimpleNamespace(file_name='gtf.pdf')],catalog={'sources':[self.source()],'errors':[]},
            images=[],method_context='本地资料',complete=unexpected,log=lambda *args:None,
            previous_audit=prior,max_rounds=2))
        self.assertEqual(text,better)
        self.assertEqual(audit['selected_round'],1)
        self.assertEqual(len(audit['remaining_issues']),1)

    def test_best_round_prioritizes_contract_integrity_over_total_issue_count(self):
        module=self.module('finalization')
        contract_clean={'round':1,'contract':{'issues':[]},
                        'review_validation':{'issues':[{'kind':'editorial_review'} for _ in range(2)]}}
        contract_broken={'round':2,'contract':{'issues':[{'kind':'number_not_located'}]},
                         'review_validation':{'issues':[]}}
        self.assertLess(module.round_quality_key(contract_clean),
                        module.round_quality_key(contract_broken))

    def test_report_progress_tracks_stage_round_and_remaining_range(self):
        import three_agent_service
        task_id='progress-unit-test'
        three_agent_service.initialize_report_progress(task_id,'GTF',max_rounds=3)
        three_agent_service.update_report_progress(
            task_id,'Editorial Agent','正在对照原文进行第 2 轮成稿校订。')
        progress=three_agent_service.get_report_progress(task_id)
        self.assertEqual(progress['status'],'running')
        self.assertEqual(progress['stage'],'成稿校订与来源复查')
        self.assertEqual(progress['current_round'],2)
        self.assertEqual(progress['max_rounds'],3)
        self.assertLessEqual(progress['estimated_remaining_minutes']['min'],
                             progress['estimated_remaining_minutes']['max'])
        three_agent_service.clear_report_progress(task_id)
        self.assertIsNone(three_agent_service.get_report_progress(task_id))

    def test_report_progress_never_regresses_and_records_stage_durations(self):
        import three_agent_service
        task_id='progress-timing-test'
        with patch('three_agent_service.time.time',side_effect=[100,102,105,109,115,120,124,130]):
            three_agent_service.initialize_report_progress(task_id,'GTF',max_rounds=3)
            three_agent_service.update_report_progress(task_id,'Research Agent','开始专题研究。')
            three_agent_service.update_report_progress(task_id,'Source Reader','建立来源索引。')
            three_agent_service.update_report_progress(task_id,'Writer Agent','开始汇总写作。')
            writer=three_agent_service.get_report_progress(task_id)
            three_agent_service.update_report_progress(task_id,'Image Evidence Agent','补入图片。')
            image_after_writer=three_agent_service.get_report_progress(task_id)
            three_agent_service.update_report_progress(task_id,'Evaluation Agent','核验引用。')
            three_agent_service.update_report_progress(task_id,'Editorial Agent','执行第 1 轮成稿校订。')
            three_agent_service.update_report_progress(
                task_id,'Evaluation Agent','正在统计运行耗时并检测最终报告中的公开 URL 可访问性。')
        final=three_agent_service.get_report_progress(task_id)
        self.assertEqual(writer['stage'],'汇总撰写')
        self.assertEqual(image_after_writer['stage'],'汇总撰写')
        self.assertEqual(image_after_writer['estimated_remaining_minutes'],
                         writer['estimated_remaining_minutes'])
        self.assertEqual(final['stage'],'最终证据评估')
        self.assertEqual(final['stage_durations_seconds']['准备任务'],2)
        self.assertEqual(final['stage_durations_seconds']['检索与专题研究'],3)
        self.assertEqual(final['stage_durations_seconds']['建立原文索引'],4)
        self.assertEqual(final['stage_durations_seconds']['汇总撰写'],11)
        self.assertEqual(final['stage_durations_seconds']['证据与引用核验'],4)
        self.assertEqual(final['stage_durations_seconds']['成稿校订与来源复查'],6)
        three_agent_service.clear_report_progress(task_id)

    def test_stitched_quote_keeps_only_verified_ordered_literal_clauses(self):
        module=self.module('finalization')
        source='2025年维修能力提高35%，仅限指定设施，不代表停场问题已解决；仍需检查更换部件。'
        self.assertEqual(module.verified_quote_spans('2025年维修能力提高35%，仍需检查更换部件。',source),
                         ['2025年维修能力提高35%','仍需检查更换部件'])
        self.assertEqual(module.verified_quote_spans('2025年维修能力提高99%，仍需检查更换部件。',source),[])
        self.assertEqual(module.verified_quote_spans('2025年维修能力提高35%，代表停场问题已解决。',source),
                         [])
        # Substrings alone cannot validate semantics: the reviewer must still
        # reject a negation error even when the source contains those characters.
        block={'id':'B1','locators':['gtf.pdf'],'numbers':[],'text':'停场问题已解决。'}
        reviewed={'checks':[{'id':'B1','verdict':'unsupported','reason':'否定被删','evidence':[]}],'issues':[]}
        self.assertFalse(module.validate_review(reviewed,[block],[self.source()])['passed'])

    def test_empty_review_cannot_be_considered_a_pass(self):
        module=self.module('finalization')
        checked=module.validate_review({'checks':[],'issues':[]},[],[self.source()])
        self.assertFalse(checked['passed'])
        self.assertTrue(checked['issues'])

    def test_table_caption_does_not_hide_the_numeric_rows_below_it(self):
        module=self.module('finalization')
        raw='## 3 比较\n\n表 1 型号比较\n| 型号 | 提升 |\n| --- | --- |\n| GTF | 99%[原文1] |\n\n## 证据来源列表\n- [原文1] gtf.pdf'
        blocks=module.claim_blocks(raw)
        self.assertEqual(len(blocks),1)
        self.assertIn('99%',blocks[0]['numbers'])

    def test_heading_without_blank_line_does_not_hide_preceding_claim(self):
        module=self.module('finalization')
        raw='## 3 维修网络\n2025年产能提高99%。[原文1]\n## 证据来源列表\n- [原文1] gtf.pdf'
        blocks=module.claim_blocks(raw)
        self.assertEqual(len(blocks),1)
        self.assertIn('99%',blocks[0]['numbers'])

    def test_final_editor_runs_after_citation_cleanup_before_the_shared_export(self):
        from three_agent_service import ThreeAgentService,ThreeAgentRequestData
        service=ThreeAgentService(ThreeAgentRequestData(task='GTF'))
        order=[]
        def cleanup(text,stats):
            order.append('cleanup');return text.replace('提高99%','提高88%'),{'changed':True}
        async def edit(text):
            order.append('edit');self.assertIn('提高88%',text)
            return text.replace('提高88%','提高35%')
        with ExitStack() as stack:
            for method,value in [('pre_search_abstracts',None),('research_agent',[]),('writer_agent',report('99%')),('inspect_report_urls',{})]:
                stack.enter_context(patch.object(service,method,new=AsyncMock(return_value=value)))
            stack.enter_context(patch.object(service,'planner_agent',return_value=[]))
            stack.enter_context(patch.object(service,'collect_report_images'))
            stack.enter_context(patch.object(service,'editorial_agent',side_effect=edit))
            stack.enter_context(patch.object(service,'append_evaluation_record',return_value='offline'))
            stack.enter_context(patch('three_agent_service.evaluate_public_url_sources',return_value={}))
            stack.enter_context(patch('three_agent_service.prune_redundant_unchecked_url_citations',side_effect=cleanup))
            stack.enter_context(patch('three_agent_service.evaluate_report_entities',return_value={}))
            exports=[]
            for method in ['write_text_to_md','write_md_to_pdf','write_md_to_word']:
                exports.append(stack.enter_context(patch('three_agent_service.'+method,new=AsyncMock(return_value='outputs/test'))))
            result=asyncio.run(service.run())
        self.assertEqual(order,['cleanup','edit'])
        self.assertTrue(all('提高35%' in e.call_args.args[0] for e in exports))
        self.assertTrue(all(result['report']==e.call_args.args[0] for e in exports))

    def test_allowed_image_still_requires_a_real_file_and_adjacent_source(self):
        module=self.module('finalization')
        with TemporaryDirectory() as directory:
            path=Path(directory)/'gtf.png'
            from PIL import Image
            Image.new('RGB',(300,200),'gray').save(path)
            images=[{'markdown_path':'/outputs/report_images/gtf.png','image_path':str(path),
                     'caption_matched':True,'source_file':'gtf.pdf','page':1}]
            raw=report().replace('## 4 综合讨论','![GTF维修](/outputs/report_images/gtf.png)\n\n## 4 综合讨论')
            checks=module.check_contract(raw,'GTF',[SimpleNamespace(file_name='gtf.pdf')],[self.source()],images,'research_report')
            self.assertTrue(any(i['kind']=='image_source' for i in checks['issues']))
            with_source=raw.replace('![GTF维修](/outputs/report_images/gtf.png)',
                 '![GTF维修](/outputs/report_images/gtf.png)\n\n图源：gtf.pdf，第1页。[原文1]')
            checks=module.check_contract(with_source,'GTF',[SimpleNamespace(file_name='gtf.pdf')],[self.source()],images,'research_report')
            self.assertFalse(any(i['kind'].startswith('image') for i in checks['issues']))


if __name__=='__main__': unittest.main()
