from dotenv import load_dotenv
import logging
import mimetypes
import sys
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Create logs directory if it doesn't exist
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        # File handler for general application logs
        logging.FileHandler('logs/app.log'),
        # Stream handler for console output
        logging.StreamHandler()
    ]
)

# Suppress verbose fontTools logging
logging.getLogger('fontTools').setLevel(logging.WARNING)
logging.getLogger('fontTools.subset').setLevel(logging.WARNING)
logging.getLogger('fontTools.ttLib').setLevel(logging.WARNING)

# Create logger instance
logger = logging.getLogger(__name__)

load_dotenv()

from backend.server.app import app

# ================= 新增：引入师兄的 3-Agent 服务 =================
from three_agent_service import (
    ModelProviderConfigurationError,
    ThreeAgentRequestData,
    ThreeAgentService,
    get_model_provider_catalog,
    get_report_progress,
    update_report_progress,
)
from gpt_researcher.document.local_library import (
    delete_local_library_file,
    list_local_library,
    rebuild_patents_index_from_pool,
    resolve_local_library_file,
    save_local_library_file,
)
from gpt_researcher.intelligence_templates import (
    delete_intelligence_template,
    get_template_catalog,
    save_intelligence_template,
)


def explain_report_exception(exc: Exception) -> dict[str, Any]:
    """Translate backend failures into user-facing diagnostics."""
    technical_detail = f"{type(exc).__name__}: {exc}"
    text = technical_detail.lower()

    if isinstance(exc, ModelProviderConfigurationError) or any(
        marker in text
        for marker in ("api key", "apikey", "dashscope_api_key", "qwen_api_key", "deepseek_api_key", "unauthorized")
    ):
        return {
            "code": "MODEL_CONFIG_ERROR",
            "title": "模型配置异常",
            "message": "当前选择的大模型不可用，通常是 API Key、模型名称或接口地址未配置正确。",
            "suggestion": "请检查 .env 中对应模型的 API Key、BASE_URL 和模型名，保存后重启工作台再试。",
            "technical_detail": technical_detail,
        }

    if any(marker in text for marker in ("rate limit", "too many requests", "429", "quota", "insufficient_quota")):
        return {
            "code": "MODEL_RATE_LIMIT",
            "title": "模型调用受限",
            "message": "模型服务返回限流或额度不足，报告生成被中断。",
            "suggestion": "请稍后重试，或更换模型、降低精读/抓取上限，必要时检查模型账号额度。",
            "technical_detail": technical_detail,
        }

    if any(marker in text for marker in ("timeout", "timed out", "read timed", "connect timeout")):
        return {
            "code": "TIMEOUT",
            "title": "请求超时",
            "message": "后台在检索、精读资料或调用模型时等待过久，任务没有在限定时间内完成。",
            "suggestion": "建议减少检索范围或精读/抓取上限，也可以稍后在网络稳定时重新提交。",
            "technical_detail": technical_detail,
        }

    if any(marker in text for marker in ("connection", "network", "dns", "name resolution", "max retries", "ssl", "certificate")):
        return {
            "code": "NETWORK_ERROR",
            "title": "网络或证书异常",
            "message": "后台访问模型服务、Web 来源或在线文献时出现网络连接问题。",
            "suggestion": "请确认网络、代理和证书环境正常；如果只需要本地资料，可先取消 Web 检索后重试。",
            "technical_detail": technical_detail,
        }

    if any(marker in text for marker in ("permission", "access is denied", "permission denied", "winerror 5")):
        return {
            "code": "FILE_PERMISSION_ERROR",
            "title": "文件权限异常",
            "message": "后台写入报告、读取资料或修改文件时没有足够权限。",
            "suggestion": "请关闭正在占用的 Word/PDF 文件，确认 outputs 与 local_docs 目录可写，然后重新生成。",
            "technical_detail": technical_detail,
        }

    if any(marker in text for marker in ("no such file", "filenotfound", "not found", "cannot find")):
        return {
            "code": "FILE_NOT_FOUND",
            "title": "文件或路径不存在",
            "message": "后台需要读取的资料、模板或导出路径不存在。",
            "suggestion": "请检查本地资料库文件是否被移动或删除，必要时重新上传资料或重建索引。",
            "technical_detail": technical_detail,
        }

    if any(marker in text for marker in ("pdf", "docx", "word", "export", "convert", "pandoc", "libreoffice")):
        return {
            "code": "EXPORT_ERROR",
            "title": "报告导出异常",
            "message": "正文可能已经生成，但 Word/PDF/Markdown 导出阶段出现问题。",
            "suggestion": "请检查导出依赖和目标文件是否被占用；也可以先下载已成功生成的其他格式。",
            "technical_detail": technical_detail,
        }

    if any(marker in text for marker in ("json", "decode", "validation", "pydantic", "valueerror")):
        return {
            "code": "DATA_VALIDATION_ERROR",
            "title": "数据格式异常",
            "message": "任务参数、模型输出或中间研究记录格式不符合系统预期。",
            "suggestion": "请简化任务描述后重试；如果持续出现，请保留运行日志便于定位是哪一步输出格式异常。",
            "technical_detail": technical_detail,
        }

    return {
        "code": "INTERNAL_ERROR",
        "title": "后台处理异常",
        "message": "后台服务在执行 3-Agent 报告流程时遇到未分类异常。",
        "suggestion": "请先查看运行日志中最后一个阶段；若重复出现，请把报错详情和任务描述一起用于排查。",
        "technical_detail": technical_detail,
    }


# 注册师兄的 POST 接口
@app.post("/api/three-agent-report")
async def generate_three_agent_report(request_data: ThreeAgentRequestData):
    logger.info(f"正在启动 3-Agent 流程，课题: {request_data.task}")
    service = None
    try:
        service = ThreeAgentService(request_data)
        return await service.run()
    except ModelProviderConfigurationError as exc:
        diagnosis = explain_report_exception(exc)
        if service is not None:
            update_report_progress(
                service.task_id, "System", f"报告生成失败：{diagnosis['message']}", status="failed")
        raise HTTPException(status_code=400, detail=diagnosis) from exc
    except Exception as exc:
        logger.exception("3-Agent 报告生成失败")
        diagnosis = explain_report_exception(exc)
        if service is not None:
            update_report_progress(
                service.task_id, "System", f"报告生成失败：{diagnosis['message']}", status="failed")
        raise HTTPException(status_code=500, detail=diagnosis) from exc


@app.get("/api/model-providers")
async def model_providers():
    return get_model_provider_catalog()


@app.get("/api/report-progress/{task_id}")
async def report_progress(task_id: str):
    progress = get_report_progress(task_id, include_running_duration=True)
    if progress is None:
        raise HTTPException(status_code=404, detail="未找到该报告任务的进度记录")
    return progress


@app.get("/api/local-library")
async def get_local_library(
    q: str = Query("", description="按文件名、题名、摘要搜索本地资料"),
    limit: int = Query(5000, ge=1, le=10000),
):
    return list_local_library(search=q, limit=limit)


@app.get("/api/intelligence-templates")
async def get_intelligence_templates():
    return get_template_catalog()


@app.post("/api/intelligence-templates/{template_type}")
async def save_intelligence_templates(template_type: str, payload: dict[str, Any]):
    try:
        result = save_intelligence_template(template_type, payload)
        return {"success": True, **result, "catalog": get_template_catalog()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("保存情报模板失败")
        raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc


@app.delete("/api/intelligence-templates/{template_type}/{template_id}")
async def delete_intelligence_templates(template_type: str, template_id: str):
    try:
        result = delete_intelligence_template(template_type, template_id)
        return {"success": True, **result, "catalog": get_template_catalog()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("删除情报模板失败")
        raise HTTPException(status_code=500, detail=f"删除失败：{exc}") from exc


@app.post("/api/local-library/upload")
async def upload_local_library_file(
    target: str = Form("user_docs"),
    file: UploadFile = File(...),
):
    try:
        result = save_local_library_file(file.file, file.filename or "uploaded_document", target)
        return {"success": True, **result, "library": list_local_library()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("上传本地资料失败")
        raise HTTPException(status_code=500, detail=f"上传失败：{exc}") from exc


@app.post("/api/local-library/patents/rebuild-index")
async def rebuild_local_patents_index():
    try:
        result = rebuild_patents_index_from_pool()
        return {"success": True, **result, "library": list_local_library()}
    except Exception as exc:
        logger.exception("重建专利索引失败")
        raise HTTPException(status_code=500, detail=f"重建专利索引失败：{exc}") from exc


@app.get("/api/local-library/{target}/{file_name}/open")
async def open_local_library_file(target: str, file_name: str):
    try:
        path = resolve_local_library_file(target, file_name)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(
            path,
            filename=path.name,
            media_type=media_type,
            content_disposition_type="inline",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("打开本地资料失败")
        raise HTTPException(status_code=500, detail=f"打开失败：{exc}") from exc


@app.delete("/api/local-library/{target}/{file_name}")
async def delete_local_library(target: str, file_name: str):
    try:
        result = delete_local_library_file(target, file_name)
        return {"success": True, **result, "library": list_local_library()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("删除本地资料失败")
        raise HTTPException(status_code=500, detail=f"删除失败：{exc}") from exc
# =================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("Starting server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
