import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

import server
from skills import SkillsManager
from team_runtime import TeamService


class ClaudeTextWorkflows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.personal = root / "home"
        self.project = root / "project"
        manager = SkillsManager(root / "settings.json", personal_root=self.personal)
        for target, attr, value in [
            (server.state, "skills_manager", manager),
            (server, "teams", TeamService()),
            (server, "_skills_runtime_context", AsyncMock(return_value={})),
        ]:
            change = patch.object(target, attr, value)
            change.start()
            self.addCleanup(change.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://localhost",
            headers={"Authorization": f"Bearer {server._API_KEY}"},
        )
        self.addAsyncCleanup(self.client.aclose)

    def skill(self, root, name, body):
        directory = root / ".claude" / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(body, encoding="utf-8")
        return directory

    async def chat(self, message, **kwargs):
        resp = await self.client.post("/api/llm/chat", json={
            "provider": "ollama", "model": "selected", "project_path": str(self.project),
            "message": message, **kwargs,
        })
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    async def test_catalog_invocation_references_and_persisted_toggle(self):
        directory = self.skill(self.project, "letter", "---\nname: courrier\narguments: [recipient]\ndescription: >\n  Écrire une\n  lettre.\n---\nPour $recipient et $1 : $ARGUMENTS. [Guide](references.md)")
        (directory / "references.md").write_text("Formule du guide.")
        params = {"project_path": str(self.project)}
        listed = (await self.client.get("/api/skills", params=params)).json()["skills"]
        self.assertIn(("claude:letter", "/courrier", "Écrire une lettre.\n"),
                      [(s["id"], s["name"], s["description"]) for s in listed])
        provider = Mock(model="selected")
        provider.chat.return_value = {"type": "text", "content": "une vraie réponse du fournisseur"}
        with patch.object(server, "get_provider", return_value=provider), patch.object(server, "_run_skill_agent", new=AsyncMock()) as tools:
            reply = await self.chat('/courrier "Marie Rose" mardi')
            self.assertEqual(reply["content"], "une vraie réponse du fournisseur")
            self.assertIn('Pour Marie Rose et mardi : "Marie Rose" mardi.', provider.chat.call_args.args[0][-1]["content"])
            self.assertIn("Formule du guide.", provider.chat.call_args.args[0][-1]["content"])
            await self.client.post("/api/skills/claude:letter/disable", params=params)
            self.assertEqual((await self.chat("/letter salut"))["type"], "error")
            provider.chat.assert_called_once()
            tools.assert_not_awaited()

    async def test_scopes_and_unsupported_skills(self):
        self.skill(self.personal, "word", "Personnelle")
        self.skill(self.project, "word", "Projet")
        self.skill(self.project, "special", "---\nhooks: {PreToolUse: []}\n---\nExemple.")
        self.skill(self.project, "inside", "[Guide](../../../../outside.md)")
        self.skill(self.project, "bad", "---\n: malformed :\n---\n")
        self.skill(self.project, "hidden", "---\nuser-invocable: false\n---\nInterne")
        self.skill(self.project, "dynamic", "!\x60echo bonjour\x60")
        self.skill(self.project, "size", "x" * (129 * 1024))
        p = Mock(model="selected")
        p.chat.return_value = {"type": "text", "content": "ok"}
        with patch.object(server, "get_provider", return_value=p):
            self.assertEqual((await self.chat("/word"))["type"], "text")
            self.assertEqual(p.chat.call_args.args[0][-1]["content"], "Personnelle")
            for name in ("bad", "dynamic", "hidden", "size", "special", "inside", "unknown"):
                with self.subTest(name=name):
                    self.assertEqual((await self.chat("/" + name))["type"], "error")
            p.chat.assert_called_once()

    async def test_team_parallelism_isolation_and_custom_profiles(self):
        agent_dir = self.project / ".claude" / "agents"
        agent_dir.mkdir(parents=True)
        (agent_dir / "auteur.md").write_text("---\nname: auteur\nmodel: inherit\n---\nInstruction de l’auteur.")
        barrier = threading.Barrier(2, timeout=5)
        requests = []

        def respond(messages):
            prompt = messages[-1]["content"]
            requests.append(prompt)
            if "contributions indépendantes" in prompt:
                self.assertIn("proposition-fixture", prompt)
                self.assertIn("relecture-fixture", prompt)
                content = "synthèse-fixture"
            else:
                barrier.wait()
                content = "proposition-fixture" if "Instruction de l’auteur" in prompt else "relecture-fixture"
            return {"type": "text", "content": content}

        p = Mock(model="selected")
        p.chat.side_effect = respond
        with patch.object(server, "get_provider", return_value=p):
            reply = await self.chat("/team @auteur @relecteur Prépare le programme",
                                    history=[{"role": "user", "content": "secret historique"}])
        self.assertEqual(reply["content"], "synthèse-fixture")
        self.assertEqual([r["role"] for r in reply["team"]["results"]], ["auteur", "relecteur"])
        self.assertEqual(len(requests), 3)
        self.assertFalse(any("secret historique" in text for text in requests))
        self.assertNotIn("relecture-fixture", requests[0])
        self.assertNotIn("proposition-fixture", requests[1])
        self.assertEqual((await self.client.get("/api/worktogether/status")).json()["summary"], reply["content"])

    async def test_concurrent_start_stop_prevents_synthesis(self):
        started, release = threading.Event(), threading.Event()
        def respond(messages):
            started.set()
            release.wait(timeout=4)
            return {"type": "text", "content": "worker"}
        p = Mock(model="selected")
        p.chat.side_effect = respond
        payload = {"goal": "un programme", "provider": "ollama", "model": "selected"}
        with patch.object(server, "get_provider", return_value=p):
            self.assertEqual((await self.client.post("/api/worktogether/start", json=payload)).json()["status"], "started")
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            try:
                self.assertEqual((await self.client.post("/api/worktogether/start", json=payload)).status_code, 409)
                self.assertEqual((await self.client.post("/api/worktogether/stop")).json()["status"], "stopping")
            finally:
                release.set()
            await server.teams.current.job
            self.assertEqual((await self.client.get("/api/worktogether/status")).json()["status"], "stopped")
            self.assertEqual(p.chat.call_count, 2)

    async def test_team_failures_and_follow_up(self):
        p = Mock(model="selected")
        p.chat.return_value = {"type": "error", "content": "invalid provider key"}
        with patch.object(server, "get_provider", return_value=p):
            failed = await self.chat("/team Planifie la visite")
            self.assertEqual(failed["type"], "error")
            self.assertEqual(failed["team"]["status"], "failed")
            self.assertEqual(p.chat.call_count, 2)
            self.assertEqual((await self.chat("/team @absent Visite"))["type"], "error")
            self.assertEqual((await self.chat("/team"))["type"], "error")
            p.chat.return_value = {"type": "text", "content": "Une réponse de test."}
            self.assertEqual((await self.chat("/team Visite"))["type"], "text")
            answer = await self.client.post("/api/worktogether/chat", json={"message": "À quelle heure ?"})
            self.assertEqual(answer.json()["response"], "Une réponse de test.")
            self.assertIn("À quelle heure ?", p.chat.call_args.args[0][-1]["content"])
