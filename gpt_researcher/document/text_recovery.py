"""Local OCR / legacy Word text caches, keyed by original file content."""
import json
from pathlib import Path

from .local_rag import _atomic_text, _file_sha256, get_local_rag_dir

_ENGINE = None
CACHE_VERSION = "rapidocr-1.4-ch-en-200dpi-v3"


def order_ocr_lines(lines, width, height):
    """Keep sustained newspaper columns together instead of interleaving their rows."""
    if len(lines) < 20:
        return lines
    def bounds(line):
        points = line[0]
        return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)
    boxes = [(line, bounds(line)) for line in lines]
    body = [box for _, box in boxes if box[1] >= height * .16 and box[3] <= height * .95
            and width * .18 <= box[2] - box[0] <= width * .55]
    if len(body) < 20:
        return lines
    gaps = []
    for x in range(int(width * .18), int(width * .83)):
        if (sum(b[0] < x < b[2] for b in body) == 0
                and sum(b[2] <= x for b in body) >= 8
                and sum(b[0] >= x for b in body) >= 8):
            if gaps and x == gaps[-1][-1] + 1:
                gaps[-1].append(x)
            else:
                gaps.append([x])
    cuts = [sum(gap) / len(gap) for gap in gaps if len(gap) >= width * .006]
    if not 1 <= len(cuts) <= 2:
        return lines
    top, bottom = min(b[1] for b in body), height * .965
    header, footer = [], []
    columns = [[] for _ in range(len(cuts) + 1)]
    for line, b in boxes:
        if b[3] < top:
            header.append((b[1], b[0], line))
        elif b[1] > bottom:
            footer.append((b[1], b[0], line))
        else:
            columns[sum((b[0] + b[2]) / 2 > cut for cut in cuts)].append((b[1], b[0], line))
    return [item[2] for group in [header, *columns, footer] for item in sorted(group, key=lambda item: item[:2])]


def cache_path(path, digest=None):
    return get_local_rag_dir() / "recovered_text" / ((digest or _file_sha256(Path(path))) + ".json")


def read_recovered_text(path, digest=None):
    target = cache_path(path, digest)
    if not target.exists():
        return None
    data = json.loads(target.read_text(encoding="utf-8"))
    return data if data.get("complete") and (data.get("version") == CACHE_VERSION or data.get("method") == "word_com") else None


def recover_source(path, *, progress=None, stopped=lambda: False):
    global _ENGINE
    path = Path(path)
    target = cache_path(path)
    if target.exists():
        cached = read_recovered_text(path)
        if cached:
            return cached
    state = {"version": CACHE_VERSION, "source_path": str(path), "complete": False, "pages": []}
    partial = target.with_suffix(".partial.json")
    if path.suffix.lower() == ".doc":
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        word = document = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            word.AutomationSecurity = 3
            document = word.Documents.Open(str(path.resolve()), ReadOnly=True,
                                           AddToRecentFiles=False, ConfirmConversions=False)
            text = str(document.Content.Text).replace("\r", "\n").replace("\x07", "\t").strip()
            if not text:
                raise ValueError("Word document contains no readable text")
            state["method"] = "word_com"
            state["pages"] = [{"page": None, "text": text, "method": "word_com"}]
        finally:
            if document is not None:
                document.Close(False)
            if word is not None:
                word.Quit()
            pythoncom.CoUninitialize()
    elif path.suffix.lower() == ".pdf":
        import fitz
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR

        if _ENGINE is None:
            _ENGINE = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        if partial.exists():
            candidate = json.loads(partial.read_text(encoding="utf-8"))
            if candidate.get("version") == CACHE_VERSION:
                state = candidate
        state["method"] = "ocr"
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError("PDF requires a password")
            for number in range(len(state["pages"]), len(document)):
                if stopped():
                    raise InterruptedError("Text recovery paused")
                if progress:
                    progress(number, len(document))
                page = document[number]
                text = page.get_text("text", sort=False).strip()
                confidence = None
                method = "native"
                if not text:
                    pix = page.get_pixmap(dpi=200, colorspace=fitz.csRGB, alpha=False)
                    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                    results, _ = _ENGINE(pixels[:, :, ::-1].copy())
                    retained = [line for line in (results or []) if float(line[2]) >= 0.6]
                    retained = order_ocr_lines(retained, pix.width, pix.height)
                    text = "\n".join(line[1] for line in retained)
                    confidence = sum(float(line[2]) for line in retained) / len(retained) if retained else 0
                    method = "ocr"
                state["pages"].append({"page": number + 1, "text": text, "method": method,
                                       "confidence": confidence})
                _atomic_text(partial, json.dumps(state, ensure_ascii=False))
        state["empty_pages"] = [item["page"] for item in state["pages"] if not item["text"].strip()]
    else:
        raise ValueError("Text recovery supports PDF and legacy DOC only")
    if cache_path(path) != target:
        raise ValueError("Source changed during text recovery")
    state["complete"] = True
    _atomic_text(target, json.dumps(state, ensure_ascii=False))
    partial.unlink(missing_ok=True)
    return state
