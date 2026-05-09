import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import server
from skills import SkillsManager


class FakeOllamaProvider:
    def chat(self, messages, tools=None, files=None):
        system_text = messages[0].get("content", "") if messages else ""
        return {
            "type": "text",
            "content": "Bonjour",
            "system_text": system_text,
        }


class SequenceProvider:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, tools=None, files=None):
        if not self.responses:
            return {"type": "text", "content": '{"final":"Terminé."}'}
        return {"type": "text", "content": self.responses.pop(0)}


class SkillsTests(unittest.IsolatedAsyncioTestCase):
    def test_enable_disable_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            manager = SkillsManager(settings_path=settings_path)

            manager.set_enabled("code_helper", True)
            reloaded = SkillsManager(settings_path=settings_path)
            payload = reloaded.to_dict("code_helper", {"active_tools": ["filesystem"], "skipped_tools": []})

            self.assertTrue(payload["enabled"])

            reloaded.set_enabled("code_helper", False)
            reloaded_again = SkillsManager(settings_path=settings_path)
            payload = reloaded_again.to_dict("code_helper", {"active_tools": ["filesystem"], "skipped_tools": []})

            self.assertFalse(payload["enabled"])

    def test_code_helper_has_local_filesystem_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            payload = manager.to_dict(
                "code_helper",
                {"active_tools": ["provider_diagnostics", "filesystem"], "skipped_tools": ["filesystem"]},
            )

        self.assertTrue(payload["available"])

    async def test_get_skills_endpoint_returns_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_manager = server.state.skills_manager
            old_sessions = server.state.core.hub.sessions
            old_skipped = server.state.core.hub.skipped_servers
            try:
                server.state.skills_manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
                server.state.core.hub.sessions = {"mac-control": object(), "safari": object()}
                server.state.core.hub.skipped_servers = [{"name": "filesystem", "reason": "missing"}]
                with patch.object(
                    server,
                    "_ollama_status",
                    new=AsyncMock(return_value={"available": True, "models": ["qwen3:8b"]}),
                ):
                    result = await server.list_skills()
            finally:
                server.state.skills_manager = old_manager
                server.state.core.hub.sessions = old_sessions
                server.state.core.hub.skipped_servers = old_skipped

        ids = {skill["id"] for skill in result["skills"]}
        self.assertIn("mac_control", ids)
        self.assertIn("provider_doctor", ids)

    async def test_chat_with_mac_control_skill_keeps_ollama_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("mac_control", True)

            old_manager = server.state.skills_manager
            old_sessions = server.state.core.hub.sessions
            try:
                server.state.skills_manager = manager
                server.state.core.hub.sessions = {"mac-control": object()}

                provider_calls = []

                def fake_get_provider(provider, api_key="", model="", base_url=""):
                    provider_calls.append(provider)
                    if provider != "ollama":
                        raise AssertionError(f"unexpected provider: {provider}")
                    return FakeOllamaProvider()

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="Ouvre Safari puis réponds Bonjour",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
            finally:
                server.state.skills_manager = old_manager
                server.state.core.hub.sessions = old_sessions

        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("actual", {}).get("model"), "qwen3:8b")
        self.assertIn("Skills actifs pertinents", result.get("system_text", ""))
        self.assertTrue(all(provider == "ollama" for provider in provider_calls))

    async def test_storage_skill_runs_tool_and_keeps_requested_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("local_models", True)

            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = manager

                provider_calls = []

                def fake_get_provider(provider, api_key="", model="", base_url=""):
                    provider_calls.append(provider)
                    if provider != "ollama":
                        raise AssertionError(f"unexpected provider: {provider}")
                    return FakeOllamaProvider()

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="analyse mon mac et dis moi ce qui occupe le plus de stockage",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server, "_run_storage_analysis", return_value="Analyse stockage locale terminée.\n- 10 Go — /Users/test/Downloads"), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
            finally:
                server.state.skills_manager = old_manager

        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("actual", {}).get("model"), "qwen3:8b")
        self.assertIn("Analyse stockage locale terminée", result.get("content", ""))
        self.assertTrue(all(provider == "ollama" for provider in provider_calls))

    async def test_mac_analysis_skill_handles_typo_and_keeps_requested_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("mac_control", True)

            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = manager

                provider_calls = []

                def fake_get_provider(provider, api_key="", model="", base_url=""):
                    provider_calls.append(provider)
                    if provider != "ollama":
                        raise AssertionError(f"unexpected provider: {provider}")
                    return FakeOllamaProvider()

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="tu peut annalysé mon mac ?",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    allow_auto_routing=False,
                    reasoning=True,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server, "_run_mac_analysis", return_value="Analyse locale du Mac terminée.\nMac: Test"), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
            finally:
                server.state.skills_manager = old_manager

        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("actual", {}).get("model"), "qwen3:8b")
        self.assertIn("Analyse locale du Mac terminée", result.get("content", ""))
        self.assertEqual(result.get("agent", {}).get("used_tools"), ["analyze_mac"])
        self.assertTrue(all(provider == "ollama" for provider in provider_calls))

    async def test_code_helper_project_overview_keeps_requested_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "server.py").write_text("print('hello')\n", encoding="utf-8")
            (project / "README.md").write_text("# Demo\n", encoding="utf-8")

            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("code_helper", True)

            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = manager

                provider_calls = []

                def fake_get_provider(provider, api_key="", model="", base_url=""):
                    provider_calls.append(provider)
                    if provider != "ollama":
                        raise AssertionError(f"unexpected provider: {provider}")
                    return FakeOllamaProvider()

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="analyse ce projet",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    project_path=str(project),
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
            finally:
                server.state.skills_manager = old_manager

        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("agent", {}).get("used_tools"), ["project_overview"])
        self.assertIn("Projet:", result.get("content", ""))
        self.assertIn("server.py", result.get("content", ""))
        self.assertTrue(all(provider == "ollama" for provider in provider_calls))

    def test_code_helper_can_write_read_and_refuse_destructive_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            written = server._write_project_file_tool("src/app.py", "print('ok')\n", str(project))
            read = server._read_project_file_tool("src/app.py", str(project))
            refused = server._run_project_command_tool("rm -rf src", str(project))

        self.assertIn("Fichier projet écrit", written)
        self.assertIn("print('ok')", read)
        self.assertIn("Commande refusée", refused)

    def test_project_command_falls_back_from_missing_pytest_to_unittest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "tests").mkdir()

            calls = []

            def fake_run(args, **kwargs):
                calls.append(args)
                if args[0] == "pytest":
                    raise FileNotFoundError("pytest")
                return server.subprocess.CompletedProcess(args, 0, stdout="OK\n", stderr="")

            with patch.object(server.subprocess, "run", side_effect=fake_run):
                result = server._run_project_command_tool("pytest", str(project))

        self.assertIn("Commande adaptée depuis pytest", result)
        self.assertIn("OK", result)
        self.assertEqual(calls[1][:3], ["python", "-m", "unittest"])

    def test_project_command_expands_bare_unittest_to_discover(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "tests").mkdir()

            calls = []

            def fake_run(args, **kwargs):
                calls.append(args)
                return server.subprocess.CompletedProcess(args, 0, stdout="Ran 1 test in 0.001s\nOK\n", stderr="")

            with patch.object(server.subprocess, "run", side_effect=fake_run):
                result = server._run_project_command_tool("python -m unittest", str(project))

        self.assertIn("discover -s tests", result)
        self.assertEqual(calls[0], ["python", "-m", "unittest", "discover", "-s", "tests"])

    async def test_code_helper_runs_project_tests_without_provider_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "tests").mkdir()

            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("code_helper", True)

            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = manager

                def fake_get_provider(provider, api_key="", model="", base_url=""):
                    if provider != "ollama":
                        raise AssertionError(f"unexpected provider: {provider}")
                    return FakeOllamaProvider()

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="lance les tests du projet",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    project_path=str(project),
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server, "_run_project_command_tool", return_value="Commande réussie: python -m unittest discover -s tests"), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
            finally:
                server.state.skills_manager = old_manager

        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("agent", {}).get("used_tools"), ["run_project_command"])
        self.assertIn("Commande réussie", result.get("content", ""))

    async def test_project_coding_agent_edits_tests_and_keeps_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "app.py").write_text("def greet():\n    return 'bonjor'\n", encoding="utf-8")

            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("code_helper", True)

            provider = SequenceProvider([
                '{"tool":"read_project_file","args":{"path":"app.py"}}',
                '{"tool":"replace_project_text","args":{"path":"app.py","old_text":"bonjor","new_text":"bonjour","expected_count":1}}',
                '{"tool":"run_project_command","args":{"command":"python -m unittest discover -s tests"}}',
                '{"final":"Bug corrigé dans app.py. Tests lancés avec succès."}',
            ])
            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = manager

                def fake_get_provider(provider_name, api_key="", model="", base_url=""):
                    if provider_name != "ollama":
                        raise AssertionError(f"unexpected provider: {provider_name}")
                    return provider

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="corrige le bug dans le code du projet et lance les tests",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    project_path=str(project),
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server, "_run_project_command_tool", return_value="Commande réussie: python -m unittest discover -s tests"), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
                edited_content = (project / "app.py").read_text(encoding="utf-8")
            finally:
                server.state.skills_manager = old_manager

        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("agent", {}).get("mode"), "project_coding")
        self.assertIn("replace_project_text", result.get("agent", {}).get("used_tools", []))
        self.assertIn("bonjour", edited_content)

    async def test_project_coding_agent_remembers_failed_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "app.py").write_text("value = 1\n", encoding="utf-8")

            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("code_helper", True)
            provider = SequenceProvider([
                '{"tool":"run_project_command","args":{"command":"python -m unittest discover -s tests"}}',
                '{"tool":"read_project_file","args":{"path":"app.py"}}',
                '{"final":"J’ai mémorisé l’échec et je sais quoi corriger ensuite."}',
            ])

            old_manager = server.state.skills_manager
            old_lessons_path = server.PROJECT_LESSONS_PATH
            try:
                server.state.skills_manager = manager
                server.PROJECT_LESSONS_PATH = Path(tmp) / "lessons.json"

                def fake_get_provider(provider_name, api_key="", model="", base_url=""):
                    if provider_name != "ollama":
                        raise AssertionError(f"unexpected provider: {provider_name}")
                    return provider

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="corrige le bug dans le projet",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    project_path=str(project),
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server, "_run_project_command_tool", return_value="Commande échouée avec code 1\nAssertionError: boom"), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
                lessons_path = Path(tmp) / "lessons.json"
                lessons_text = lessons_path.read_text(encoding="utf-8") if lessons_path.exists() else ""
            finally:
                server.PROJECT_LESSONS_PATH = old_lessons_path
                server.state.skills_manager = old_manager

        self.assertEqual(result.get("agent", {}).get("mode"), "project_coding")
        self.assertIn("AssertionError", lessons_text)

    async def test_project_coding_agent_auto_validates_after_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "tests").mkdir()
            (project / "app.py").write_text("value = 'bad'\n", encoding="utf-8")

            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("code_helper", True)
            provider = SequenceProvider([
                '{"tool":"replace_project_text","args":{"path":"app.py","old_text":"bad","new_text":"good","expected_count":1}}',
                '{"final":"Correction appliquée."}',
            ])

            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = manager

                def fake_get_provider(provider_name, api_key="", model="", base_url=""):
                    if provider_name != "ollama":
                        raise AssertionError(f"unexpected provider: {provider_name}")
                    return provider

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="corrige le bug dans le projet",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    project_path=str(project),
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server, "_run_project_command_tool", return_value="Commande réussie: python -m unittest discover -s tests\nOK"), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
            finally:
                server.state.skills_manager = old_manager

        self.assertIn("Validation automatique", result.get("content", ""))
        self.assertIn("run_project_command", result.get("agent", {}).get("used_tools", []))


if __name__ == "__main__":
    unittest.main()
