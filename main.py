from dotenv import load_dotenv
import logging
import mimetypes
from pathlib import Path
from fastapi import File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

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
from gpt_researcher.intelligence_templates import get_template_catalog

# 注册师兄的 POST 接口
@app.post("/api/three-agent-report")
async def generate_three_agent_report(request_data: ThreeAgentRequestData):
    logger.info(f"正在启动 3-Agent 流程，课题: {request_data.task}")
    service = None
    try:
        service = ThreeAgentService(request_data)
        return await service.run()
    except ModelProviderConfigurationError as exc:
        if service is not None:
            update_report_progress(
                service.task_id, "System", f"报告生成失败：{exc}", status="failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if service is not None:
            update_report_progress(
                service.task_id, "System", f"报告生成失败：{type(exc).__name__}。", status="failed")
        raise


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
