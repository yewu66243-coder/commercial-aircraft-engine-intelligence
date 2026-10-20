"""Conservative, deterministic fact parsing for human review, not fact checking."""
import re

from .evidence_samples import NAME

NON_FACT_TYPES = {'建议', '研究问题', '研究范围说明'}

_DOMAIN_NAMES = re.compile(
    r'(?<![A-Za-z0-9])(?:GTF\s+Advantage|E-Jet\s+E2|E-Jet|'
    r'Trent\s+(?:XWB(?:-\d+)?|\d+)|737\s+MAX|A(?:2\d{2}|3\d{2})(?:neo|ceo)?|7[378]\d(?:-\d+)?|'
    r'GTF-2(?:型燃油油量表测试仪)?|Catalyst|GEnx|GE9X|CFM56|'
    r'LEAP|RTX|MRO|OEM|CFM|IHI|GE|FADEC|TMS|HS\+|Ameco|Cirium|'
    r'RBS|ALS|ESN|ICA|AD|NPRM|SB|TAT|PMA|DER|AMOC|GULL|RISE)'
    r'(?![A-Za-z0-9])|中国民航局|中国民用航空局|巴航工业|三菱重工')
_NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'
_RANGE = _NUMBER + r'(?:\s*[～~—–至-]\s*' + _NUMBER + r')?'
_UNIT = (r'个百分点|飞行小时|缺发天|亿美元|万美元|亿元|万元|个循环|'
         r'个月|小时|分钟|工时|循环|多项|多台|多年|余台|余项|'
         r'架|台|张|套|个|家|项|座|倍|年|月|日|天|次|℃|°C|MPa|kN|%|％')
_QUANTITY = re.compile(r'(?<![A-Za-z0-9.])(?P<value>' + _RANGE + r')\s*(?P<unit>' + _UNIT + r')')
_DATE = re.compile(r'\d{4}(?:年\d{1,2}月(?:\d{1,2}日)?|年|[-/]\d{1,2}(?:[-/]\d{1,2})?)')
_QUALIFIERS = re.compile(r'预计|预期|计划|设计目标|目标|大约|约(?=\s*\d)|超过|至少|以上|以下|不足|截至|同比|累计|每年|月均|年底|后到为准|之前|之后|以内')
_PROPERTY = re.compile(r'检定周期|冷却气流量|叶片温度|在翼时间|耗油率|高空功率|飞行测试|飞行试验|耐久性试验|'
                       r'支持率|保障成本|总成本|租金|收益|缺发事件|缺发天数|缺发|年维修量|维修产能|产能|'
                       r'循环数|利用率|返厂量|返厂|维修|送修|交付|改装|运行|测试|研制历时')


def mentions(text):
    """Prefer full names and never emit nested aliases from the same span."""
    matches = sorted([*NAME.finditer(text), *_DOMAIN_NAMES.finditer(text)],
                     key=lambda m: (m.start(), -len(m[0])))
    selected, end = [], -1
    for match in matches:
        if match.start() < end:
            continue
        selected.append({'name': match[0], 'start': match.start(), 'end': match.end()})
        end = match.end()
    return selected


def quantities(text, names):
    dates = list(_DATE.finditer(text))
    result = []
    for match in _QUANTITY.finditer(text):
        if any(match.start() < n['end'] and match.end() > n['start'] for n in names):
            continue
        if any(match.start() < d.end() and match.end() > d.start() for d in dates):
            continue
        unit = match['unit']
        if unit.startswith(('多', '余')):
            unit = unit[1:]
        if unit == '缺发天':
            unit = '天'
        result.append({'value': match['value'], 'unit': unit,
                       'raw_value': match[0], 'start': match.start(), 'end': match.end()})
    return result


def statement_type(text):
    # Mixed/uncertain cases remain in the factual queue; these labels never judge truth.
    if (re.search(r'[？?]$|(?:是本章要回答的问题|本文据此设定研究问题)', text)
            and not re.search(r'已完成|已获得|数据显示|报道显示|造成|达到', text)):
        return '研究问题'
    if (re.match(r'^\s*(?:据此提出.*建议|建议|后续监测应|应当|应建立|应优先|其[一二三四五六]，|第[一二三四五六]，)', text)
            and not re.search(r'已|曾|于\d{4}|\d+\s*(?:台|架|套|%|％)|数据显示|报道称|完成|获得|发布|造成|达到', text)):
        return '建议'
    if re.search(r'可能|意味着|本文认为|本文据此|可归纳|这一结果.*意义|若.*则', text):
        return '分析推断（需核对前提）'
    return '事实陈述'


def _property(text, quantity):
    suffix = text[quantity['end']:]
    after = _PROPERTY.match(suffix)
    if after:
        return after[0]
    if quantity['raw_value'].endswith('缺发天'):
        return '缺发天数'
    prefix = text[:quantity['start']]
    matches = list(_PROPERTY.finditer(prefix))
    if not matches:
        return '属性待确认'
    # Do not take an attribute from the preceding comma-delimited assertion.
    match = matches[-1]
    if re.search(r'[，,；;|]', prefix[match.end():]):
        return '属性待确认'
    return match[0]


def fact_fields(text, table_headers=None, table_cells=None):
    """Return object/property/value rows; ambiguous relations stay explicitly pending."""
    names = mentions(text)
    values = quantities(text, names)
    qualifiers = '；'.join(dict.fromkeys(_QUALIFIERS.findall(text) + _DATE.findall(text)))
    kind = statement_type(text)
    if (not values and re.match(r'^\s*(?:本文以|本文使用的材料|本文重点考察|研究对象限定|本报告以)', text)
            and not re.search(r'已完成|已获得|数据显示|报道显示|造成|达到', text)):
        kind = '研究范围说明'
    if kind in NON_FACT_TYPES:
        return [], kind
    facts = []
    # Numeric table cells inherit their actual column header, not nearby prose.
    if table_headers and table_cells:
        subject = table_cells[0].strip()
        if re.fullmatch(_NUMBER, subject):
            subject = table_headers[0] + '=' + subject
        for header, cell in zip(table_headers[1:], table_cells[1:]):
            clean = re.sub(r'\[.*?\](?:\([^)]*\))?', '', cell).strip()
            if not clean or clean in {'-', '—'}:
                continue
            match = re.fullmatch(r'(' + _RANGE + r')\s*(.*?)', clean)
            if match:
                unit_match = re.search(r'[（(]([^）)]+)[）)]', header)
                explicit = re.fullmatch(r'\s*(' + _UNIT + r')(?:周期|以上|以下|以内)?\s*', match[2])
                if match[2] and not explicit:
                    match = None
            if match:
                unit = (explicit[1] if explicit else '') or (unit_match[1] if unit_match else '')
                if not unit:
                    unit = {'备发数量': '台', '发动机数量': '台', '台数': '台',
                            '缺发事件': '次', '缺发天数': '天'}.get(header, '')
                value, note = match[1], '' if unit else '单位待人工确认'
            else:
                value, unit, note = clean, '', '文本属性，须核对整格内容'
            facts.append({'name': subject, 'attribute': header, 'value': value, 'unit': unit,
                          'raw_value': clean, 'conditions': table_headers[0] + '=' + table_cells[0].strip(),
                          'statement_type': kind, 'association': '表格同行/列标题', 'review_note': note})
        if facts:
            return facts, kind
    # Model-like names take precedence over generic materials/regulator abbreviations.
    objects = [n for n in names if re.search(r'\d|GTF|Advantage|Catalyst|GEnx|LEAP|Trent|E-Jet', n['name'])]
    objects = objects or names
    unique = list(dict.fromkeys(n['name'] for n in objects))
    if values:
        for quantity in values:
            # Local comma context is used only when it contains exactly one explicit object.
            left = max(text.rfind(c, 0, quantity['start']) for c in '，,；;|') + 1
            right = min([p for c in '，,；;|' if (p := text.find(c, quantity['end'])) >= 0] or [len(text)])
            local = list(dict.fromkeys(n['name'] for n in objects if left <= n['start'] < right))
            subject = local[0] if len(local) == 1 else unique[0] if len(unique) == 1 else ''
            inherited = bool(subject and not local)
            attribute = _property(text, quantity)
            facts.append({'name': subject or '对象待确认', 'attribute': attribute,
                          **{key: quantity[key] for key in ('value', 'unit', 'raw_value')},
                          'conditions': qualifiers, 'statement_type': kind,
                          'association': ('前文单一对象候选（待人工确认）' if inherited
                                          else '分句内单一对象' if subject else '对象关系待确认'),
                          'review_note': '；'.join(filter(None, [
                              '跨分句对象关联须人工确认' if inherited else '',
                              '' if subject else ('多个对象，不自动绑定：' + '、'.join(unique)
                                                   if unique else '未识别到明确对象，不自动绑定'),
                              '属性待人工确认' if attribute == '属性待确认' else '']))})
    # Keep named objects not used in numeric facts, including ambiguous multi-object cases.
    used = {fact['name'] for fact in facts}
    for name in dict.fromkeys(n['name'] for n in names):
        if name not in used:
            facts.append({'name': name, 'attribute': '句内事实关系', 'value': '', 'unit': '',
                          'raw_value': '', 'conditions': qualifiers, 'statement_type': kind,
                          'association': '正文完整名称', 'review_note': '核对完整原句中的关系，不只核对名称出现'})
    return facts, kind
