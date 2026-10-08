from pathlib import Path
p = Path('backend/reporting/finalization.py')
s = p.read_text(encoding='utf-8')
start = s.index('def sentence_needs_reference(')
end = s.index('def claim_blocks(', start)
s = s[:start] + s[end:]
start = s.index("        for sentence in body_units('## Body\\n' + block['text']):")
end = s.index('        known = ', start)
s = s[:start] + s[end:]
s = '\n'.join(line for line in s.split('\n') if 'from gpt_researcher.evaluation.evidence_samples import body_units, NAME, QUANTITY' not in line)
s = '\n'.join("            prompt += '\\n正文引用保留来源编号与完整文件名或URL；不要求每句话都带引用。无引用实体由独立核验在本轮原文库中检索证据。'" if "prompt += '\\n每个事实句" in line else line for line in s.split('\n'))
p.write_text(s, encoding='utf-8')
p = Path('backend/reporting/prompts.py')
s = p.read_text(encoding='utf-8')
s = '\n'.join('正文可使用适量引用，不要求每句话都标注来源。保留实体的数值、时间和条件属性，供独立核验检索本轮原文。' if line.startswith('每个含型号、数值、时间') else line for line in s.split('\n'))
p.write_text(s, encoding='utf-8')
p = Path('backend/reporting/evaluation_report.py')
s = p.read_text(encoding='utf-8').replace('未标明引用的语句不会自动继承其他句子的来源。', '无引用实体会在本轮原文库检索证据，不自动继承其他句子的来源；检索命中不代表事实正确。')
p.write_text(s, encoding='utf-8')
