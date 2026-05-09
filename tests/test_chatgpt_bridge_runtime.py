import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import opencode_auth


class ChatGPTBridgeRuntimeTests(unittest.TestCase):
    def test_prefers_embedded_bridge_runtime_without_path_opencode(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp) / "ChatGPTBridge"
            bridge.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            bridge.chmod(bridge.stat().st_mode | stat.S_IXUSR)

            with patch.dict(os.environ, {"MACAGENT_BRIDGE_DIR": tmp}, clear=False), \
                 patch.object(opencode_auth.shutil, "which", return_value=""):
                self.assertEqual(opencode_auth.get_opencode_bin(), str(bridge))
                self.assertEqual(opencode_auth.get_bridge_runtime_source(), "bundled")

    def test_missing_bridge_runtime_returns_user_facing_error(self):
        with patch.dict(os.environ, {"MACAGENT_BRIDGE_DIR": "/tmp/missing-chatgpt-bridge"}, clear=False), \
             patch.object(opencode_auth.shutil, "which", return_value=""):
            result = opencode_auth.run_opencode_command(["models", "openai"])

        self.assertFalse(result["ok"])
        self.assertIn("ChatGPT Bridge runtime", result["error"])

    def test_codex_runtime_uses_chatgpt_login_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex = Path(tmp) / "codex"
            codex.write_text("#!/bin/sh\necho 'Logged in using ChatGPT'\nexit 0\n", encoding="utf-8")
            codex.chmod(codex.stat().st_mode | stat.S_IXUSR)

            with patch.dict(os.environ, {"MACAGENT_BRIDGE_DIR": tmp}, clear=False), \
                 patch.object(opencode_auth.shutil, "which", return_value=""):
                status = opencode_auth.get_openai_oauth_status()

        self.assertTrue(status["connected"])
        self.assertEqual(status["bridge_kind"], "codex")
        self.assertEqual(status["provider_name"], "ChatGPT")


if __name__ == "__main__":
    unittest.main()
