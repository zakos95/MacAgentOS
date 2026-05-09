import unittest

from llm_connector import LLMConnector
from llm_universal import get_provider
from provider_connections import get_provider_connection_specs


class HuggingFaceProviderTests(unittest.TestCase):
    def test_huggingface_present_in_provider_connections(self):
        ids = [spec["id"] for spec in get_provider_connection_specs()]
        self.assertIn("huggingface", ids)

    def test_huggingface_default_router_base_url(self):
        provider = get_provider("huggingface", api_key="hf_test")
        self.assertEqual(getattr(provider, "base_url", ""), "https://router.huggingface.co/v1")

    def test_huggingface_missing_token_returns_clear_error(self):
        provider = get_provider("huggingface", api_key="")
        result = provider.chat([{"role": "user", "content": "ping"}])
        self.assertEqual(result.get("type"), "error")
        self.assertIn("token", (result.get("content") or "").lower())

    def test_huggingface_connector_uses_router_chat_completions(self):
        connector = LLMConnector(provider="huggingface", model="openai/gpt-oss-120b", api_key="hf_test")
        self.assertEqual(connector.base_url, "https://router.huggingface.co/v1/chat/completions")

    def test_openai_without_api_key_does_not_fallback_to_bridge(self):
        provider = get_provider("openai", api_key="", model="gpt-4o-mini")
        result = provider.chat([{"role": "user", "content": "ping"}])
        self.assertEqual(result.get("type"), "error")
        self.assertIn("openai", (result.get("content") or "").lower())
        self.assertNotEqual(result.get("provider"), "local_chatgpt_codex")

    def test_custom_openai_compatible_invalid_url_is_rejected(self):
        with self.assertRaises(ValueError):
            get_provider("openai_compatible", api_key="", model="local-model", base_url="not-a-url")


if __name__ == "__main__":
    unittest.main()
