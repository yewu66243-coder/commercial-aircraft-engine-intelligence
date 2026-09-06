"""Request-scoped DeepSeek/Qwen model selection."""
import asyncio
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.getcwd())


class ModelProviderSelectionTests(unittest.TestCase):
    def test_catalog_exposes_both_models_without_secrets(self):
        from three_agent_service import get_model_provider_catalog

        catalog = get_model_provider_catalog({
            "OPENAI_API_KEY": "deep-secret",
            "SMART_LLM": "openai:deepseek-chat",
            "DASHSCOPE_API_KEY": "qwen-secret",
            "QWEN_MODEL": "qwen-plus",
        })

        self.assertEqual(catalog["default"], "deepseek")
        self.assertEqual([item["id"] for item in catalog["providers"]], ["deepseek", "qwen"])
        self.assertTrue(all(item["configured"] for item in catalog["providers"]))
        self.assertNotIn("deep-secret", repr(catalog))
        self.assertNotIn("qwen-secret", repr(catalog))

    def test_catalog_strips_credentials_embedded_in_endpoint_url(self):
        from three_agent_service import get_model_provider_catalog

        catalog = get_model_provider_catalog({
            "OPENAI_API_KEY": "deep-secret",
            "DASHSCOPE_API_KEY": "qwen-secret",
            "QWEN_BASE_URL": "https://url-user:url-password@qwen.example:8443/v1",
        })

        qwen = next(item for item in catalog["providers"] if item["id"] == "qwen")
        self.assertEqual(qwen["endpoint_host"], "qwen.example")
        self.assertNotIn("url-user", repr(catalog))
        self.assertNotIn("url-password", repr(catalog))

    def test_qwen_runtime_uses_official_compatible_defaults(self):
        from three_agent_service import resolve_model_runtime

        runtime = resolve_model_runtime("qwen", {"DASHSCOPE_API_KEY": "qwen-secret"})

        self.assertEqual(runtime.provider_id, "qwen")
        self.assertEqual(runtime.provider_name, "千问")
        self.assertEqual(runtime.smart_model, "qwen-plus")
        self.assertEqual(runtime.fast_model, "qwen-plus")
        self.assertEqual(runtime.strategic_model, "qwen-plus")
        self.assertEqual(runtime.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_missing_qwen_key_and_unknown_provider_are_rejected(self):
        from three_agent_service import (
            ModelProviderConfigurationError,
            ThreeAgentRequestData,
            ThreeAgentService,
            resolve_model_runtime,
        )

        with self.assertRaisesRegex(ValueError, "不支持"):
            resolve_model_runtime("invented", {})
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ModelProviderConfigurationError, "DASHSCOPE_API_KEY"):
                ThreeAgentService(ThreeAgentRequestData(task="GTF", llm_provider="qwen"))

    def test_selected_runtime_is_applied_to_every_researcher_model(self):
        from three_agent_service import ThreeAgentRequestData, ThreeAgentService

        researcher = SimpleNamespace(
            cfg=SimpleNamespace(llm_kwargs={"verbose": False}),
            conduct_research=AsyncMock(),
            write_report=AsyncMock(return_value="研究稿"),
            get_research_context=lambda: ["原文"],
            get_research_sources=lambda: [],
        )
        environment = {
            "DASHSCOPE_API_KEY": "qwen-secret",
            "QWEN_BASE_URL": "https://example.test/compatible/v1",
            "QWEN_MODEL": "qwen-plus",
            "QWEN_FAST_MODEL": "qwen-turbo",
            "QWEN_STRATEGIC_MODEL": "qwen-max",
        }
        with patch.dict("os.environ", environment, clear=True), patch(
            "three_agent_service.GPTResearcher", return_value=researcher
        ):
            service = ThreeAgentService(ThreeAgentRequestData(task="GTF", llm_provider="qwen"))
            asyncio.run(service._research_one("维修可靠性", 1))

        self.assertEqual(researcher.cfg.fast_llm_provider, "openai")
        self.assertEqual(researcher.cfg.fast_llm_model, "qwen-turbo")
        self.assertEqual(researcher.cfg.smart_llm_model, "qwen-plus")
        self.assertEqual(researcher.cfg.strategic_llm_model, "qwen-max")
        self.assertEqual(researcher.cfg.llm_kwargs["openai_api_key"], "qwen-secret")
        self.assertEqual(
            researcher.cfg.llm_kwargs["openai_api_base"],
            "https://example.test/compatible/v1",
        )

    def test_qwen_writer_client_uses_selected_key_endpoint_and_model(self):
        from three_agent_service import ThreeAgentRequestData, ThreeAgentService

        completion = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="# 千问报告\n\n## 摘要\n测试内容。"),
            finish_reason="stop",
        )])
        client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=completion))))
        environment = {
            "DASHSCOPE_API_KEY": "qwen-secret",
            "QWEN_BASE_URL": "https://qwen.example/v1",
            "QWEN_MODEL": "qwen-plus",
        }
        with patch.dict("os.environ", environment, clear=True), patch(
            "three_agent_service.AsyncOpenAI", return_value=client
        ) as client_factory:
            service = ThreeAgentService(ThreeAgentRequestData(task="GTF", llm_provider="qwen"))
            asyncio.run(service.writer_agent([
                {"title": "技术", "subtopic": "GTF", "draft": "原始研究材料", "context": []}
            ]))

        self.assertEqual(client_factory.call_args.kwargs["api_key"], "qwen-secret")
        self.assertEqual(client_factory.call_args.kwargs["base_url"], "https://qwen.example/v1")
        self.assertEqual(client.chat.completions.create.call_args_list[0].kwargs["model"], "qwen-plus")

    def test_qwen_editorial_uses_the_same_selected_runtime(self):
        from three_agent_service import ThreeAgentRequestData, ThreeAgentService

        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"checks": [], "issues": []}'),
            finish_reason="stop",
        )])
        entered_client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))))

        class ClientContext:
            async def __aenter__(self):
                return entered_client

            async def __aexit__(self, *_):
                return False

        environment = {
            "DASHSCOPE_API_KEY": "editor-key",
            "QWEN_BASE_URL": "https://editor-qwen.example/v1",
            "QWEN_MODEL": "qwen-plus",
        }
        finalizer = AsyncMock(return_value=("已校订", {"status": "ready", "passed": True}))
        with patch.dict("os.environ", environment, clear=True), patch(
            "three_agent_service.AsyncOpenAI", return_value=ClientContext()
        ) as client_factory, patch("three_agent_service.finalize_report", new=finalizer):
            service = ThreeAgentService(ThreeAgentRequestData(task="GTF", llm_provider="qwen"))
            service.generation_status = "ready"
            result = asyncio.run(service.editorial_agent("待校订"))
            complete = finalizer.await_args.kwargs["complete"]
            asyncio.run(complete("复查提示", "review"))

        self.assertEqual(result, "已校订")
        self.assertEqual(client_factory.call_args.kwargs["api_key"], "editor-key")
        self.assertEqual(client_factory.call_args.kwargs["base_url"], "https://editor-qwen.example/v1")
        self.assertEqual(entered_client.chat.completions.create.await_args.kwargs["model"], "qwen-plus")

    def test_concurrent_deepseek_and_qwen_research_do_not_share_runtime(self):
        from three_agent_service import ThreeAgentRequestData, ThreeAgentService

        def researcher():
            return SimpleNamespace(
                cfg=SimpleNamespace(llm_kwargs={}),
                conduct_research=AsyncMock(),
                write_report=AsyncMock(return_value="研究稿"),
                get_research_context=lambda: [],
                get_research_sources=lambda: [],
            )

        deep_researcher, qwen_researcher = researcher(), researcher()
        environment = {
            "OPENAI_API_KEY": "deep-key",
            "OPENAI_BASE_URL": "https://deep.example/v1",
            "SMART_LLM": "openai:deepseek-chat",
            "DASHSCOPE_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example/v1",
            "QWEN_MODEL": "qwen-plus",
        }

        def factory(*, query, **_):
            return deep_researcher if query == "deep-task" else qwen_researcher

        async def research_both(deep, qwen):
            return await asyncio.gather(
                deep._research_one("deep-task", 1),
                qwen._research_one("qwen-task", 1),
            )

        with patch.dict("os.environ", environment, clear=True), patch(
            "three_agent_service.GPTResearcher", side_effect=factory
        ):
            deep = ThreeAgentService(ThreeAgentRequestData(task="GTF", llm_provider="deepseek"))
            qwen = ThreeAgentService(ThreeAgentRequestData(task="GTF", llm_provider="qwen"))
            asyncio.run(research_both(deep, qwen))
            self.assertEqual(os.environ["OPENAI_BASE_URL"], "https://deep.example/v1")

        self.assertEqual(deep_researcher.cfg.llm_kwargs["openai_api_key"], "deep-key")
        self.assertEqual(deep_researcher.cfg.llm_kwargs["openai_api_base"], "https://deep.example/v1")
        self.assertEqual(deep_researcher.cfg.smart_llm_model, "deepseek-chat")
        self.assertEqual(qwen_researcher.cfg.llm_kwargs["openai_api_key"], "qwen-key")
        self.assertEqual(qwen_researcher.cfg.llm_kwargs["openai_api_base"], "https://qwen.example/v1")
        self.assertEqual(qwen_researcher.cfg.smart_llm_model, "qwen-plus")

    def test_http_catalog_and_missing_qwen_configuration(self):
        import httpx
        import main

        async def exercise():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                catalog_response = await client.get("/api/model-providers")
                report_response = await client.post(
                    "/api/three-agent-report",
                    json={"task": "配置检查", "llm_provider": "qwen"},
                )
                return catalog_response, report_response

        with patch.dict("os.environ", {}, clear=True):
            catalog_response, report_response = asyncio.run(exercise())

        self.assertEqual(catalog_response.status_code, 200)
        self.assertFalse(next(
            item for item in catalog_response.json()["providers"] if item["id"] == "qwen"
        )["configured"])
        self.assertEqual(report_response.status_code, 400)
        self.assertIn("DASHSCOPE_API_KEY", report_response.json()["detail"])

    def test_explicit_request_base_url_wins_over_global_openai_base(self):
        from gpt_researcher.utils import llm

        provider = SimpleNamespace(
            get_chat_response=AsyncMock(return_value="ok"),
            last_response_metadata={},
            last_usage_metadata=None,
        )
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://deepseek.example/v1"}), patch.object(
            llm, "get_llm", return_value=provider
        ) as factory:
            result = asyncio.run(llm.create_chat_completion(
                messages=[{"role": "user", "content": "test"}],
                model="qwen-plus",
                llm_provider="openai",
                llm_kwargs={
                    "openai_api_key": "qwen-secret",
                    "openai_api_base": "https://qwen.example/v1",
                },
            ))

        self.assertEqual(result, "ok")
        self.assertEqual(factory.call_args.kwargs["openai_api_base"], "https://qwen.example/v1")

    def test_frontend_offers_and_submits_model_provider(self):
        project = Path(__file__).resolve().parents[1]
        html = (project / "frontend/index.html").read_text(encoding="utf-8")
        script = (project / "frontend/scripts.js").read_text(encoding="utf-8")

        self.assertIn('id="llmProviderSelect"', html)
        self.assertIn('value="deepseek"', html)
        self.assertIn('value="qwen"', html)
        self.assertIn("/api/model-providers", script)
        self.assertIn("llm_provider:", script)


if __name__ == "__main__":
    unittest.main()
