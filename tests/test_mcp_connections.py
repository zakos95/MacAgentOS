"""Exercise actual SDK transports on loopback, without developer credentials."""
import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import uvicorn
from mcp.server.fastmcp import FastMCP

from mcp_hub import MCPHub


@asynccontextmanager
async def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    settings = uvicorn.Config(app, log_config=None, access_log=False, ws="none")
    server = uvicorn.Server(settings)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async def wait_for_listener():
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Fixture did not start")
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_for_listener(), 5)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


class MCPConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.env = patch.dict(os.environ, {"MACAGENT_APP_SUPPORT_DIR": directory.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_stdio_import_and_tool_name_collisions(self):
        script = self.directory / "fixture.py"
        script.write_text(
            "import os\nfrom mcp.server.fastmcp import FastMCP\n"
            "app = FastMCP('echo')\n@app.tool()\n"
            "def echo(value: str) -> str:\n    return os.environ['LABEL'] + value\n"
            "app.run()\n", encoding="utf-8")
        entries = {
            name: {"command": sys.executable, "args": ["fixture.py"], "cwd": str(self.directory),
                   "env": {"LABEL": "${MCP_TEST_LABEL:-" + name + "}"}}
            for name in ("first", "second")
        }
        entries["absent"] = {"command": "/nonexistent-binary"}
        entries["invalid"] = {"command": "never-spawn", "args": 42}
        entries["disabled"] = {"disabled": True, "command": "never-spawn"}
        p = self.directory / "mcp.json"
        p.write_text(json.dumps({"mcpServers": entries}))
        hub = MCPHub(self.directory / "empty.json")
        hub.user_config = p
        hub.load_config()
        self.addAsyncCleanup(hub.cleanup)
        await hub.connect_all()
        self.assertEqual(set(hub.sessions), {"first", "second"})
        self.assertEqual({item["name"] for item in hub.skipped_servers}, {"absent", "invalid"})
        self.assertEqual(hub.get_all_tools_for_llm(), [])
        self.assertIsNone(await hub.chat_command("describe /mcp first echo"))
        self.assertIn('"value"', await hub.chat_command("/mcp first echo"))
        self.assertIn("objet JSON", await hub.chat_command("/mcp first echo []"))
        for name in ("first", "second"):
            actual = await hub.chat_command('/mcp ' + name + ' echo {"value":" OK"}')
            self.assertEqual(actual, name + " OK")
        self.assertIn("Erreur MCP", await hub.chat_command("/mcp first echo {}"))
        await hub.cleanup()
        self.assertFalse(hub.sessions)
        self.assertFalse(hub.tools)

    async def test_remote_transports_with_headers(self):
        for mode in ("http", "sse"):
            with self.subTest(mode=mode):
                fixture = FastMCP("fixture", stateless_http=True, json_response=True)

                @fixture.tool()
                def twice(number: int) -> str:
                    return str(number * 2)

                @fixture.resource("demo://note")
                def note() -> str:
                    return "Une note."

                @fixture.resource("demo://room/{room}")
                def room_card(room: str) -> str:
                    return f"Pièce {room}"

                @fixture.prompt()
                def welcome(name: str) -> str:
                    return f"Bonjour {name}"

                endpoint = fixture.sse_app() if mode == "sse" else fixture.streamable_http_app()

                async def authenticated(scope, receive, send):
                    if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != b"Bearer demo":
                        await send({"type": "http.response.start", "status": 401, "headers": []})
                        await send({"type": "http.response.body", "body": b""})
                        return
                    await endpoint(scope, receive, send)

                async with serve(authenticated) as base:
                    path = self.directory / "config.json"
                    suffix = "/sse" if mode == "sse" else "/mcp"
                    path.write_text(json.dumps({"mcpServers": {"demo": {
                        "type": mode, "url": base + suffix,
                        "headers": {"Authorization": "Bearer ${MCP_TEST_AUTH:-demo}"},
                    }}}))
                    hub = MCPHub(path)
                    hub.load_config()
                    try:
                        await hub.connect_all()
                        self.assertEqual(hub.skipped_servers, [])
                        self.assertIn("twice", await hub.chat_command("/mcp"))
                        self.assertEqual(await hub.chat_command('/mcp demo twice {"number":21}'), "42")
                        self.assertIn("demo://note", await hub.chat_command("/mcp demo @resources"))
                        self.assertEqual(await hub.chat_command("/mcp demo @resources demo://note"), "Une note.")
                        self.assertIn("demo://room/{room}", await hub.chat_command("/mcp demo @templates"))
                        self.assertIn("welcome (name)", await hub.chat_command("/mcp demo @prompts"))
                        self.assertIn("Bonjour Léa", await hub.chat_command('/mcp demo @prompts welcome {"name":"Léa"}'))
                    finally:
                        await hub.cleanup()

    async def test_slow_server_shutdown(self):
        path = self.directory / "slow.json"
        path.write_text(json.dumps({"mcpServers": {
            "slow": {"command": sys.executable, "args": ["-c", "import time; time.sleep(5)"]}
        }}))
        hub = MCPHub(path)
        hub._CONNECT_TIMEOUT = 0.15
        hub.load_config()
        await hub.connect_all()
        await asyncio.wait_for(hub.cleanup(), 4)
        self.assertEqual([item["name"] for item in hub.skipped_servers], ["slow"])
        self.assertFalse(hub.sessions)
