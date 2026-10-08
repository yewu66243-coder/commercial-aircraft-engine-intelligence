"""Local OpenAI-compatible model provider registry.

The registry is intentionally stored under local_docs so customer API keys stay
machine-local and outside Git. Public catalog helpers never return secrets.
"""
from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "local_docs" / "model_providers.local.json"
_MAX_MODELS = 30


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _slug(value: str, fallback: str = "custom_model") -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", value or "").strip("_").lower()
    text = re.sub(r"_+", "_", text)[:48]
    if not text and value:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        return f"{fallback}_{digest}"
    return text or fallback


def _provider_id(value: str) -> str:
    candidate = _slug(value)
    return candidate if candidate.startswith("custom_") else f"custom_{candidate}"


def _clean_text(value: Any, fallback: str = "", limit: int = 80) -> str:
    if not isinstance(value, str):
        return fallback
    text = re.sub(r"[\r\n\t<>]", " ", value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit] or fallback


def _clean_base_url(value: Any) -> str:
    url = _clean_text(value, limit=240).rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API Base URL 必须是 http 或 https 开头的完整地址。")
    if parsed.username or parsed.password:
        raise ValueError("API Base URL 不能包含用户名或密码，请把密钥填入 API Key。")
    return url


def _clean_models(value: Any) -> List[str]:
    if isinstance(value, str):
        candidates = re.split(r"[,，;；\n]+", value)
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    models: List[str] = []
    for item in candidates:
        model = _clean_text(item, limit=96)
        if not model or any(char.isspace() for char in model):
            continue
        if model not in models:
            models.append(model)
        if len(models) >= _MAX_MODELS:
            break
    if not models:
        raise ValueError("请至少填写一个模型 ID。")
    return models


def _load_raw() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"version": 1, "providers": []}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "providers": []}
    if not isinstance(data, dict):
        return {"version": 1, "providers": []}
    providers = data.get("providers")
    if not isinstance(providers, list):
        data["providers"] = []
    return data


def _save_raw(data: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_provider(provider: Dict[str, Any], keep_secret: bool = True) -> Dict[str, Any] | None:
    try:
        provider_id = _provider_id(str(provider.get("id") or provider.get("name") or "custom_model"))
        name = _clean_text(provider.get("name"), "自定义模型服务", 60)
        base_url = _clean_base_url(provider.get("base_url") or provider.get("api_base"))
        models = _clean_models(provider.get("models") or provider.get("model"))
    except ValueError:
        return None
    api_key = _clean_text(provider.get("api_key"), "", 300)
    normalized = {
        "id": provider_id,
        "name": name,
        "base_url": base_url,
        "models": models,
        "default_model": _clean_text(provider.get("default_model"), models[0], 96),
        "enabled": bool(provider.get("enabled", True)),
        "created_at": _clean_text(provider.get("created_at"), _now(), 32),
        "updated_at": _clean_text(provider.get("updated_at"), _now(), 32),
    }
    if normalized["default_model"] not in models:
        normalized["default_model"] = models[0]
    if keep_secret:
        normalized["api_key"] = api_key
    return normalized


def load_custom_model_providers() -> List[Dict[str, Any]]:
    providers = []
    for item in _load_raw().get("providers", []):
        if isinstance(item, dict):
            normalized = _normalize_provider(item, keep_secret=True)
            if normalized:
                providers.append(normalized)
    return providers


def public_custom_model_providers() -> List[Dict[str, Any]]:
    public = []
    for provider in load_custom_model_providers():
        parsed = urlparse(provider["base_url"])
        api_key = provider.get("api_key") or ""
        public.append({
            "id": provider["id"],
            "name": provider["name"],
            "model": provider["default_model"],
            "fast_model": provider["default_model"],
            "strategic_model": provider["default_model"],
            "configured": bool(api_key and provider["base_url"] and provider["models"] and provider.get("enabled", True)),
            "endpoint_host": parsed.hostname or "",
            "custom": True,
            "models": list(provider["models"]),
            "api_key_preview": f"***{api_key[-4:]}" if api_key else "",
            "updated_at": provider.get("updated_at", ""),
        })
    return public


def custom_generation_models() -> List[Dict[str, Any]]:
    entries = []
    for provider in public_custom_model_providers():
        for model in provider.get("models", []):
            entries.append({
                "id": f"{provider['id']}:{model}",
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "name": f"{provider['name']} {model}",
                "model": model,
                "report_details": ["detailed"],
                "configured": provider["configured"],
                "custom": True,
            })
    return entries


def find_custom_model_provider(provider_id: str) -> Dict[str, Any] | None:
    normalized_id = _provider_id(provider_id)
    return next((item for item in load_custom_model_providers() if item["id"] == normalized_id), None)


def save_custom_model_provider(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("模型配置必须是对象。")
    existing_id = _provider_id(str(payload.get("id") or payload.get("name") or "custom_model"))
    existing = find_custom_model_provider(existing_id) or {}
    merged = deepcopy(existing)
    merged.update(payload)
    if not _clean_text(merged.get("api_key"), "", 300) and existing.get("api_key"):
        merged["api_key"] = existing["api_key"]
    normalized = _normalize_provider(merged, keep_secret=True)
    if not normalized:
        raise ValueError("模型配置不完整，请检查名称、API Base URL、API Key 和模型 ID。")
    if not normalized.get("api_key"):
        raise ValueError("请填写 API Key。")
    normalized["updated_at"] = _now()
    if not existing:
        normalized["created_at"] = _now()

    data = _load_raw()
    providers = [item for item in load_custom_model_providers() if item["id"] != normalized["id"]]
    providers.append(normalized)
    data["providers"] = providers
    _save_raw(data)
    return {"provider": next(item for item in public_custom_model_providers() if item["id"] == normalized["id"])}


def delete_custom_model_provider(provider_id: str) -> Dict[str, Any]:
    normalized_id = _provider_id(provider_id)
    providers = load_custom_model_providers()
    kept = [item for item in providers if item["id"] != normalized_id]
    if len(kept) == len(providers):
        raise ValueError("未找到该自定义模型配置。")
    data = _load_raw()
    data["providers"] = kept
    _save_raw(data)
    return {"deleted": normalized_id}


def build_provider_payload_for_test(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("模型配置必须是对象。")
    provider_id = payload.get("id")
    existing = find_custom_model_provider(str(provider_id)) if provider_id else None
    merged = deepcopy(existing or {})
    merged.update(payload)
    if not _clean_text(merged.get("api_key"), "", 300) and existing and existing.get("api_key"):
        merged["api_key"] = existing["api_key"]
    normalized = _normalize_provider(merged, keep_secret=True)
    if not normalized:
        raise ValueError("模型配置不完整，请检查名称、API Base URL、API Key 和模型 ID。")
    if not normalized.get("api_key"):
        raise ValueError("请填写 API Key。")
    return normalized
