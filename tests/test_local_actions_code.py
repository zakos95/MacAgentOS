import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class LocalActionCodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_code_snake_returns_code_task(self):
        result = await server.plan_local_action(server.LocalActionPlanRequest(message="code un snake"))

        self.assertEqual(result["type"], "approval_required")
        self.assertEqual(result["action"]["type"], "code_task")
        self.assertEqual(result["action"]["payload"]["target_path"], "~/Desktop/snake.html")

    async def test_execute_snake_code_task_creates_html_without_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Desktop").mkdir()
            action = server.LocalActionExecuteAction(
                type="code_task",
                payload=server.LocalActionExecuteActionPayload(
                    target_path="~/Desktop/snake.html",
                    instruction="code un snake",
                ),
            )

            with patch.object(server.Path, "home", return_value=home):
                result = await server._execute_local_action_inner(action)

            output = home / "Desktop" / "snake.html"
            self.assertEqual(result["status"], "success")
            self.assertTrue(output.exists())
            content = output.read_text(encoding="utf-8")
            self.assertIn("<canvas", content)
            self.assertIn("function tick", content)

    async def test_natural_can_you_analyze_my_mac_is_local_action(self):
        result = await server.plan_local_action(server.LocalActionPlanRequest(message="tu peux analysé mon mac ?"))

        self.assertEqual(result["type"], "approval_required")
        self.assertEqual(result["action"]["type"], "analyze_mac")

    async def test_natural_can_you_look_at_storage_is_local_action_even_if_router_says_chat(self):
        with patch.object(server, "route_intent", return_value="chat"):
            result = await server.plan_local_action(
                server.LocalActionPlanRequest(message="tu peut regarder l'etat de stockage de mon mac ?")
            )

        self.assertEqual(result["type"], "approval_required")
        self.assertEqual(result["action"]["type"], "analyze_storage")


if __name__ == "__main__":
    unittest.main()
