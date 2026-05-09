import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_hub


class MCPHubRuntimeResolutionTests(unittest.TestCase):
    def test_resolve_mcp_python_prefers_current_compatible_python(self):
        with patch.object(mcp_hub.sys, "executable", "/tmp/current-python"), \
             patch.object(mcp_hub, "_is_compatible_python", return_value=True):
            self.assertEqual(mcp_hub._resolve_mcp_python(), "/tmp/current-python")

    def test_resolve_server_config_skips_missing_path_command(self):
        params = mcp_hub._resolve_server_config(
            "filesystem",
            {"command": "definitely-not-installed-mcp-bin", "args": []},
            {"${PROJECT_ROOT}": str(mcp_hub.PROJECT_ROOT)},
        )
        self.assertIsNone(params)

    def test_resolve_server_config_uses_existing_path_command(self):
        python_path = shutil.which("python3")
        self.assertIsNotNone(python_path)

        params = mcp_hub._resolve_server_config(
            "mac-control",
            {"command": python_path, "args": [str(Path("mac_server.py").resolve())]},
            {},
        )

        self.assertIsNotNone(params)
        self.assertEqual(params.command, python_path)


if __name__ == "__main__":
    unittest.main()
