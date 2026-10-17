"""Report layout templates are not uploaded as research evidence."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .document_export import render_word
from .template_store import (
    MAX_TEMPLATE_BYTES, ReportTemplateError, import_template, list_templates,
    resolve_template_profile, standard_template_bytes,
)

router = APIRouter(prefix="/api/report-templates", tags=["report-templates"])
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PREVIEW_REPORT = """# 报告模板试排

## 摘要
本文档用于检查所选模板的封面、字体、字号、段落和页眉页脚。试排内容不构成研究结论。

关键词：航空发动机；报告排版

## 目录
- 1 研究背景
- 2 技术分析

## 1 研究背景
本段用于展示正文排版效果。实际报告将依据当前任务与检索材料撰写，模板中的示例正文不会作为研究证据。

## 2 技术分析
### 2.1 技术路线
本段用于检查二级标题与正文之间的间距，以及段落首行缩进。

#### 2.1.1 关键参数
本段用于检查三级标题的字体与字号。

表1 试排示例

| 项目 | 内容 |
| --- | --- |
| 文档 | 模板排版检查 |
| 正文 | 根据任务生成 |

## 参考文献
[1] 作者. 文献题名[J]. 刊名, 年份, 卷(期): 页码.
"""


@router.get("")
async def catalog():
    return {"templates": await asyncio.to_thread(list_templates)}


@router.get("/standard")
async def standard():
    return Response(await asyncio.to_thread(standard_template_bytes), media_type=DOCX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="report-template.docx"'})


@router.post("")
async def upload(file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_TEMPLATE_BYTES + 1)
        info = await asyncio.to_thread(import_template, file.filename or "", data)
        return {"template": info}
    except ReportTemplateError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    finally:
        await file.close()


@router.post("/preview")
async def preview(payload: dict, background_tasks: BackgroundTasks):
    temporary = TemporaryDirectory(prefix="report-template-preview-")
    try:
        profile = resolve_template_profile(payload.get("report_format_profile"))
        if not profile.get("template"):
            raise ReportTemplateError("请先选择或导入一个报告模板。")
        path = Path(temporary.name) / "template-preview.docx"
        await asyncio.to_thread(render_word, PREVIEW_REPORT, path, None, profile)
        background_tasks.add_task(temporary.cleanup)
        return FileResponse(path, filename="报告模板试排.docx", media_type=DOCX_TYPE,
                            background=background_tasks)
    except ReportTemplateError as exc:
        temporary.cleanup()
        raise HTTPException(400, detail=str(exc)) from exc
    except Exception as exc:
        temporary.cleanup()
        raise HTTPException(500, detail="模板试排失败，请检查模板文档是否完整。") from exc
