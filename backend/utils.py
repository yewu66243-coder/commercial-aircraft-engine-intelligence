"""Asynchronous file writers used by the report download endpoints."""
import asyncio
from pathlib import Path
import re
import urllib.parse

import aiofiles

if __package__:
    from .reporting.document_export import clean_text, render_pdf, render_word, resolve_image_path
else:
    # backend.server also imports this module as top-level "utils".
    from reporting.document_export import clean_text, render_pdf, render_word, resolve_image_path


IMAGE_PATTERN = r'!\[([^\]]*)\]\((/outputs/[^)]+|file:///[^)]+)\)'


def _strip_xml_invalid_chars(text: str) -> str:
    return clean_text(text)


def _resolve_report_image_path(url: str) -> Path | None:
    return resolve_image_path(url)


def _preprocess_images_for_pdf(text: str) -> str:
    def replace(match):
        path = resolve_image_path(match.group(2))
        return f"![{match.group(1)}]({path.as_uri()})" if path else match.group(0)
    return re.sub(IMAGE_PATTERN, replace, text)


async def write_to_file(filename: str, text: str) -> None:
    """Write UTF-8 text, creating the destination directory when needed."""
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    text_utf8 = str(text).encode("utf-8", errors="replace").decode("utf-8")
    async with aiofiles.open(filename, "w", encoding="utf-8") as file:
        await file.write(text_utf8)


async def write_text_to_md(text: str, filename: str = "") -> str:
    file_path = f"outputs/{filename[:60]}.md"
    await write_to_file(file_path, text)
    return urllib.parse.quote(file_path)


async def write_md_to_pdf(text: str, filename: str = "") -> str:
    """Return the URL-encoded PDF path, or an empty string on export failure."""
    file_path = f"outputs/{filename[:60]}.pdf"
    try:
        await asyncio.to_thread(render_pdf, text, file_path, Path.cwd())
        print(f"Report written to {file_path}")
        return urllib.parse.quote(file_path)
    except Exception as exc:
        print(f"Error in converting Markdown to PDF: {exc}")
        return ""


async def write_md_to_word(text: str, filename: str = "") -> str:
    """Return the URL-encoded DOCX path, or an empty string on export failure."""
    file_path = f"outputs/{filename[:60]}.docx"
    try:
        await asyncio.to_thread(render_word, text, file_path, Path.cwd())
        print(f"Report written to {file_path}")
        return urllib.parse.quote(file_path)
    except Exception as exc:
        print(f"Error in converting Markdown to DOCX: {exc}")
        return ""
