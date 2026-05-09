import unittest
from unittest.mock import patch

import server
from core import MacAgentCore
from llm_connector import LLMConnector


class FakeOllamaProvider:
    def __init__(self, calls):
        self.calls = calls

    def chat(self, messages, tools=None, files=None):
        self.calls.append(("chat", messages, tools, files))
        return {"type": "text", "content": "Bonjour"}


class OllamaRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_ollama_status_normalizes_chat_endpoint_base_url(self):
        self.assertEqual(
            server._normalize_ollama_base_url("http://localhost:11434/api/chat"),
            "http://localhost:11434",
        )
        self.assertEqual(
            server._normalize_ollama_base_url("http://localhost:11434/api/tags"),
            "http://localhost:11434",
        )

    async def test_llm_chat_ollama_explicit_never_uses_opencode(self):
        provider_calls = []

        def fake_get_provider(provider, api_key="", model="", base_url=""):
            provider_calls.append((provider, model, base_url))
            if provider != "ollama":
                raise AssertionError(f"unexpected provider: {provider}")
            return FakeOllamaProvider(provider_calls)

        req = server.LLMChatRequest(
            provider="ollama",
            model="qwen3:8b",
            message="Réponds juste Bonjour",
            system_prompt="Réponds brièvement.",
            base_url="http://localhost:11434",
            allow_auto_routing=False,
        )

        with patch.object(server, "get_provider", side_effect=fake_get_provider), \
             patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
             patch.object(server.token_optimizer, "update_memory", return_value=""):
            result = await server.llm_chat(req)

        self.assertEqual(result.get("type"), "text")
        self.assertEqual(result.get("content"), "Bonjour")
        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("actual", {}).get("model"), "qwen3:8b")
        self.assertNotEqual(result.get("provider"), "local_chatgpt_codex")
        self.assertTrue(all(call[0] in {"ollama", "chat"} for call in provider_calls))

    async def test_llm_chat_reasoning_ollama_stays_ollama(self):
        def fake_get_provider(provider, api_key="", model="", base_url=""):
            if provider != "ollama":
                raise AssertionError(f"reasoning switched provider to {provider}")
            return FakeOllamaProvider([])

        req = server.LLMChatRequest(
            provider="ollama",
            model="qwen3:8b",
            message="Réponds juste Bonjour",
            system_prompt="Réfléchis puis réponds brièvement.",
            base_url="http://localhost:11434",
            reasoning=True,
            allow_auto_routing=False,
        )

        with patch.object(server, "get_provider", side_effect=fake_get_provider), \
             patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
             patch.object(server.token_optimizer, "update_memory", return_value=""):
            result = await server.llm_chat(req)

        self.assertEqual(result.get("type"), "text")
        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("route", {}).get("fallback_reason"), "")

    async def test_core_execute_explicit_ollama_ignores_existing_opencode_state(self):
        core = MacAgentCore()
        core.llm = LLMConnector(provider="local_chatgpt_codex", model="openai/gpt-5.4")

        def fake_get_provider(provider, api_key="", model="", base_url=""):
            if provider != "ollama":
                raise AssertionError(f"core model validation used {provider}")
            class FakeModels:
                def get_models(self, api_key=""):
                    return ["qwen3:8b"]
            return FakeModels()

        def fake_ask(self, system_prompt, user_message, tools_description=None, model_override=None):
            if self.provider != "ollama":
                raise AssertionError(f"OpenCode path used instead of Ollama: {self.provider}")
            self._last_model_override = model_override
            return {"type": "text", "content": "Bonjour"}

        with patch("core.get_provider", side_effect=fake_get_provider), \
             patch.object(LLMConnector, "ask", fake_ask), \
             patch.object(core.token_optimizer, "update_memory", return_value=""):
            events = await core.execute(
                "Réponds juste Bonjour",
                provider="ollama",
                model="qwen3:8b",
                base_url="http://localhost:11434",
                allow_auto_routing=False,
                system_prompt="Réponds brièvement.",
            )

        self.assertEqual(events[-1].event_type, "text")
        self.assertEqual(events[-1].content, "Bonjour")
        self.assertEqual(core.last_usage_telemetry["actual"]["provider"], "ollama")
        self.assertEqual(core.last_usage_telemetry["actual"]["model"], "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
