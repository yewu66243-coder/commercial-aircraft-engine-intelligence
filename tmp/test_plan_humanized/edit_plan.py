from pathlib import Path
from zipfile import ZipFile
from hashlib import sha256
from lxml import etree as E
import json

TMP = Path(__file__).resolve().parent
ROOT = TMP.parents[1]
SOURCE = ROOT / 'outputs/商用航空发动机情报系统测试方案_参照版.docx'
OUT = ROOT / 'outputs/商用航空发动机情报系统测试方案_文字修订版.docx'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
source_hash = sha256(SOURCE.read_bytes()).hexdigest()
lines = (TMP / 'revised.txt').read_text(encoding='utf-8').splitlines()
changes=[]
with ZipFile(SOURCE) as zin:
    root=E.fromstring(zin.read('word/document.xml'))
    paras=root.find(W+'body').findall(W+'p')
    assert len(paras)==len(lines), (len(paras),len(lines))
    for index, (para, new) in enumerate(zip(paras, lines)):
        ts=list(para.iter(W+'t'))
        old=''.join(t.text or '' for t in ts)
        assert len(ts)==1, 'Preserve existing single-run formatting'
        if old!=new:
            changes.append({'paragraph':index+1,'before':old,'after':new})
            ts[0].text=new
    data=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    with ZipFile(OUT,'w') as zout:
        for item in zin.infolist():
            zout.writestr(item,data if item.filename=='word/document.xml' else zin.read(item.filename))
with ZipFile(SOURCE) as a, ZipFile(OUT) as b:
    assert a.namelist()==b.namelist()
    assert all(a.read(n)==b.read(n) for n in a.namelist() if n!='word/document.xml')
    # Prose-only changes: paragraph/run formatting and section properties identical.
    ar=E.fromstring(a.read('word/document.xml')); br=E.fromstring(b.read('word/document.xml'))
    for doc in [ar,br]:
        for t in doc.iter(W+'t'): t.text=''
    assert E.tostring(ar)==E.tostring(br)
assert source_hash==sha256(SOURCE.read_bytes()).hexdigest()
(TMP/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
(TMP/'artifact.md').write_text('''# Prose revision

Apply installed humanizer SKILL.md, file mode. Preserve technical meaning.
Reference: previous eight-page test plan, unchanged. Retain all 60 paragraphs,
section order, 14 test steps, run formatting and all non-document ZIP members.
A4, 仿宋 16 pt, 28 pt line spacing; no styling or relationship changes.
No changes to fixed sample prompt, UI names, commands, URLs, thresholds or times.

Editorial findings: repeated 预期/展示 labels, redundant cautionary closers,
abstract descriptions such as 研究边界 and 原文断言, repetitive nominal headings
at the start of procedural paragraphs. Replace with direct formal Chinese
instructions; retain genuine failure criteria and technical contrasts.
Keep numbered headings: required by reference and appropriate for test procedures.
No invented experience, test outcomes or factual claims.

Draft review: each revised paragraph compared with the original; exact sample
prompt retained. Check lower-bound quantities, 10 rounds, 20 minutes, 90%, 98%,
per-query cap, unresolved denominator and body-only entity scope. Keep commands,
paths, link targets and UI strings intact. Initial changes logged in changes.json.

Rendering uses the previously diagnosed Word read-only PDF export fallback and
canonical render_docx rasterize with bundled pypdfium2. Final visual QA pending.
''',encoding='utf-8')
print(OUT)
print('Paragraphs',len(lines),'changed',len(changes),'characters',sum(map(len,lines)))
