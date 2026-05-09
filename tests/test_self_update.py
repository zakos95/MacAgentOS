import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import self_update
import server
from skills import SkillsManager


class SelfUpdateTests(unittest.IsolatedAsyncioTestCase):
    def test_prepare_creates_safe_and_working_copies_without_build_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "server.py").write_text("print('ok')\n", encoding="utf-8")
            (source / "build").mkdir()
            (source / "build" / "junk.txt").write_text("junk", encoding="utf-8")

            destination = Path(tmp) / "MacAgentOS-SelfUpdate"
            result = self_update.prepare(str(source), str(destination))

            self.assertEqual(result["status"], "ok")
            self.assertTrue((destination / "MacAgentOS-SAFE" / "server.py").exists())
            self.assertTrue((destination / "MacAgentOS-WORKING" / "server.py").exists())
            self.assertFalse((destination / "MacAgentOS-WORKING" / "build").exists())
            self.assertTrue((destination / "self_update_manifest.json").exists())

    def test_prepare_does_not_overwrite_existing_safe_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "server.py").write_text("original\n", encoding="utf-8")
            destination = Path(tmp) / "workspace"

            self_update.prepare(str(source), str(destination))
            (destination / "MacAgentOS-SAFE" / "server.py").write_text("safe\n", encoding="utf-8")
            (source / "server.py").write_text("changed\n", encoding="utf-8")
            self_update.prepare(str(source), str(destination))

            self.assertEqual((destination / "MacAgentOS-SAFE" / "server.py").read_text(encoding="utf-8"), "safe\n")

    async def test_self_update_skill_and_tool_are_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
                context = await server._skills_runtime_context(check_ollama=False)
                payload = server.state.skills_manager.to_dict("self_update_lab", context)
                tools = server._agent_tool_specs(context)
            finally:
                server.state.skills_manager = old_manager

        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["available"])
        self.assertIn("prepare_self_update_workspace", tools)
        self.assertIn("auto_update_candidate", tools)
        self.assertIn("promote_self_update_candidate", tools)
        self.assertIn("request_self_update_from_llm", tools)

    def test_promote_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate.app"
            target = Path(tmp) / "target.app"
            candidate.mkdir()

            result = self_update.promote_candidate(str(candidate), str(target))

        self.assertEqual(result["status"], "error")
        self.assertIn("confirmation", result["message"].lower())

    def test_promote_creates_backup_and_replaces_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate.app"
            target = Path(tmp) / "target.app"
            candidate.mkdir()
            target.mkdir()
            (candidate / "version.txt").write_text("new", encoding="utf-8")
            (target / "version.txt").write_text("old", encoding="utf-8")

            result = self_update.promote_candidate(
                str(candidate),
                str(target),
                backup_root=str(Path(tmp) / "backups"),
                confirmation="PROMOTE_MAC_AGENT_OS_CANDIDATE",
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual((target / "version.txt").read_text(encoding="utf-8"), "new")
            backup_path = Path(result["backup_app"])
            self.assertTrue((backup_path / "version.txt").exists())
            self.assertEqual((backup_path / "version.txt").read_text(encoding="utf-8"), "old")

    def test_diagnose_returns_repair_hints_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "working"
            working.mkdir()
            (working / "server.py").write_text("def broken(:\n", encoding="utf-8")
            (working / "mcp_hub.py").write_text("", encoding="utf-8")
            (working / "llm_universal.py").write_text("", encoding="utf-8")
            (working / "provider_connections.py").write_text("", encoding="utf-8")
            (working / "skills.py").write_text("", encoding="utf-8")
            (working / "self_update.py").write_text("", encoding="utf-8")

            result = self_update.diagnose(str(working))

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["suggestions"])
        self.assertIn("syntax", " ".join(result["suggestions"]).lower())

    def test_auto_update_can_build_candidate_without_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "working"
            working.mkdir()

            with patch.object(self_update, "diagnose", return_value={"status": "ok", "message": "ok"}), \
                 patch.object(self_update, "build_candidate", return_value={"status": "ok", "candidate_app": str(Path(tmp) / "candidate.app")}):
                result = self_update.auto_update(str(working), str(Path(tmp) / "out"))

        self.assertEqual(result["status"], "ok")
        self.assertIn("candidate", result["message"].lower())

    async def test_self_update_run_cycle_requests_llm_and_builds_candidate_with_visible_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "working"
            working.mkdir()
            candidate = Path(tmp) / "candidate.app"
            proposal = working / ".macagent" / "self_update_proposals" / "proposal.md"

            with patch.object(server.self_update_manager, "diagnose", return_value={"status": "ok", "message": "Diagnostic terminé."}), \
                 patch.object(server, "_request_self_update_from_llm", new=AsyncMock(return_value={
                     "status": "ok",
                     "message": "Proposition IA générée.",
                     "proposal_path": str(proposal),
                     "ai_response": "Patch proposé.",
                     "provider": "ollama",
                     "model": "qwen3:8b",
                     "context_files": ["server.py"],
                 })), \
                 patch.object(server.self_update_manager, "build_candidate", return_value={
                     "status": "ok",
                     "message": "Candidate buildée.",
                     "candidate_app": str(candidate),
                 }):
                result = await server.self_update_run_cycle(server.SelfUpdatePathRequest(
                    working_path=str(working),
                    output_root=str(Path(tmp) / "out"),
                    objective="rends l'autoupdate réel",
                ))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["proposal_path"], str(proposal))
        self.assertEqual(result["build"]["candidate_app"], str(candidate))
        self.assertEqual([step["name"] for step in result["steps"]], ["Diagnostic", "Demande IA", "Build candidate"])
        self.assertTrue(all(step["status"] == "ok" for step in result["steps"]))

    async def test_self_update_run_cycle_stops_when_llm_update_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "working"
            working.mkdir()

            with patch.object(server.self_update_manager, "diagnose", return_value={"status": "ok", "message": "Diagnostic terminé."}), \
                 patch.object(server, "_request_self_update_from_llm", new=AsyncMock(return_value={
                     "status": "error",
                     "message": "Aucune session ChatGPT détectée.",
                     "provider": "local_chatgpt_codex",
                     "model": "gpt-5.4",
                 })), \
                 patch.object(server.self_update_manager, "build_candidate") as build_candidate:
                result = await server.self_update_run_cycle(server.SelfUpdatePathRequest(
                    working_path=str(working),
                    output_root=str(Path(tmp) / "out"),
                ))

        self.assertEqual(result["status"], "error")
        self.assertIn("proposition IA", result["message"])
        self.assertEqual([step["name"] for step in result["steps"]], ["Diagnostic", "Demande IA"])
        build_candidate.assert_not_called()

    def test_backend_rebuild_detects_newer_backend_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "working"
            dist = working / "dist"
            dist.mkdir(parents=True)
            backend = dist / "MacAgentServer"
            source = working / "opencode_bridge.py"
            backend.write_text("old", encoding="utf-8")
            source.write_text("new", encoding="utf-8")

            self.assertTrue(self_update._backend_needs_rebuild(working, backend))

    def test_validation_python_prefers_manifest_when_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            working = root / "MacAgentOS-WORKING"
            working.mkdir(parents=True)
            python_path = Path(tmp) / "python"
            python_path.write_text("", encoding="utf-8")
            (root / "self_update_manifest.json").write_text(
                '{"validation_python": "' + str(python_path) + '"}',
                encoding="utf-8",
            )

            with patch.object(self_update.sys, "frozen", True, create=True):
                resolved = self_update._validation_python(working)

        self.assertEqual(resolved, str(python_path))

    async def test_self_update_chat_prepare_keeps_requested_provider(self):
        class FakeProvider:
            def chat(self, messages, tools=None, files=None):
                return {"type": "text", "content": "fallback should not be used"}

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "server.py").write_text("print('ok')\n", encoding="utf-8")
            destination = Path(tmp) / "workspace"
            manager = SkillsManager(settings_path=Path(tmp) / "settings.json")
            manager.set_enabled("self_update_lab", True)

            old_manager = server.state.skills_manager
            try:
                server.state.skills_manager = manager

                def fake_get_provider(provider, api_key="", model="", base_url=""):
                    if provider != "ollama":
                        raise AssertionError(f"unexpected provider: {provider}")
                    return FakeProvider()

                req = server.LLMChatRequest(
                    provider="ollama",
                    model="qwen3:8b",
                    message="duplique le projet en v1.2 avec une copie safe",
                    system_prompt="Tu es concis.",
                    base_url="http://localhost:11434",
                    project_path=str(source),
                    allow_auto_routing=False,
                )

                with patch.object(server, "get_provider", side_effect=fake_get_provider), \
                     patch.object(server, "_get_cached_models", return_value=["qwen3:8b"]), \
                     patch.object(server.self_update_manager, "workspace_from_root", return_value=self_update.workspace_from_root(str(destination))), \
                     patch.object(server.token_optimizer, "update_memory", return_value=""):
                    result = await server.llm_chat(req)
            finally:
                server.state.skills_manager = old_manager

        self.assertEqual(result.get("actual", {}).get("provider"), "ollama")
        self.assertEqual(result.get("agent", {}).get("used_tools"), ["prepare_self_update_workspace"])
        self.assertIn("Workspace self-update prêt", result.get("content", ""))

    async def test_self_update_llm_request_sends_logs_and_code_to_active_provider(self):
        class FakeProvider:
            def __init__(self):
                self.messages = []

            def chat(self, messages, tools=None, files=None):
                self.messages = messages
                return {"type": "text", "content": "Plan IA: modifier self_update.py puis relancer les tests."}

        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "working"
            working.mkdir()
            provider = FakeProvider()
            old_settings = server.state.core.get_settings
            try:
                server.state.core.get_settings = lambda: {
                    "provider": "ollama",
                    "model": "qwen3:8b",
                    "base_url": "http://localhost:11434",
                    "api_key": "",
                }
                with patch.object(server, "get_provider", return_value=provider), \
                     patch.object(server.self_update_manager, "diagnose", return_value={"status": "ok", "message": "Validation réussie."}), \
                     patch.object(server, "get_logs", return_value={"entries": ["Backend prêt"], "path": ""}), \
                     patch.object(server, "_self_update_context_files", return_value={"self_update.py": "def validate(): pass"}):
                    result = await server._request_self_update_from_llm(str(working), "améliore l'autoupdate")

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["provider"], "ollama")
                self.assertTrue(Path(result["proposal_path"]).exists())
                prompt = provider.messages[-1]["content"]
                self.assertIn("Backend prêt", prompt)
                self.assertIn("def validate", prompt)
                self.assertIn("améliore l'autoupdate", prompt)
            finally:
                server.state.core.get_settings = old_settings


if __name__ == "__main__":
    unittest.main()
