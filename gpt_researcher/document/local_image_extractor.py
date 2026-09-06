from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable


FIGURE_RE = re.compile(
    r"(?i)(\bfig(?:ure)?\.?\s*\d+|图\s*\d+|图[一二三四五六七八九十]+)"
)
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./+]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


@dataclass
class LocalReportImage:
    source_file: str
    source_path: str
    page: int
    image_path: str
    markdown_path: str
    caption: str
    nearby_text: str
    kind: str
    score: float
    caption_matched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_slug(value: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return (slug or "paper")[:max_len]


def _query_tokens(query: str) -> list[str]:
    tokens: set[str] = set()
    latin: set[str] = set()
    for word in WORD_RE.findall(query.lower()):
        word = word.strip("._-/+")
        if len(word) >= 3:
            latin.add(word)
    for block in CJK_RE.findall(query):
        if len(block) <= 12:
            tokens.add(block)
        for size in (2, 3, 4):
            if len(block) >= size:
                for i in range(len(block) - size + 1):
                    tokens.add(block[i : i + size])
    identifiers = sorted(latin, key=lambda item: (-len(item), item))[:40]
    return identifiers + sorted(tokens, key=lambda item: (-len(item), item))[:40-len(identifiers)]


def _score_text(text: str, tokens: Iterable[str]) -> float:
    normalized = text.lower()
    score = 0.0
    for token in tokens:
        count = normalized.count(token.lower())
        if count:
            score += min(count, 4) * (2.0 if len(token) >= 4 else 1.0)
    if FIGURE_RE.search(text):
        score += 4.0
    return score


def _compact_text(text: str, max_len: int = 700) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:max_len]


def _find_caption(page_text: str) -> str:
    lines = [line.strip() for line in (page_text or "").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not FIGURE_RE.search(line):
            continue
        caption = line
        if len(caption) < 60 and index + 1 < len(lines):
            caption = f"{caption} {lines[index + 1]}"
        return _compact_text(caption, 220)
    return ""


def _find_caption_with_rect(page: Any) -> tuple[str, tuple[float, float, float, float] | None]:
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        blocks = []

    for block in blocks:
        if len(block) < 5:
            continue
        text = _compact_text(str(block[4]), 220)
        if not FIGURE_RE.search(text):
            continue
        return text, (float(block[0]), float(block[1]), float(block[2]), float(block[3]))

    page_text = page.get_text("text") or ""
    return _find_caption(page_text), None


def _candidate_id(pdf_path: Path, page_number: int, suffix: str) -> str:
    raw = f"{pdf_path.resolve()}:{page_number}:{suffix}".encode("utf-8", errors="ignore")
    return hashlib.md5(raw).hexdigest()[:10]


def _find_image_caption(page: Any, image_rect: Any) -> str:
    """Match a short caption immediately below this image, not elsewhere on its page."""
    choices = []
    for block in page.get_text("blocks") or []:
        if len(block) < 7 or block[6] != 0:
            continue
        text = _compact_text(str(block[4]), 300)
        x0, y0, x1, y1 = map(float, block[:4])
        gap = y0 - image_rect.y1
        if not text or len(text) > 160 or not -2 <= gap <= 24 or y1 - y0 > 48:
            continue
        overlap = max(0, min(x1, image_rect.x1) - max(x0, image_rect.x0))
        if overlap < 0.8 * max(x1 - x0, 1):
            continue
        # A neighboring column or running header must not become the figure caption.
        if x0 < image_rect.x0 - 12 or x1 > image_rect.x1 + 18:
            continue
        choices.append((abs(gap), text))
    return min(choices, default=(0, ""))[1]


def _image_markdown_path(path: Path) -> str:
    outputs = Path("outputs").resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(outputs)
        return "/outputs/" + relative.as_posix()
    except ValueError:
        return resolved.as_posix()


def _append_candidate(
    candidates: list[LocalReportImage],
    *,
    pdf_path: Path,
    page_number: int,
    image_path: Path,
    caption: str,
    nearby_text: str,
    kind: str,
    score: float,
    caption_matched: bool = False,
) -> None:
    candidates.append(
        LocalReportImage(
            source_file=pdf_path.name,
            source_path=str(pdf_path),
            page=page_number,
            image_path=str(image_path),
            markdown_path=_image_markdown_path(image_path),
            caption=caption or f"{pdf_path.name} 第 {page_number} 页图像证据",
            nearby_text=_compact_text(nearby_text),
            kind=kind,
            score=round(score, 3),
            caption_matched=caption_matched,
        )
    )


def extract_local_report_images(
    papers: Iterable[Any],
    query: str,
    *,
    output_root: str | os.PathLike[str] = "outputs/report_images",
    max_total: int = 6,
    max_per_paper: int = 3,
    max_pages_per_pdf: int = 50,
    min_width: int = 220,
    min_height: int = 140,
) -> list[dict[str, Any]]:
    """Extract real image evidence from selected local PDFs for report use.

    The function only uses local source PDFs selected for close reading. It
    first extracts embedded raster images, then falls back to page screenshots
    for pages with figure captions or strong query matches.
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return []

    output_dir = Path(output_root).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tokens = _query_tokens(query)
    all_candidates: list[LocalReportImage] = []
    def rank(candidate):
        caption_score = _score_text(candidate.caption, tokens) if candidate.caption_matched else 0
        # A genuine topic-matched caption outranks a neighboring page whose
        # prose happens to discuss the queried engine.
        return (bool(candidate.caption_matched and caption_score), candidate.caption_matched,
                caption_score, candidate.score)

    for paper_index, paper in enumerate(papers, start=1):
        source_path = Path(getattr(paper, "source_path", "") or "")
        if source_path.suffix.lower() != ".pdf" or not source_path.exists():
            continue

        pdf_slug = _safe_slug(source_path.stem)
        paper_candidates: list[LocalReportImage] = []
        candidate_pool_limit = max(max_per_paper * 4, max_per_paper)
        seen_xrefs: set[int] = set()

        try:
            with fitz.open(str(source_path)) as doc:
                page_count = min(len(doc), max_pages_per_pdf)
                page_infos: list[tuple[float, int, str, str, tuple[float, float, float, float] | None]] = []

                for page_index in range(page_count):
                    page = doc.load_page(page_index)
                    text = page.get_text("text") or ""
                    caption, caption_rect = _find_caption_with_rect(page)
                    page_score = _score_text(text, tokens)
                    if caption:
                        page_score += 6.0
                    page_number = page_index + 1
                    page_infos.append((page_score, page_number, text, caption, caption_rect))

                    for image_index, image in enumerate(page.get_images(full=True), start=1):
                        if len(paper_candidates) >= candidate_pool_limit:
                            break
                        xref = int(image[0])
                        if xref in seen_xrefs:
                            continue
                        seen_xrefs.add(xref)

                        width = int(image[2] or 0)
                        height = int(image[3] or 0)
                        if width < min_width or height < min_height:
                            continue
                        ratio = width / max(height, 1)
                        if ratio > 8 or ratio < 0.125:
                            continue

                        rects = page.get_image_rects(xref)
                        image_caption = next((value for rect in rects
                                              if (value := _find_image_caption(page, rect))), "")
                        if page_number == 1 and not image_caption:
                            continue

                        try:
                            extracted = doc.extract_image(xref)
                            image_bytes = extracted.get("image", b"")
                            if len(image_bytes) < 8_000:
                                continue
                            ext = (extracted.get("ext") or "png").lower()
                            if ext not in {"png", "jpg", "jpeg", "webp"}:
                                # PDF JPEG2000/JBIG2 data is not browser PNG data. Re-encode it.
                                pix = fitz.Pixmap(doc, xref)
                                if pix.colorspace and pix.colorspace.n > 3:
                                    pix = fitz.Pixmap(fitz.csRGB, pix)
                                image_bytes = pix.tobytes("png")
                                ext = "png"
                            image_name = (
                                f"{paper_index:02d}_{pdf_slug}_p{page_number:03d}_"
                                f"img{image_index}_{_candidate_id(source_path, page_number, str(xref))}.{ext}"
                            )
                            image_path = output_dir / image_name
                            image_path.write_bytes(image_bytes)
                        except Exception:
                            continue

                        score = (0.15 * page_score + 4 * _score_text(image_caption, tokens)
                                 + min((width * height) / 250_000, 4.0))
                        _append_candidate(
                            paper_candidates,
                            pdf_path=source_path,
                            page_number=page_number,
                            image_path=image_path,
                            caption=image_caption,
                            nearby_text=text,
                            kind="embedded_image",
                            score=score,
                            caption_matched=bool(image_caption),
                        )

                if len(paper_candidates) < candidate_pool_limit:
                    ranked_pages = sorted(page_infos, key=lambda item: item[0], reverse=True)
                    for page_score, page_number, text, caption, caption_rect in ranked_pages:
                        if len(paper_candidates) >= candidate_pool_limit:
                            break
                        if page_number == 1 or not caption:
                            continue

                        try:
                            page = doc.load_page(page_number - 1)
                            clip = page.rect
                            if caption_rect:
                                cap = fitz.Rect(*caption_rect)
                                page_rect = page.rect
                                if cap.y0 > page_rect.height * 0.35:
                                    y0 = max(page_rect.y0, cap.y0 - page_rect.height * 0.52)
                                    y1 = min(page_rect.y1, cap.y1 + 45)
                                else:
                                    y0 = max(page_rect.y0, cap.y0 - 35)
                                    y1 = min(page_rect.y1, cap.y1 + page_rect.height * 0.52)
                                clip = fitz.Rect(page_rect.x0 + 24, y0, page_rect.x1 - 24, y1)
                            pix = page.get_pixmap(
                                matrix=fitz.Matrix(1.8, 1.8),
                                alpha=False,
                                clip=clip,
                            )
                            image_name = (
                                f"{paper_index:02d}_{pdf_slug}_p{page_number:03d}_"
                                f"figure_{_candidate_id(source_path, page_number, 'figure')}.png"
                            )
                            image_path = output_dir / image_name
                            pix.save(str(image_path))
                        except Exception:
                            continue

                        _append_candidate(
                            paper_candidates,
                            pdf_path=source_path,
                            page_number=page_number,
                            image_path=image_path,
                            caption=caption,
                            nearby_text=text,
                            kind="figure_snapshot",
                            score=page_score + 1.0,
                        )
        except Exception:
            continue

        all_candidates.extend(
            sorted(paper_candidates, key=rank, reverse=True)[:max_per_paper]
        )

    ranked = sorted(all_candidates, key=rank, reverse=True)[:max_total]
    return [candidate.to_dict() for candidate in ranked]
