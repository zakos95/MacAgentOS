"""
MacAgent-OS Core - The main application engine
Inspired by OpenWork but for macOS with native MCP control
"""
import asyncio
import json
import uuid
import hashlib
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime

from llm_connector import LLMConnector
from llm_universal import get_provider
from mcp_hub import MCPHub
from token_optimization import TokenOptimizer

logger = logging.getLogger("MacAgent-Core")


def _normalize_provider_model(provider: str, model: str) -> str:
    selected_provider = (provider or "").lower()
    selected_model = (model or "").strip()
    if selected_provider == "local_chatgpt_codex":
        if not selected_model or not selected_model.startswith("openai/"):
            return "openai/gpt-5.4"
    if selected_provider == "openai" and selected_model.startswith("openai/"):
        return selected_model.split("/", 1)[1]
    return selected_model


@dataclass
class Workspace:
    """Represents a workspace (project folder)"""
    id: str
    path: str
    name: str
    created_at: str
    last_opened: str
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "created_at": self.created_at,
            "last_opened": self.last_opened
        }


@dataclass
class ExecutionEvent:
    """An event in the execution timeline"""
    event_type: str  # "thought", "tool_call", "tool_result", "text", "permission"
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[Dict] = None
    tool_result: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict:
        return {
            "event_type": self.event_type,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "timestamp": self.timestamp
        }


class MacAgentCore:
    """The main MacAgent-OS core engine"""
    
    def __init__(
        self,
        provider: str = "ollama",
        model: str = "dolphin3:latest",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        from paths import get_data_dir
        self.data_dir = get_data_dir()
        self.data_dir.mkdir(exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        persisted = self._load_persisted_settings()

        resolved_provider = persisted.get("provider") or provider
        resolved_model = _normalize_provider_model(resolved_provider, persisted.get("model") or model)
        resolved_api_key = api_key if api_key is not None else persisted.get("api_key")
        resolved_base_url = base_url if base_url is not None else persisted.get("base_url")

        # LLM
        self.llm = LLMConnector(
            provider=resolved_provider,
            model=resolved_model,
            api_key=resolved_api_key,
            base_url=resolved_base_url
        )
        
        # MCP Hub
        self.hub = MCPHub()
        
        # State
        self.workspaces: Dict[str, Workspace] = {}
        self.current_workspace_id: Optional[str] = None
        self.last_usage_telemetry: Dict[str, Any] = {}
        self.token_optimizer = TokenOptimizer(self.data_dir)
        
        # System prompt
        self.system_prompt = self._build_system_prompt()
        
        logger.info("MacAgent-OS Core initialized")

    def _load_persisted_settings(self) -> Dict[str, Any]:
        """Load persisted LLM settings if they exist."""
        if not self.settings_path.exists():
            return {}

        try:
            return json.loads(self.settings_path.read_text())
        except Exception as exc:
            logger.warning(f"Could not load settings from {self.settings_path}: {exc}")
            return {}

    def _persist_settings(self):
        """Persist the currently active LLM settings for the next launch."""
        # Load existing file first so we don't wipe planner_* / chat_* keys
        # that may have been written independently.
        existing: Dict[str, Any] = {}
        if self.settings_path.exists():
            try:
                existing = json.loads(self.settings_path.read_text())
            except Exception:
                pass

        existing.update({
            "provider": self.llm.provider,
            "model": self.llm.model,
            "api_key": self.llm.api_key or "",
            "base_url": self.llm.base_url or "",
        })

        try:
            self.settings_path.write_text(json.dumps(existing, indent=2))
        except Exception as exc:
            logger.warning(f"Could not persist settings to {self.settings_path}: {exc}")
    
    def _build_system_prompt(self) -> str:
        return """Tu es MacAgent-OS, un assistant IA avancé pour macOS.

CAPACITÉS :
- Contrôle complet du Mac via MCP (fichiers, apps, système)
- Utilisation d'outils pour exécuter des actions
- Gestion de plusieurs workspaces
- Rappel des instructions précédentes

RÈGLES :
1. Quand l'utilisateur demande une action, UTILISE un outil
2. Sois concis et direct
3. Utilise les chemins absolus (/Users/...)
4. Explique ce que tu fais avant de le faire pour les actions sensibles
5. Demande confirmation pour les actions irréversibles

OUTILS DISPONIBLES :
- list_directory, read_file, write_file, create_directory
- execute_bash, execute_applescript
- open_application, kill_app, get_running_apps
- get_system_info, get_battery_status, volume_control
- Take screenshots, and more...

Tu peux aussi utiliser les SKILLS dans .opencode/skills/ pour des workflows réutilisables."""
    
    async def start(self):
        """Start the core"""
        logger.info("Starting MacAgent-OS Core...")
        self.hub.load_config()
        try:
            startup_timeout = 24 if getattr(sys, "frozen", False) else 8
            await asyncio.wait_for(self.hub.connect_all(), timeout=startup_timeout)
        except asyncio.TimeoutError:
            logger.warning("MCP startup timed out; continuing in degraded mode")
        except Exception as exc:
            logger.warning(f"MCP startup failed; continuing in degraded mode: {exc}")
        logger.info("MacAgent-OS Core ready")
    
    async def stop(self):
        """Stop the core"""
        await self.hub.cleanup()
        logger.info("MacAgent-OS Core stopped")
    
    # === WORKSPACES ===
    
    def create_workspace(self, path: str, name: Optional[str] = None) -> Workspace:
        """Create a new workspace"""
        workspace_id = str(uuid.uuid4())[:8]
        workspace_path = Path(path).resolve()
        
        workspace = Workspace(
            id=workspace_id,
            path=str(workspace_path),
            name=name or workspace_path.name,
            created_at=datetime.now().isoformat(),
            last_opened=datetime.now().isoformat()
        )
        
        self.workspaces[workspace_id] = workspace
        self.current_workspace_id = workspace_id
        
        logger.info(f"Created workspace: {workspace.name} ({workspace_id})")
        return workspace
    
    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Get a workspace by ID"""
        return self.workspaces.get(workspace_id)
    
    def list_workspaces(self) -> List[Workspace]:
        """List all workspaces"""
        return list(self.workspaces.values())
    
    def set_current_workspace(self, workspace_id: str) -> bool:
        """Set the current workspace"""
        if workspace_id in self.workspaces:
            self.current_workspace_id = workspace_id
            self.workspaces[workspace_id].last_opened = datetime.now().isoformat()
            return True
        return False
    
    # === MCP TOOLS ===
    
    def get_tools_for_llm(self) -> Dict:
        """Get tools formatted for LLM"""
        mcp_tools = self.hub.get_all_tools_for_llm()
        formatted = {}
        
        for tool in mcp_tools:
            formatted[tool["name"]] = {
                "description": tool["description"],
                "parameters": tool.get("inputSchema", {}).get("properties", {})
            }
        
        return formatted
    
    async def call_tool(self, tool_name: str, arguments: Dict) -> Any:
        """Call an MCP tool"""
        return await self.hub.call_tool(tool_name, arguments)
    
    # === EXECUTION ===
    
    async def execute(
        self,
        user_message: str,
        session_history: Optional[List[Dict]] = None,
        max_iterations: int = 10,
        allow_auto_routing: bool = False,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        turbo: bool = False,
    ) -> List[ExecutionEvent]:
        """Execute a user message and return events"""
        events = []
        history_items = []
        for msg in session_history or []:
            if hasattr(msg, "to_dict"):
                msg = msg.to_dict()
            if isinstance(msg, dict):
                history_items.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })
            else:
                history_items.append({"role": "user", "content": str(msg)})

        project_path = ""
        if self.current_workspace_id and self.current_workspace_id in self.workspaces:
            project_path = self.workspaces[self.current_workspace_id].path

        context = self.token_optimizer.build_context(
            base_system_prompt=system_prompt or self.system_prompt,
            user_message=user_message,
            history=history_items,
            project_path=project_path,
            turbo=turbo,
        )
        requested_provider = (provider or self.llm.provider).lower()
        requested_model = _normalize_provider_model(requested_provider, model or self.llm.model)
        selected_llm = self.llm
        if (
            requested_provider != self.llm.provider
            or requested_model != self.llm.model
            or api_key is not None
            or base_url is not None
        ):
            selected_llm = LLMConnector(
                provider=requested_provider,
                model=requested_model,
                api_key=self.llm.api_key if api_key is None else api_key,
                base_url=self.llm.base_url if base_url is None else base_url,
            )
        actual_provider = requested_provider
        actual_model = requested_model
        route_tier = "locked"
        route_reason = "explicit model selection"
        fallback_reason = ""
        available_models: List[str] = []

        try:
            provider_for_models = get_provider(
                requested_provider,
                api_key=selected_llm.api_key or "",
                model=requested_model,
                base_url=selected_llm.base_url or "",
            )
            available_models = provider_for_models.get_models(api_key=selected_llm.api_key or "") or []
        except Exception as exc:
            logger.warning(
                "Core execute could not load models for routing validation provider=%s requested_model=%s error=%s",
                requested_provider,
                requested_model,
                exc,
            )

        if requested_model and available_models and requested_model not in available_models:
            route = self.token_optimizer.choose_model(
                provider=requested_provider,
                requested_model=requested_model,
                user_message=user_message,
                history=history_items,
            )
            if route.model and route.model in available_models:
                actual_model = route.model
            else:
                actual_model = available_models[0]
            route_tier = route.tier
            fallback_reason = "requested model unavailable"
            route_reason = f"{route.reason}; fallback: {fallback_reason}"
        elif allow_auto_routing:
            route = self.token_optimizer.choose_model(
                provider=requested_provider,
                requested_model=requested_model,
                user_message=user_message,
                history=history_items,
            )
            actual_model = route.model or requested_model
            route_tier = route.tier
            route_reason = route.reason
            if requested_model and actual_model != requested_model:
                fallback_reason = "automatic routing explicitly allowed"

        logger.info(
            "Core execute routing requested_provider=%s requested_model=%s actual_provider=%s actual_model=%s fallback_reason=%s",
            requested_provider,
            requested_model,
            actual_provider,
            actual_model,
            fallback_reason or "none",
        )
        current_message = context["user_message"]
        tools = self.get_tools_for_llm()
        
        for iteration in range(max_iterations):
            # Ask LLM
            started_at = time.perf_counter()
            response = selected_llm.ask(
                context["system_prompt"],
                current_message,
                tools,
                model_override=actual_model
            )
            
            if response["type"] == "error":
                events.append(ExecutionEvent(
                    event_type="error",
                    content=response["content"]
                ))
                break
            
            if response["type"] == "text":
                text_content = response["content"]
                self.token_optimizer.update_memory(
                    context["project_key"],
                    context["project_label"],
                    user_message,
                    text_content,
                )
                self.last_usage_telemetry = self.token_optimizer.telemetry.build(
                    actual_model,
                    context["messages"],
                    text_content,
                    started_at,
                )
                self.last_usage_telemetry.update({
                    "requested": {"provider": requested_provider, "model": requested_model},
                    "actual": {"provider": actual_provider, "model": actual_model},
                    "route": {
                        "tier": route_tier,
                        "reason": route_reason,
                        "fallback_reason": fallback_reason,
                    },
                })
                events.append(ExecutionEvent(
                    event_type="text",
                    content=text_content
                ))
                break
            
            if response["type"] == "tool_call":
                tool_name = response["name"]
                tool_args = response["arguments"]
                
                # Add tool call event
                events.append(ExecutionEvent(
                    event_type="tool_call",
                    content=f"Executing tool: {tool_name}",
                    tool_name=tool_name,
                    tool_args=tool_args
                ))
                
                # Execute tool
                try:
                    result = await self.call_tool(tool_name, tool_args)
                    events.append(ExecutionEvent(
                        event_type="tool_result",
                        content=f"Tool result: {str(result)[:200]}...",
                        tool_name=tool_name,
                        tool_result=str(result)
                    ))
                    current_message = f"Tool '{tool_name}' executed:\n{result}\n\nQue faire ensuite?"
                except Exception as e:
                    events.append(ExecutionEvent(
                        event_type="error",
                        content=f"Tool error: {str(e)}"
                    ))
        
        return events
    
    # === SETTINGS ===
    
    def update_llm_settings(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        """Update LLM settings"""
        resolved_provider = provider or self.llm.provider
        resolved_model = _normalize_provider_model(resolved_provider, model or self.llm.model)
        resolved_api_key = self.llm.api_key if api_key is None else api_key.strip()
        resolved_base_url = self.llm.base_url if base_url is None else (base_url.strip() or None)

        self.llm = LLMConnector(
            provider=resolved_provider,
            model=resolved_model,
            api_key=resolved_api_key,
            base_url=resolved_base_url
        )
        self._persist_settings()
        logger.info(f"Updated LLM: {resolved_provider}/{resolved_model}")
    
    def get_settings(self) -> Dict:
        """Get current settings, including optional chat/planner model overrides."""
        persisted = self._load_persisted_settings()
        base = {
            "provider": self.llm.provider,
            "model": self.llm.model,
            "base_url": self.llm.base_url or "",
            "api_key": self.llm.api_key or "",
            "workspaces": len(self.workspaces),
            "current_workspace": self.current_workspace_id,
            "last_usage": self.last_usage_telemetry,
        }
        # Surface optional split-model config (read-only here; written via /api/settings)
        for key in ("chat_provider", "chat_model", "planner_provider", "planner_model",
                    "planner_base_url", "chat_base_url"):
            if key in persisted:
                base[key] = persisted[key]
        return base
    
    # === STATE ===
    
    def get_state(self) -> Dict:
        """Get full state"""
        return {
            "ready": self.hub is not None,
            "workspaces": [w.to_dict() for w in self.workspaces.values()],
            "current_workspace": self.current_workspace_id,
            "settings": self.get_settings()
        }


# Singleton
_core: Optional[MacAgentCore] = None

def get_core() -> MacAgentCore:
    global _core
    if _core is None:
        _core = MacAgentCore()
    return _core


# === MAIN ===

async def main():
    """Test the core"""
    print("=" * 50)
    print("🚀 MacAgent-OS Core Test")
    print("=" * 50)
    
    core = get_core()
    await core.start()
    
    # Test workspace
    ws = core.create_workspace("$HOME/Desktop", "Desktop")
    print(f"Created workspace: {ws.name}")
    
    # Test tools
    tools = core.get_tools_for_llm()
    print(f"\nTools available: {len(tools)}")
    for tool in list(tools.keys())[:5]:
        print(f"  - {tool}")
    
    # Test execution
    print("\n💬 Testing 'list my desktop files':")
    events = await core.execute("Liste les fichiers sur mon Bureau")
    for event in events:
        print(f"  {event.event_type}: {event.content[:80]}...")
    
    await core.stop()
    print("\n✅ Done!")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
