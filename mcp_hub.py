import asyncio
import json
import logging
import os
import shutil
import sys
from typing import Any, Dict, List, Optional
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MacAgent-MCP")
from paths import get_project_root, get_app_support_dir
PROJECT_ROOT = get_project_root()


# ---------------------------------------------------------------------------
# Runtime path substitution
# ---------------------------------------------------------------------------
# These tokens can appear inside mcp_servers.json args/command values and are
# expanded to their real paths at load time, so the file never needs hardcoded
# user-specific paths.
#
# Supported tokens:
#   ${PROJECT_ROOT}   → resolved project root (dev: repo dir; prod: sys._MEIPASS)
#   ${MCP_DYNAMIC}    → ~/Library/Application Support/MacAgentOS/mcp_dynamic
#   ${VENV_PYTHON}    → current Python if >=3.10, else a local compatible venv,
#                       else shutil.which("python3")
#   ${HOME}           → current user home directory

def _python_version(path: Path) -> Optional[tuple[int, int]]:
    version_info = path.parent.parent / "pyvenv.cfg"
    if not version_info.exists():
        return None
    try:
        for line in version_info.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("version"):
                raw = line.split("=", 1)[1].strip()
                parts = raw.split(".")
                return (int(parts[0]), int(parts[1]))
    except Exception:
        return None
    return None


def _is_compatible_python(path: Path) -> bool:
    version = _python_version(path)
    return version is None or version >= (3, 10)


def _resolve_mcp_python() -> str:
    current = Path(sys.executable)
    if _is_compatible_python(current):
        return str(current)

    for relative in ((".venv312", "bin", "python"), (".venv", "bin", "python")):
        candidate = PROJECT_ROOT.joinpath(*relative)
        if candidate.exists() and _is_compatible_python(candidate):
            return str(candidate)

    return shutil.which("python3") or "python3"


def _make_token_map() -> dict:
    return {
        "${PROJECT_ROOT}": str(PROJECT_ROOT),
        "${MCP_DYNAMIC}": str(get_app_support_dir() / "mcp_dynamic"),
        "${VENV_PYTHON}": _resolve_mcp_python(),
        "${HOME}": str(Path.home()),
    }

def _expand_tokens(value: str, token_map: dict) -> str:
    for token, replacement in token_map.items():
        value = value.replace(token, replacement)
    return value

def _resolve_arg(value: str, token_map: dict) -> str:
    """Expand tokens AND rewrite legacy absolute paths to token equivalents."""
    expanded = _expand_tokens(value, token_map)
    return expanded

def _resolve_server_config(name: str, cfg: dict, token_map: dict) -> Optional[StdioServerParameters]:
    """
    Parse one server config entry, expanding tokens and rewriting absolute paths.
    Returns None if the resulting command is not executable (safety check).
    """
    command = _resolve_arg(cfg.get("command", ""), token_map)
    args = [_resolve_arg(str(a), token_map) for a in cfg.get("args", [])]
    env = cfg.get("env") or None

    # Safety: if command is missing, skip it before stdio_client tries to spawn it.
    # This keeps optional MCP dependencies (npx/uvx/etc.) from looking like backend
    # startup failures.
    if "/" not in command and shutil.which(command) is None:
        logger.warning(
            "MCP server '%s': command '%s' not found on PATH — skipped",
            name, command,
        )
        return None

    # If an absolute command no longer exists (e.g. old hardcoded venv path on
    # another machine), try the current compatible Python as a last resort.
    if command.startswith("/") and not Path(command).exists():
        fallback = _resolve_mcp_python()
        if fallback:
            logger.warning(
                "MCP server '%s': command '%s' not found, falling back to %s",
                name, command, fallback,
            )
            command = fallback
        else:
            logger.warning(
                "MCP server '%s': command '%s' not found and python3 not on PATH — skipped",
                name, command,
            )
            return None

    # Safety: if first arg is a Python script path, check it exists.
    if args and args[0].endswith(".py") and not Path(args[0]).exists():
        logger.warning(
            "MCP server '%s': script '%s' not found — skipped",
            name, args[0],
        )
        return None

    return StdioServerParameters(command=command, args=args, env=env)


class MCPHub:
    """
    Le Hub central qui se connecte à tous les serveurs MCP configurés
    et agrège leurs outils.
    """
    def __init__(self, config_path: str = "mcp_servers.json"):
        self.config_path = (
            str(PROJECT_ROOT / config_path)
            if config_path == "mcp_servers.json"
            else config_path
        )
        self.servers: Dict[str, StdioServerParameters] = {}
        self.sessions: Dict[str, ClientSession] = {}
        self.available_tools: Dict[str, dict] = {} # { "tool_name": {"server": "server_name", "info": tool_info} }
        self.skipped_servers: List[dict] = []
        self._exit_stack = None

    def _mark_skipped(self, server_name: str, reason: str):
        if any(item.get("name") == server_name for item in self.skipped_servers):
            return
        self.skipped_servers.append({"name": server_name, "reason": reason})

    def load_config(self):
        """Charge la configuration des serveurs MCP depuis le fichier JSON.

        Paths in the JSON can use tokens like ${PROJECT_ROOT}, ${VENV_PYTHON},
        ${MCP_DYNAMIC}, and ${HOME} — they are expanded at load time so the file
        never needs hardcoded user-specific absolute paths.

        Legacy absolute paths from older configs are accepted as-is; if the
        command/script no longer exists on this machine the server is silently
        skipped instead of crashing.
        """
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)

            token_map = _make_token_map()
            # Support both key names used historically
            servers_raw = config.get("mcpServers") or config.get("mcp_servers") or {}
            loaded = 0
            skipped = 0
            for server_name, server_config in servers_raw.items():
                params = _resolve_server_config(server_name, server_config, token_map)
                if params is not None:
                    self.servers[server_name] = params
                    loaded += 1
                else:
                    self._mark_skipped(
                        server_name,
                        "Dépendance ou script optionnel absent; serveur MCP ignoré.",
                    )
                    skipped += 1
            logger.info(
                "MCP config loaded: %d servers active, %d skipped (missing files/commands).",
                loaded, skipped,
            )
        except FileNotFoundError:
            logger.warning(
                "MCP config '%s' not found — creating default config.", self.config_path
            )
            self._create_default_config()
        except Exception as e:
            logger.error("Failed to load MCP config '%s': %s — MCP disabled.", self.config_path, e)

    def _create_default_config(self):
        """Crée un fichier de configuration par défaut avec quelques serveurs utiles."""
        default_config = {
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", str(PROJECT_ROOT)]
                },
                "fetch": {
                    "command": "uvx",
                    "args": ["mcp-server-fetch"]
                }
            }
        }
        with open(self.config_path, 'w') as f:
            json.dump(default_config, f, indent=4)
        self.load_config()

    # Per-server connection timeout (seconds). Prevents one slow/absent server
    # from blocking the others during startup.
    _CONNECT_TIMEOUT = 18.0 if getattr(sys, "frozen", False) else 6.0

    async def _connect_one(self, server_name: str, server_params: StdioServerParameters):
        """Connect to a single MCP server, with its own exit stack and timeout."""
        from contextlib import AsyncExitStack
        stack = AsyncExitStack()
        try:
            read, write = await asyncio.wait_for(
                stack.enter_async_context(stdio_client(server_params)),
                timeout=self._CONNECT_TIMEOUT,
            )
            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read, write)),
                timeout=self._CONNECT_TIMEOUT,
            )
            await asyncio.wait_for(session.initialize(), timeout=self._CONNECT_TIMEOUT)
            self.sessions[server_name] = session
            self._server_stacks[server_name] = stack
            logger.info(f"✅ Connecté à {server_name}")
            await self._discover_tools(server_name, session)
        except asyncio.TimeoutError:
            logger.warning(f"⏱ Timeout connexion à {server_name} (>{self._CONNECT_TIMEOUT}s) — ignoré")
            self._mark_skipped(server_name, "Connexion trop lente; serveur MCP ignoré.")
            await stack.aclose()
        except Exception as e:
            logger.error(f"❌ Échec de la connexion à {server_name} : {e}")
            self._mark_skipped(server_name, "Connexion impossible; serveur MCP ignoré.")
            await stack.aclose()

    async def connect_all(self):
        """Se connecte à tous les serveurs MCP configurés en parallèle."""
        self._server_stacks: Dict[str, Any] = {}
        tasks = [
            self._connect_one(name, params)
            for name, params in self.servers.items()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _discover_tools(self, server_name: str, session: ClientSession):
        """Demande la liste des outils au serveur et les stocke."""
        try:
            response = await session.list_tools()
            tools = response.tools
            
            for tool in tools:
                # On stocke l'outil avec une référence au serveur qui le possède
                self.available_tools[tool.name] = {
                    "server": server_name,
                    "info": tool
                }
            logger.info(f"  -> {len(tools)} outils découverts sur {server_name}")
        except Exception as e:
            logger.error(f"Erreur lors de la découverte des outils sur {server_name} : {e}")

    def get_all_tools_for_llm(self) -> List[dict]:
        """Formate tous les outils découverts pour les envoyer au LLM."""
        formatted_tools = []
        for tool_name, data in self.available_tools.items():
            tool = data["info"]
            formatted_tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema
            })
        return formatted_tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """
        Route l'appel d'outil vers le bon serveur MCP.
        Le garde-fou (HITL) a été désactivé à la demande de l'utilisateur.
        """
        if tool_name not in self.available_tools:
            return f"Erreur : L'outil '{tool_name}' n'existe pas."
            
        server_name = self.available_tools[tool_name]["server"]
        session = self.sessions.get(server_name)
        
        if not session:
            return f"Erreur : La session avec le serveur {server_name} est inactive."
            
        # --- GARDE-FOU DÉSACTIVÉ ---
        logger.warning(f"⚠️ EXÉCUTION AUTOMATIQUE (SANS GARDE-FOU) : {tool_name} sur {server_name}")
        logger.info(f"Arguments : {json.dumps(arguments)}")
        # ------------------------------------
            
        try:
            result = await session.call_tool(tool_name, arguments)
            
            # Formater le résultat (MCP renvoie une liste de contenus)
            if result.content:
                # On prend le premier contenu texte pour simplifier
                for content in result.content:
                    if content.type == "text":
                        return content.text
            return "Outil exécuté avec succès (pas de retour texte)."
            
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution de {tool_name} : {e}")
            return f"Erreur d'exécution : {str(e)}"

    async def cleanup(self):
        """Ferme proprement toutes les connexions."""
        stacks = getattr(self, "_server_stacks", {})
        if stacks:
            await asyncio.gather(
                *(stack.aclose() for stack in stacks.values()),
                return_exceptions=True,
            )
            logger.info("Toutes les connexions MCP ont été fermées.")
        elif self._exit_stack:
            await self._exit_stack.aclose()
            logger.info("Toutes les connexions MCP ont été fermées.")

# --- TEST DU HUB MCP ---
async def main():
    print("🚀 Démarrage du Hub MCP MacAgent...")
    hub = MCPHub()
    
    # 1. Charger la config
    hub.load_config()
    
    # 2. Se connecter aux serveurs
    await hub.connect_all()
    
    # 3. Afficher les outils disponibles
    print("\n🛠️ Outils disponibles pour le LLM :")
    tools = hub.get_all_tools_for_llm()
    for t in tools:
        print(f" - {t['name']} : {t['description'][:50]}...")
        
    # 4. Test d'appel d'outil (si le serveur filesystem est connecté)
    if "list_allowed_directories" in hub.available_tools:
        print("\n🧪 Test d'exécution d'un outil MCP...")
        result = await hub.call_tool("list_allowed_directories", {})
        print(f"Résultat : {result}")
        
    await hub.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
