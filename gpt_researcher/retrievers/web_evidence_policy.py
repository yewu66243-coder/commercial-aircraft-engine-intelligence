"""Transparent search and evidence selection rules; selection is not verification."""
from datetime import date
import re
from urllib.parse import urlsplit


TOPICS = {
    'technical': ('技术问题 可靠性', 'engine technical issues reliability', ('技术', '粉末', '缺陷', '部件', '可靠性', 'powder', 'defect', 'turbine', 'compressor')),
    'regulatory': ('适航指令 检查 改装', 'airworthiness directive inspection', ('适航', '监管', '通告', '指令', 'airworthiness', 'directive', 'faa', 'easa')),
    'maintenance': ('维修 产能 备发', 'engine maintenance capacity turnaround', ('维修', '保障', '产能', '备发', 'maintenance', 'mro', 'turnaround', 'capacity')),
    'market': ('交付 航司 停飞 影响', 'airline grounded fleet delivery impact', ('市场', '交付', '客户', '停飞', '订单', 'airline', 'grounded', 'delivery', 'fleet')),
}


def subject_terms(query):
    models = re.findall(r'\b(?:PW\d{4}G(?:-JM)?|LEAP(?:-[12][ABC])?|GE9X|GEnx|CFM56|CF6|Trent(?:\s*\d+)?|CJ\d+)\b', query, re.I)
    terms = list(dict.fromkeys(s.lower() for s in models))
    if re.search(r'\bGTF\b|PW(?:1000|1100|1500|1900)G', query, re.I):
        terms += ['gtf', 'pw1000g', 'pw1100g', 'pw1500g', 'pw1900g', 'geared turbofan', '齿轮传动涡扇', '静洁动力']
    return list(dict.fromkeys(terms))


def build_topic_queries(task, subtopic, domains=()):
    """Use four short bilingual searches for recognized engine topics.

    Unrecognized tasks keep the existing model planner. Domain restrictions are
    passed separately; never insert the entire task or every preferred site.
    """
    terms = subject_terms(task)
    if not terms:
        return []
    focus = re.search(r'围绕[“\"]([^”\"]+)|请重点梳理(.+?)(?:。|$)', subtopic)
    focus = next((s for s in focus.groups() if s), '') if focus else subtopic
    ranked = sorted(TOPICS, key=lambda k: sum(w in focus.lower() for w in TOPICS[k][2]), reverse=True)
    topic = ranked[0]
    zh, en, _ = TOPICS[topic]
    if topic == 'technical' and re.search(r'粉末|powder metal', task, re.I):
        zh, en = '粉末金属 缺陷 部件', 'powder metal engine defect components'
    subject = 'GTF' if 'gtf' in terms else terms[0].upper()
    model = next((s.upper() for s in terms if s.startswith('pw') and s != 'pw1000g'), terms[0].upper())
    years = ' '.join(dict.fromkeys(re.findall(r'\b20\d{2}\b', task)))
    domain_order = ('faa.gov', 'easa.europa.eu', 'caac.gov.cn') if topic == 'regulatory' else ('rtx.com', 'prattwhitney.com', 'mtu.de')
    preferred = next((d for d in domain_order if d in domains), None)
    if preferred is None:
        preferred = next((d for d in domains if re.fullmatch(r'[a-zA-Z0-9.-]+', d)), None)
    return list(dict.fromkeys(q.strip() for q in [
        f'{subject} 发动机 {zh} {years}',
        f'{subject} {en} {years}',
        f'{model} {TOPICS[ranked[1]][0]} {years}',
        f'{model} {en} {years}' + (f' site:{preferred}' if preferred else ''),
    ]))


def evidence_assessment(query, title, text, url='', published_date='', today=None):
    """Conservative screening of fetched text, with explainable ranking features."""
    text, title = str(text or ''), str(title or '')
    combined = (title + '\n' + text).lower()
    blocked = re.search(r'just a moment|access denied|安全验证|人机验证|验证[-—_ ]*道客巴巴|captcha|verify you are human', title, re.I)
    if not blocked and len(text) < 1200:
        blocked = re.search(r'验证码|完成验证|访问验证|verify you are human|enable javascript and cookies|checking your browser|captcha', text, re.I)
    reason = '疑似验证码或访问拦截页' if blocked else '未取得足够网页正文' if len(text.strip()) < 100 else ''
    if not reason and re.search(r'/(?:linklist|links|directory)(?:/|$)', urlsplit(url).path, re.I):
        reason = '站点链接目录页，未作为文章原文采用'
    subjects = subject_terms(query)
    hits = [s for s in subjects if s in combined]
    if not reason and subjects and not hits:
        reason = '未命中本任务发动机型号或系列（规则初筛）'
    topics = [k for k, (_, _, words) in TOPICS.items() if any(w in combined for w in words)]
    score = min(12, sum(4 for s in subjects if s in title.lower())) + min(6, len(hits)) + len(topics)
    # Only use supplied publication dates, not dates guessed from URLs or crawl dates.
    recency = '发布日期未知'
    match = re.match(r'(20\d{2})[-/](\d{1,2})[-/](\d{1,2})', str(published_date or ''))
    if match:
        try:
            age = ((today or date.today()) - date(*map(int, match.groups()))).days
            if age >= 0:
                score += 3 if age <= 365 else 1 if age <= 1095 else 0
                recency = '近一年' if age <= 365 else '历史资料'
            else:
                recency = '日期晚于运行日，待确认'
        except ValueError:
            pass
    host = (urlsplit(url).hostname or '').lower()
    if any(host == d or host.endswith('.' + d) for d in ('faa.gov', 'easa.europa.eu', 'caac.gov.cn', 'rtx.com', 'prattwhitney.com', 'mtu.de')):
        score += 1
    return {'evidence_eligible': not bool(reason), 'exclusion_reason': reason,
            'relevance_score': score, 'topics': topics, 'recency': recency}


def passage_score(text, query):
    lowered = text.lower()
    terms = subject_terms(query) + re.findall(r'[a-zA-Z][a-zA-Z0-9-]{2,}', query.lower())
    terms += [w for _, _, words in TOPICS.values() for w in words if w in query.lower()]
    return sum(min(3, lowered.count(t)) for t in set(terms))
