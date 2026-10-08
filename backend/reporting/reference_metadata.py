"""Extract bibliographic fields from source pages, independently of report prose."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def _normalize_pdf_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    return text.replace("\x11", " ").replace("\uf02a", "").replace("\u3000", " ")


def _clean_people(value: str) -> str:
    value = re.sub(r"\s+", "", str(value or ""))
    value = re.sub(r"(?:研究生姓名|作者简介|通信作者|联系|人|教授|博士研究生|硕士研究生).*", "", value)
    parts = [p for p in re.split(r"[,，、;；]", value) if p]
    return "; ".join(parts[:4])


def extract_page_metadata(pages, source_type="论文"):
    result, provenance = {}, {}

    def put(key, value, page):
        if value and key not in result:
            result[key] = value
            provenance[key] = {"page": page, "method": "local_page"}

    printed_pages = []
    for item in pages:
        page = item.get("page")
        text = _normalize_pdf_text(item.get("text", ""))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        header = "\n".join(lines[:8])
        margins = _normalize_pdf_text(item.get('margin_text', header))
        doi = re.search(r'\bDOI\s*[:：]\s*(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', margins, re.I)
        if not doi:
            doi = re.search(r'\bdoi\s*[:：]\s*(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', text, re.I)
        if doi:
            put('doi', doi[1].rstrip('.'), page)
        translator = re.search(r'[（(]([\u4e00-\u9fff]{2,6})[，,]\s*译自', text)
        if translator:
            put('translator', translator[1], page)
        if re.search(r"专利|patent", source_type, re.I):
            number = re.search(r"\b(CN\s*\d{8,12}\s*[A-Z]\d?)\b", text)
            if number:
                put("patent_number", re.sub(r"\s+", "", number[1]), page)
            # Publication dates often occur next to the repeated publication number.
            date = re.search(r"CN\s*\d{8,12}\s*[A-Z]\d?\s*\n\s*(20\d{2})[.\-/](\d{2})[.\-/](\d{2})", text)
            if not date:
                date = re.search(r"(?:授权公告日|申请公布日)\s*(20\d{2})[.\-/](\d{2})[.\-/](\d{2})", text)
            if date:
                put("publication_date", "-".join(date.groups()), page)
                put("year", date[1], page)
            owner = re.search(r"\(7[13]\)\s*(?:专利权人|申请人)\s*([\s\S]+?)(?=\n\s*地址|\n\s*\(\d+\))", text)
            if owner:
                put("applicant", re.sub(r"\s+", "", owner[1]), page)
            continue
        if re.search(r"博士学位论文|硕士学位论文", text):
            put("degree", "博士学位论文" if "博士学位论文" in text else "硕士学位论文", page)
            put("source_type", "学位论文", page)
            title = re.search(r"(?:博士|硕士)学位论文\s+([\s\S]{4,120}?)(?=\s+研究生姓名|\s+姓名|\s+学科[、,，])", text)
            if title:
                put("title", re.sub(r"\s+", "", title[1]), page)
            author = re.search(r"研究生姓名\s*([\u4e00-\u9fff\s]{2,8}?)(?=\s*(?:学科|专业|研究方向|指导教师))", text)
            if author:
                put("author", re.sub(r"\s+", "", author[1]), page)
            institution = re.search(r"(南京航空航天大学|北京航空航天大学|西北工业大学|[\u4e00-\u9fff]{2,20}大学)", text)
            if institution:
                put("degree_institution", institution[1], page)
            year = re.search(r"(20\d{2}|19\d{2})\s*年", text)
            if not year:
                year = re.search(r"二\s*[OО0零〇]{2}\s*([一二三四五六七八九])\s*年", text)
            if year:
                chinese_digits = "一二三四五六七八九"
                value = "200" + str(chinese_digits.index(year[1]) + 1) if len(year[1]) == 1 else year[1]
                put("year", value, page)
            continue
        citation_line = re.search(
            r"本文引用格式[:：]\s*([\u4e00-\u9fffA-Za-z,，、;；\s]+?)\.\s*([^.\n。]+?)\[J\]\.\s*([^,，。\n]+)[,，]\s*(20\d{2})[,，]\s*([^。\n]+)",
            text,
        )
        if citation_line:
            put("author", _clean_people(citation_line[1]), page)
            put("title", re.sub(r"\s+", "", citation_line[2]), page)
            put("container", citation_line[3].strip(), page)
            put("year", citation_line[4], page)
            vip = citation_line[5].strip().replace(" ", "")
            vol_issue_pages = re.sub(r"(\d+)\s*\((\d+)\)\s*[:：]\s*([\d\-]+).*", r"\1(\2):\3", vip)
            if vol_issue_pages:
                put("volume_issue_pages", vol_issue_pages, page)
        # Only marginal text is used for publication dates, never years in titles/body.
        journal = re.search(r"^([\u4e00-\u9fff]{2,20})(?:\s*[I|｜]\s*[A-Za-z]|\s*$)", header, re.M)
        has_imprint = bool(re.search(r"20\d{2}\s*(?:/\s*\d{1,2}|年\s*第\s*\d+\s*期)", header))
        if not has_imprint:
            has_imprint = bool(re.search(r"20\d{2}年\s+第\d+卷\s+\d+月\s+第\d+期", text))
        if not journal:
            journal = re.search(r"第\d+卷\s+第\d+期\s*\n\s*([\u4e00-\u9fff]{2,20})", text)
        if not journal:
            journal = re.search(r"第\d+卷\s+\d+月\s+第\d+期\s*\n\s*([\u4e00-\u9fff]{2,20})", text)
        if journal and has_imprint:
            put("container", journal[1], page)
        masthead = re.search(r'^([\u4e00-\u9fff]{2,20})\n[^\n]{0,25}\n[A-Z][A-Z &]+\n\d{1,4}(?:\n|$)', header)
        if masthead:
            put('container', masthead[1], page)
        stamp = re.search(r"(20\d{2})\s*(?:/\s*(\d{1,2})|年\s*第\s*(\d+)\s*期)", header)
        if not stamp:
            stamp = re.search(r"(20\d{2})年\s+第(\d+)卷\s+\d+月\s+第(\d+)期", text)
        if stamp:
            put("year", stamp[1], page)
            if len(stamp.groups()) >= 3 and stamp[2] and stamp[3]:
                put("volume", str(int(stamp[2])), page)
                put("issue", str(int(stamp[3])), page)
            else:
                put("issue", str(int(stamp[2] or stamp[3])), page)
        if has_imprint:
            numbers = [int(line.strip()) for line in header.splitlines() if re.fullmatch(r"\d{1,4}", line.strip())]
            if len(numbers) == 1:
                printed_pages.append((page, numbers[0]))
        article_no = re.search(r"文章编号[:：]\s*\d{4}-\d{4}\((20\d{2})\)(\d{2})-(\d{4})-(\d{2})", text)
        if article_no:
            start = int(article_no[3])
            length = int(article_no[4])
            put("pages", f"{start}-{start + length - 1}", page)
    # Do not infer article extent from a sparse set of sampled pages.
    if printed_pages and len(printed_pages) == len(pages):
        values = [value for _, value in printed_pages]
        physical = [p for p, _ in printed_pages]
        if (all(isinstance(p, int) for p in physical)
                and physical == list(range(1, len(pages) + 1))
                and values == list(range(values[0], values[0] + len(values)))):
            put("pages", str(values[0]) if len(values) == 1 else f"{values[0]}-{values[-1]}", [p for p, _ in printed_pages])
    if provenance:
        result["metadata_provenance"] = provenance
    return result


def read_local_metadata(record):
    path = Path(record.get("source_path") or "")
    if not path.is_file() or path.suffix.lower() != ".pdf":
        return {}
    try:
        import fitz
        with fitz.open(path) as document:
            # Article PDFs are short; cap work on books and collected volumes.
            indices = list(range(len(document))) if len(document) <= 30 else [0, 1, len(document) - 1]
            pages = []
            for i in indices:
                page = document[i]
                box = page.rect
                top = page.get_text(clip=fitz.Rect(box.x0, box.y0, box.x1, box.y0 + box.height * .12))
                bottom = page.get_text(clip=fitz.Rect(box.x0, box.y1 - box.height * .08, box.x1, box.y1))
                pages.append({'page': i + 1, 'text': page.get_text(), 'margin_text': top + '\n' + bottom})
        if not any(p["text"].strip() for p in pages):
            from gpt_researcher.document.text_recovery import read_recovered_text
            recovered = read_recovered_text(path)
            pages = (recovered or {}).get("pages", [])
        metadata = extract_page_metadata(pages, record.get("source_type", ""))
        return metadata
    except Exception as exc:
        return {"local_metadata_error": type(exc).__name__}


def missing_reference_fields(record):
    kind = str(record.get("source_type") or "")
    if re.search(r"专利|patent", kind, re.I):
        required = {"title": "题名", "applicant": "申请人/专利权人", "patent_number": "公布/公告号", "publication_date": "公布/公告日期"}
    elif re.search(r"学位|dissertation|thesis", kind, re.I) or record.get("degree"):
        required = {"title": "题名", "author": "作者", "degree_institution": "授予单位", "year": "年份"}
    elif re.search(r"论文|期刊|journal", kind, re.I):
        required = {"title": "题名", "author": "作者", "container": "刊名", "year": "发表年份"}
        missing = [label for field, label in required.items() if not record.get(field)]
        if record.get('publication_status') == 'online_first':
            if not (record.get('doi') or record.get('url') or record.get('lookup_url')):
                missing.append('DOI/在线地址')
            return missing
        if not (record.get("issue") or record.get("volume") or record.get("volume_issue_pages")):
            missing.append("卷期")
        if not (record.get("pages") or ":" in str(record.get("volume_issue_pages") or "")):
            missing.append("页码/文章号")
        return missing
    else:
        return []
    return [label for field, label in required.items() if not record.get(field)]
