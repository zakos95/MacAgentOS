"""MCP tool discovery and sessions for the native and web clients."""
import asyncio
import json
import logging
import os
import re
import shutil
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from paths import get_app_support_dir, get_project_root

PROJECT_ROOT = get_project_root()
logger = logging.getLogger("MacAgent-MCP")
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z_0-9]*)(?::-([^}]*))?\}")
# Only these bundled tools are callable from the legacy autonomous core.
# Imported servers and other tools are accessible through explicit user requests.
_SYSTEM_READS = {"get_system_info", "get_battery_status", "get_running_apps"}


def _is_compatible_python(path: Path) -> bool:
    if path == Path(sys.executable):
        return sys.version_info >= (3, 10)
    return path.is_file() and os.access(path, os.X_OK)


def _resolve_mcp_python() -> str:
    if _is_compatible_python(Path(sys.executable)):
        return sys.executable
    return shutil.which("python3") or "python3"


def _make_token_map() -> dict:
    return {
        "${PROJECT_ROOT}": str(PROJECT_ROOT),
        "${MCP_DYNAMIC}": str(get_app_support_dir() / "mcp_dynamic"),
        "${VENV_PYTHON}": _resolve_mcp_python(),
        "${HOME}": str(Path.home()),
    }


def _expand_tokens(value: str, token_map: dict, remote=False) -> str:
    if not isinstance(value, str):
        raise ValueError("Valeur de configuration textuelle attendue.")

    def replace(match):
        key, default = match.groups()
        if remote and key in {"MACAGENT_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                              "ANTHROPIC_AUTH_TOKEN", "AWS_SECRET_ACCESS_KEY",
                              "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "NPM_TOKEN",
                              "HTTPS_PROXY", "HTTP_PROXY"}:
            raise ValueError("Utilisez une variable dédiée à ce connecteur.")
        if match.group(0) in token_map:
            return token_map[match.group(0)]
        if key in os.environ:
            return os.environ[key]
        if default is not None:
            return default
        raise ValueError(f"Variable d’environnement manquante ({key}).")

    return _VARIABLE.sub(replace, value)


@dataclass(frozen=True)
class RemoteServer:
    transport: str
    url: str
    headers: dict[str, str]


def _resolve_server_config(name: str, cfg: dict, token_map: dict):
    if not isinstance(cfg, dict):
        raise ValueError("Configuration de serveur invalide.")
    if cfg.get("disabled"):
        return None
    transport = cfg.get("type", "stdio")
    if transport in ("http", "streamable-http", "sse"):
        if cfg.get("headersHelper") or cfg.get("oauth"):
            raise ValueError("Ce connecteur nécessite une authentification non prise en charge.")
        url = _expand_tokens(cfg.get("url", ""), token_map, remote=True)
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL HTTP(S) requise. Utilisez headers pour les identifiants.")
        headers = cfg.get("headers", {})
        if not isinstance(headers, dict):
            raise ValueError("Objet headers attendu.")
        headers = {k: _expand_tokens(v, token_map, remote=True) for k, v in headers.items()}
        return RemoteServer(transport, url, headers)
    if transport != "stdio":
        raise ValueError("Transport non pris en charge. Utilisez stdio, http ou sse.")
    if "command" not in cfg or "url" in cfg:
        raise ValueError("Commande stdio ou type de transport distant requis.")
    command = _expand_tokens(cfg["command"], token_map)
    env = cfg.get("env", {})
    args = cfg.get("args", [])
    if not isinstance(env, dict) or not isinstance(args, list):
        raise ValueError("Les champs args et env doivent être un tableau et un objet.")
    env = {k: _expand_tokens(v, token_map) for k, v in env.items()}
    args = [_expand_tokens(v, token_map) for v in args]
    cwd = Path(_expand_tokens(cfg["cwd"], token_map)).expanduser() if cfg.get("cwd") else None
    if cwd is not None and not cwd.is_dir():
        raise ValueError("Répertoire de travail introuvable.")
    if "/" in command:
        path = Path(command).expanduser()
        command = str(path if path.is_absolute() else (cwd or Path.cwd()) / path)
    if sys.platform == "darwin" and "PATH" not in env:
        # Finder-launched GUI apps typically lack the package manager paths.
        paths = [*os.get_exec_path(), "/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin")]
        env["PATH"] = os.pathsep.join(dict.fromkeys(paths))
    executable = shutil.which(command, path=env.get("PATH"))
    if not executable:
        return None
    return StdioServerParameters(command=executable, args=args, env=env, cwd=cwd)


class MCPHub:
    _CONNECT_TIMEOUT = 18.0 if getattr(sys, "frozen", False) else 6.0

    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else PROJECT_ROOT / "mcp_servers.json"
        self.user_config = None if config_path else get_app_support_dir() / "mcp.json"
        self.servers = {}
        self.sessions = {}
        self.tools = {}
        self.capabilities = {}
        self.skipped_servers = []
        self._workers = []
        self._stop = None
        self._trusted_system = False

    def _mark_skipped(self, name, reason):
        if not any(row["name"] == name for row in self.skipped_servers):
            self.skipped_servers.append({"name": name, "reason": reason})

    def load_config(self):
        """Read the packaged defaults and a user-selected configuration snapshot."""
        self.servers.clear()
        self.skipped_servers.clear()
        self._trusted_system = False
        for path in [self.config_path, self.user_config]:
            if path is None or not path.exists():
                continue
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
                entries = config.get("mcpServers", config.get("mcp_servers", {}))
                if not isinstance(entries, dict):
                    raise ValueError
            except (AttributeError, OSError, ValueError):
                self._mark_skipped(path.name, "Fichier de configuration MCP illisible.")
                continue
            tokens = _make_token_map()
            for name, cfg in entries.items():
                if name in self.servers:
                    self._mark_skipped(name, "Ce nom est déjà utilisé. Renommez le serveur importé.")
                    continue
                if not name or any(c.isspace() for c in name):
                    self._mark_skipped(name or "?", "Le nom du serveur doit être un seul mot.")
                    continue
                if isinstance(cfg, dict) and cfg.get("disabled"):
                    continue
                try:
                    params = _resolve_server_config(name, cfg, tokens)
                except (ValueError, TypeError, OSError) as exc:
                    # Only display our configuration validation messages. Never log credentials.
                    reason = str(exc) if type(exc) is ValueError else "Configuration invalide."
                    self._mark_skipped(name, reason)
                    continue
                if params is None:
                    self._mark_skipped(name, "Commande introuvable. Vérifiez son chemin absolu.")
                    continue
                self.servers[name] = params
                if path == PROJECT_ROOT / "mcp_servers.json" and name == "mac-control":
                    self._trusted_system = True

    async def _run_server(self, name, params, ready):
        # The SDK's AnyIO task groups must be entered and exited by the same task.
        try:
            async with AsyncExitStack() as stack:
                if isinstance(params, StdioServerParameters):
                    transport = stdio_client(params)
                elif params.transport == "sse":
                    transport = sse_client(params.url, headers=params.headers)
                else:
                    client = await stack.enter_async_context(httpx.AsyncClient(
                        headers=params.headers, timeout=httpx.Timeout(30.0, read=300.0)
                    ))
                    transport = streamable_http_client(params.url, http_client=client)
                streams = await stack.enter_async_context(transport)
                session = await stack.enter_async_context(ClientSession(*streams[:2]))
                init = await session.initialize()
                discovered = {}
                if init.capabilities.tools is not None:
                    discovered = {tool.name: tool for tool in await self._list_pages(session.list_tools, "tools")}
                self.capabilities[name] = init.capabilities
                self.sessions[name] = session
                self.tools[name] = discovered
                ready.set()
                await self._stop.wait()
        except Exception as exc:
            logger.warning("MCP %s: %s", name, type(exc).__name__)
            self._mark_skipped(name, "Connexion interrompue. Vérifiez la commande, l’URL et l’authentification.")
        finally:
            self.capabilities.pop(name, None)
            self.tools.pop(name, None)
            self.sessions.pop(name, None)
            ready.set()

    async def _start_one(self, name, params):
        ready = asyncio.Event()
        worker = asyncio.create_task(self._run_server(name, params, ready))
        self._workers.append(worker)
        try:
            await asyncio.wait_for(ready.wait(), self._CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            self._mark_skipped(name, "Délai de connexion MCP dépassé.")
            worker.cancel()
        except asyncio.CancelledError:
            worker.cancel()
            raise

    async def connect_all(self):
        if self._workers:
            await self.cleanup()
        self._stop = asyncio.Event()
        await asyncio.gather(*(self._start_one(name, p) for name, p in self.servers.items()))

    async def cleanup(self):
        if self._stop:
            self._stop.set()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()

    def get_all_tools_for_llm(self):
        if not self._trusted_system:
            return []
        return [
            {"name": name, "description": tool.description or "", "inputSchema": tool.inputSchema}
            for name, tool in self.tools.get("mac-control", {}).items() if name in _SYSTEM_READS
        ]

    async def call_tool(self, tool_name: str, arguments: dict):
        if not self._trusted_system or tool_name not in _SYSTEM_READS:
            return "Cet outil nécessite une commande /mcp explicite dans le chat."
        return await self._invoke("mac-control", tool_name, arguments)

    async def _invoke(self, server_name, tool_name, arguments):
        if tool_name not in self.tools.get(server_name, {}):
            return "Outil introuvable. Envoyez /mcp pour afficher les outils connectés."
        try:
            result = await self.sessions[server_name].call_tool(
                tool_name, arguments, read_timeout_seconds=timedelta(seconds=45)
            )
        except Exception as exc:
            logger.warning("MCP call %s/%s: %s", server_name, tool_name, type(exc).__name__)
            return "Échec de l’appel MCP. Vérifiez le connecteur dans Diagnostics."
        parts = []
        for item in result.content:
            if item.type == "text":
                parts.append(item.text)
            elif item.type in ("image", "audio"):
                parts.append(f"[{item.type} · {item.mimeType}]")
            else:
                # Resource links and embedded text resources retain their usable metadata.
                resource = item.model_dump(mode="json", by_alias=True, exclude_none=True)
                if isinstance(resource.get("resource"), dict):
                    resource["resource"].pop("blob", None)
                parts.append(json.dumps(resource, ensure_ascii=False))
        if result.structuredContent is not None and not parts:
            parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
        reply = "\n".join(parts) or "Outil exécuté (résultat vide)."
        return ("Erreur MCP : " if result.isError else "") + reply

    @staticmethod
    async def _list_pages(method, field):
        rows, seen = [], set()
        cursor = None
        while True:
            page = await method(cursor=cursor)
            rows.extend(getattr(page, field))
            cursor = page.nextCursor
            if not cursor:
                return rows
            if cursor in seen:
                raise ValueError("Repeated MCP pagination cursor")
            seen.add(cursor)

    async def _catalog_lookup(self, server_name, kind, value):
        session = self.sessions.get(server_name)
        capabilities = self.capabilities.get(server_name)
        if session is None:
            return "Serveur MCP déconnecté."
        if kind in ("@resources", "@templates") and capabilities.resources is None:
            return "Ce serveur ne publie aucune ressource MCP."
        if kind == "@prompts" and capabilities.prompts is None:
            return "Ce serveur ne publie aucun prompt MCP."
        if kind == "@templates":
            entries = await self._list_pages(session.list_resource_templates, "resourceTemplates")
            return "\n".join(str(item.uriTemplate) for item in entries) or "Aucun modèle de ressource."
        if kind == "@resources":
            if not value:
                entries = await self._list_pages(session.list_resources, "resources")
                return "\n".join(str(item.uri) for item in entries) or "Aucune ressource."
            reply = await session.read_resource(AnyUrl(value))
            return "\n".join(getattr(item, "text", None) or f"[{item.uri} · {item.mimeType}]"
                             for item in reply.contents) or "Ressource vide."
        if not value:
            entries = await self._list_pages(session.list_prompts, "prompts")
            return "\n".join(f"{item.name} ({', '.join(a.name for a in item.arguments or [])})"
                             for item in entries) or "Aucun prompt."
        fields = value.split(None, 1)
        arguments = json.loads(fields[1]) if len(fields) > 1 else {}
        if not isinstance(arguments, dict) or not all(isinstance(v, str) for v in arguments.values()):
            return "Un prompt attend un objet JSON à valeurs textuelles."
        prompt = await session.get_prompt(fields[0], arguments)
        return "\n".join(f"{m.role} — {getattr(m.content, 'text', None) or '[' + m.content.type + ']'}"
                         for m in prompt.messages) or "Prompt vide."

    async def chat_command(self, message: str) -> Optional[str]:
        """Dispatch only a slash command from the current user's message."""
        parts = message.strip().split(None, 3)
        if not parts or parts[0] != "/mcp":
            return None
        if len(parts) <= 2:
            names = sorted(self.sessions) if len(parts) == 1 else [parts[1]]
            entries = [f"{name} — {', '.join(sorted(self.tools[name])) or 'aucun outil'}"
                       for name in names if name in self.tools]
            return "\n".join(entries or ["Aucun serveur correspondant connecté. Consultez Diagnostics → MCP."]) + (
                "\n\nSchéma : /mcp SERVEUR OUTIL\nExécuter : /mcp SERVEUR OUTIL {…}"
                "\nCatalogues : /mcp SERVEUR @resources, @templates ou @prompts"
            )
        _, server_name, tool_name, *raw_args = parts
        if tool_name in {"@resources", "@templates", "@prompts"}:
            try:
                return await asyncio.wait_for(
                    self._catalog_lookup(server_name, tool_name, raw_args[0] if raw_args else ""), 45
                )
            except (ValueError, TypeError):
                return "Paramètres invalides. Vérifiez l’URI ou les arguments JSON."
            except Exception as exc:
                logger.warning("MCP catalog %s: %s", server_name, type(exc).__name__)
                return "Le serveur MCP n’a pas pu fournir ce résultat."
        tool = self.tools.get(server_name, {}).get(tool_name)
        if tool is None:
            return "Outil introuvable. Envoyez /mcp pour afficher les outils connectés."
        if not raw_args:
            return f"{tool.description or tool.name}\n{json.dumps(tool.inputSchema, ensure_ascii=False, indent=2)}"
        try:
            arguments = json.loads(raw_args[0])
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            return "Arguments invalides. Fournissez un objet JSON, ou {} pour un appel sans argument."
        return await self._invoke(server_name, tool_name, arguments)
