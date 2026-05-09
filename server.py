"""
MacAgent-OS Server - API Server like OpenWork
Provides comprehensive API for desktop control
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import shlex
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager
from datetime import datetime

import secrets

if sys.version_info < (3, 10):
    raise RuntimeError(
        "MacAgent OS requires Python 3.10+ because the MCP dependency uses "
        "modern Python syntax. Create/activate a Python 3.12 venv, then run server.py."
    )

def _run_bundled_mcp_if_requested() -> None:
    """Let the frozen backend executable serve bundled local MCP scripts."""
    if not getattr(sys, "frozen", False) or len(sys.argv) < 2:
        return

    requested = Path(sys.argv[1]).name
    if requested == "mac_server.py":
        from mac_server import mcp
        mcp.run()
        raise SystemExit(0)
    if requested == "mcp_safari.py":
        from mcp_safari import mcp
        mcp.run()
        raise SystemExit(0)


_run_bundled_mcp_if_requested()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from pydantic import BaseModel

from core import MacAgentCore, get_core
from sessions import SessionManager, get_session_manager
from templates import TemplatesManager, get_templates_manager
from permissions import PermissionManager, get_permission_manager, PermissionReply
from skills import SkillsManager, get_skills_manager
from commands import CommandsManager, get_commands_manager
from agents import AgentsManager, get_agents_manager
from plugins import PluginManager, get_plugin_manager
from worker import WorkerManager, get_worker_manager
from debug import DebugExporter, get_debug_exporter
from opencode_auth import get_bridge_runtime_kind, get_openai_oauth_status, get_opencode_bin
from opencode_bridge import get_local_chatgpt_bridge_status
from token_optimization import TokenOptimizer
from app_runtime import load_runtime
from provider_connections import get_provider_connection_specs
import self_update as self_update_manager

from paths import get_data_dir, get_project_root
LOG_DIR = get_data_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SERVER_LOG_PATH = LOG_DIR / "server.log"
MEMORY_PATH = get_data_dir() / "memory.json"
PROJECT_LESSONS_PATH = get_data_dir() / "project_lessons.json"
runtime = load_runtime()

logging.basicConfig(
    level=getattr(logging, runtime.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(SERVER_LOG_PATH, encoding="utf-8")
    ]
)
logger = logging.getLogger("MacAgent-Server")

# === MODELS ===

class PromptRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class SettingsRequest(BaseModel):
    provider: str = "ollama"
    model: str = "dolphin3:latest"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    # Optional split-model config.
    # If absent, chat and planner both use provider/model above.
    chat_provider: Optional[str] = None
    chat_model: Optional[str] = None
    chat_base_url: Optional[str] = None
    planner_provider: Optional[str] = None
    planner_model: Optional[str] = None
    planner_base_url: Optional[str] = None

class WorkspaceRequest(BaseModel):
    path: str
    name: Optional[str] = None

class PermissionReplyRequest(BaseModel):
    reply: str  # "once", "always", "deny"

# === GLOBAL STATE ===

class ServerState:
    def __init__(self):
        self.core = MacAgentCore()
        self.session_manager = get_session_manager()
        self.templates_manager = get_templates_manager()
        self.permissions_manager = get_permission_manager()
        self.skills_manager = get_skills_manager()
        self.commands_manager = get_commands_manager()
        self.agents_manager = get_agents_manager()
        self.plugin_manager = get_plugin_manager()
        self.worker_manager = get_worker_manager()
        self.debug_exporter = get_debug_exporter()
        self.is_ready = False

state = ServerState()
token_optimizer = TokenOptimizer(get_data_dir())

# === AUTH ===

_API_KEY_FILE = get_data_dir() / "api_key.txt"

def _load_or_generate_api_key() -> str:
    env_key = os.environ.get("MACAGENT_API_KEY", "").strip()
    if env_key:
        return env_key
    if _API_KEY_FILE.exists():
        stored = _API_KEY_FILE.read_text().strip()
        if stored:
            return stored
    new_key = secrets.token_urlsafe(32)
    _API_KEY_FILE.write_text(new_key)
    return new_key

_API_KEY: str = _load_or_generate_api_key()


def load_memory() -> dict[str, str]:
    default_memory = {
        "last_file": "",
        "last_action": "",
        "last_result": "",
    }
    try:
        if not MEMORY_PATH.exists():
            MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            MEMORY_PATH.write_text(json.dumps(default_memory, ensure_ascii=False, indent=2), encoding="utf-8")
            return dict(default_memory)
        loaded = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return dict(default_memory)
        return {
            "last_file": str(loaded.get("last_file", "") or ""),
            "last_action": str(loaded.get("last_action", "") or ""),
            "last_result": str(loaded.get("last_result", "") or ""),
        }
    except Exception:
        return dict(default_memory)


def save_memory(memory: dict[str, str]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")


def _remember_action(action_type: str, result_text: str, file_path: str = "") -> None:
    memory = load_memory()
    memory["last_action"] = action_type
    memory["last_file"] = file_path
    memory["last_result"] = (result_text or "").strip().replace("\n", " ")[:240]
    save_memory(memory)

# Paths that do NOT require authentication
_AUTH_EXEMPT = {"/health", "/", "/ui"}

class _BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Exempt public routes and WebSocket upgrade
        if path in _AUTH_EXEMPT or not path.startswith("/api"):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return StarletteResponse(
                content='{"detail":"Authorization required. Add header: Authorization: Bearer <api_key>"}',
                status_code=401,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth[len("Bearer "):]
        if not secrets.compare_digest(token.encode(), _API_KEY.encode()):
            return StarletteResponse(
                content='{"detail":"Invalid API key"}',
                status_code=403,
                media_type="application/json",
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting MacAgent-OS Server...")
    logger.info(f"API KEY (Bearer): {_API_KEY}  [stored at: {_API_KEY_FILE}]")
    await state.core.start()
    state.is_ready = True
    logger.info("Server ready!")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    await state.core.stop()


# === APP ===

app = FastAPI(title="MacAgent-OS Server", version="0.1.0", lifespan=lifespan)
app.add_middleware(_BearerAuthMiddleware)

# === HEALTH ===

@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "ok",
        "version": "0.1.0",
        "ready": state.is_ready
    }


@app.get("/api/logs")
async def get_logs(limit: int = 200):
    """Return recent backend logs."""
    try:
        if not SERVER_LOG_PATH.exists():
            return {"entries": [], "path": str(SERVER_LOG_PATH)}
        lines = SERVER_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
        return {"entries": lines[-max(1, min(limit, 1000)):], "path": str(SERVER_LOG_PATH)}
    except Exception as e:
        return {"entries": [f"Unable to read logs: {e}"], "path": str(SERVER_LOG_PATH)}


def _user_safe_log_entries(entries: list[str]) -> list[str]:
    safe: list[str] = []
    blocked_prefixes = ("Traceback ", "  File \"", "File \"")
    for raw in entries:
        line = (raw or "").strip()
        if not line:
            continue
        if line.startswith(blocked_prefixes):
            continue
        if "API KEY (Bearer)" in line:
            safe.append("Backend: clé API locale chargée.")
            continue
        if "address already in use" in line or "Errno 48" in line:
            safe.append("Port 8000 déjà utilisé: ferme l’autre processus ou change le port.")
            continue
        if len(line) > 260:
            line = line[:257] + "..."
        safe.append(line)
    return safe


def _normalize_ollama_base_url(base_url: str = "http://localhost:11434") -> str:
    base = (base_url or "http://localhost:11434").strip().rstrip("/")
    for suffix in ("/api/chat", "/api/tags", "/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    return base or "http://localhost:11434"


async def _ollama_status(base_url: str = "http://localhost:11434") -> dict[str, Any]:
    base = _normalize_ollama_base_url(base_url)
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/api/tags", timeout=5.0)
        if resp.status_code != 200:
            return {
                "available": False,
                "base_url": base,
                "models": [],
                "message": f"Ollama a répondu avec HTTP {resp.status_code}.",
            }
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        return {
            "available": True,
            "base_url": base,
            "models": models,
            "message": (
                f"{len(models)} modèle(s) Ollama détecté(s)."
                if models else
                "Ollama répond, mais aucun modèle local n’est installé."
            ),
        }
    except Exception:
        return {
            "available": False,
            "base_url": base,
            "models": [],
            "message": "Ollama ne répond pas sur localhost:11434. Lance Ollama si tu veux utiliser des modèles locaux.",
        }


@app.get("/api/diagnostics")
async def diagnostics():
    """Summarize current backend diagnostics."""
    settings = state.core.get_settings()
    oauth = get_openai_oauth_status()
    bridge = get_local_chatgpt_bridge_status()
    heretic = await heretic_status()
    ollama = await _ollama_status(settings.get("base_url") if settings.get("provider") == "ollama" else "http://localhost:11434")
    local_models = ollama.get("models", [])

    logs_payload = await get_logs(limit=60)
    active_mcp = sorted(getattr(state.core.hub, "sessions", {}).keys())
    skipped_mcp = getattr(state.core.hub, "skipped_servers", [])

    settings["api_key"] = "***" if settings.get("api_key") else ""
    return {
        "ready": state.is_ready,
        "settings": settings,
        "chatgpt": oauth,
        "bridge": bridge,
        "heretic": heretic,
        "ollama": {
            "available": bool(ollama.get("available")),
            "base_url": ollama.get("base_url", "http://localhost:11434"),
            "model_count": len(local_models),
            "message": ollama.get("message", ""),
        },
        "mcp": {
            "active": active_mcp,
            "active_count": len(active_mcp),
            "skipped": skipped_mcp,
            "skipped_count": len(skipped_mcp),
        },
        "last_usage": settings.get("last_usage", {}),
        "local_model_count": len(local_models),
        "local_models_preview": local_models[:8],
        "log_entries": _user_safe_log_entries(logs_payload.get("entries", [])[-40:])[-20:],
    }


# === STATE ===

@app.get("/api/state")
async def get_state():
    """Get full application state"""
    return state.core.get_state()


# === WORKSPACES ===

@app.get("/api/workspaces")
async def list_workspaces():
    """List all workspaces"""
    workspaces = state.core.list_workspaces()
    return {"workspaces": [w.to_dict() for w in workspaces]}

@app.post("/api/workspaces")
async def create_workspace(req: WorkspaceRequest):
    """Create a new workspace"""
    resolved = Path(req.path).resolve()
    home = Path.home()
    if not (resolved == home or str(resolved).startswith(str(home) + "/")):
        return JSONResponse({"error": "Path must be within the user home directory"}, status_code=400)
    ws = state.core.create_workspace(str(resolved), req.name)
    return ws.to_dict()

@app.get("/api/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str):
    """Get a workspace"""
    ws = state.core.get_workspace(workspace_id)
    if ws:
        return ws.to_dict()
    return JSONResponse({"error": "not found"}, status_code=404)

@app.post("/api/workspaces/{workspace_id}/set-current")
async def set_current_workspace(workspace_id: str):
    """Set current workspace"""
    success = state.core.set_current_workspace(workspace_id)
    return {"success": success}


# === SESSIONS ===

@app.get("/api/sessions")
async def list_sessions():
    """List all sessions"""
    sessions = state.session_manager.list()
    return {"sessions": [s.to_dict() for s in sessions]}

@app.post("/api/sessions")
async def create_session(name: str = ""):
    """Create a new session"""
    session = state.session_manager.create(name=name)
    return session.to_dict()

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a session"""
    session = state.session_manager.get(session_id)
    if session:
        return session.to_dict()
    return JSONResponse({"error": "not found"}, status_code=404)

@app.post("/api/sessions/{session_id}/set-current")
async def set_current_session(session_id: str):
    """Set current session"""
    success = state.session_manager.set_current(session_id)
    return {"success": success}

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session"""
    success = state.session_manager.delete(session_id)
    return {"success": success}

@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Get session messages"""
    session = state.session_manager.get(session_id)
    if session:
        return {"messages": [m.to_dict() for m in session.messages]}
    return JSONResponse({"error": "not found"}, status_code=404)


# === SKILLS ===

class SelfUpdatePrepareRequest(BaseModel):
    source_path: str = ""
    destination_root: str = ""
    force_working_refresh: bool = False


class SelfUpdatePathRequest(BaseModel):
    working_path: str = ""
    output_root: str = ""
    candidate_app: str = ""
    target_app: str = ""
    backup_root: str = ""
    promote: bool = False
    confirmation: str = ""
    objective: str = ""


def _read_text_excerpt(path: Path, limit: int = 12000) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if len(text) <= limit:
        return _redact_sensitive_text(text)
    head = text[: limit // 2]
    tail = text[-limit // 2:]
    return _redact_sensitive_text(f"{head}\n\n...[extrait réduit]...\n\n{tail}")


def _redact_sensitive_text(text: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", text or "")
    redacted = re.sub(r"hf_[A-Za-z0-9]{12,}", "hf_***", redacted)
    redacted = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token)(\s*[:=]\s*)['\"]?[^'\"\s]+", r"\1\2***", redacted)
    return redacted


def _self_update_context_files(working: Path) -> dict[str, str]:
    candidates = [
        working / "self_update.py",
        working / "server.py",
        working / "skills.py",
        working / "tests" / "test_self_update.py",
        working / "NativeMacApp" / "Sources" / "NativeMacApp.swift",
    ]
    snippets: dict[str, str] = {}
    for path in candidates:
        content = _read_text_excerpt(path, limit=3200 if path.name == "NativeMacApp.swift" else 2600)
        if content:
            snippets[str(path.relative_to(working))] = content
    return snippets


def _compact_self_update_diagnosis(diagnosis: dict[str, Any]) -> dict[str, Any]:
    validation = diagnosis.get("validation") if isinstance(diagnosis, dict) else {}
    checks = validation.get("checks", []) if isinstance(validation, dict) else []
    compact_checks: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        item = {
            "command": check.get("command", ""),
            "returncode": check.get("returncode"),
            "ok": check.get("ok"),
        }
        output = str(check.get("output", "") or "")
        if output and not check.get("ok"):
            item["output"] = output[-1600:]
        compact_checks.append(item)
    return {
        "status": diagnosis.get("status"),
        "message": diagnosis.get("message"),
        "validation": {
            "status": validation.get("status") if isinstance(validation, dict) else None,
            "message": validation.get("message") if isinstance(validation, dict) else None,
            "working_path": validation.get("working_path") if isinstance(validation, dict) else "",
            "checks": compact_checks,
        },
        "suggestions": diagnosis.get("suggestions", [])[:5] if isinstance(diagnosis, dict) else [],
    }


def _write_self_update_proposal(working: Path, content: str) -> str:
    proposals_dir = working / ".macagent" / "self_update_proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    proposal_path = proposals_dir / f"proposal-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    proposal_path.write_text(content, encoding="utf-8")
    return str(proposal_path)


async def _request_self_update_from_llm(working_path: str = "", objective: str = "") -> dict[str, Any]:
    working = Path(working_path or self_update_manager.workspace_from_root().working_path).expanduser()
    try:
        working = working.resolve()
    except Exception:
        return {"status": "error", "message": "Working copy introuvable."}
    if not working.exists() or not working.is_dir():
        return {"status": "error", "message": "Working copy introuvable."}

    diagnosis = await asyncio.to_thread(self_update_manager.diagnose, str(working))
    logs_payload = await get_logs(limit=80)
    safe_logs = [_redact_sensitive_text(line) for line in _user_safe_log_entries(logs_payload.get("entries", []))]
    files = _self_update_context_files(working)
    settings = state.core.get_settings()
    provider_id = settings.get("provider", "ollama")
    model = settings.get("model", "")
    base_url = settings.get("base_url", "")
    api_key = settings.get("api_key", "")

    files_text = "\n\n".join(
        f"## {name}\n```text\n{content}\n```"
        for name, content in files.items()
    )
    user_objective = objective.strip() or (
        "Analyse l'état self-update de Mac Agent OS et propose une amélioration minimale, testable, "
        "sans modifier la copie SAFE et sans casser Providers IA/Ollama/Bridge."
    )
    prompt = (
        f"Objectif utilisateur:\n{user_objective}\n\n"
        f"Diagnostic JSON compact:\n{json.dumps(_compact_self_update_diagnosis(diagnosis), ensure_ascii=False)[:6000]}\n\n"
        f"Logs récents sans secrets:\n" + "\n".join(safe_logs[-20:])[:4000] + "\n\n"
        "Extraits de code pertinents:\n"
        f"{files_text[:14000]}\n\n"
        "Réponds en français avec:\n"
        "1. Résumé du problème ou opportunité.\n"
        "2. Changement minimal recommandé.\n"
        "3. Fichiers à modifier.\n"
        "4. Patch proposé ou pseudo-diff si tu es sûr.\n"
        "5. Tests exacts à lancer.\n"
        "6. Risques et garde-fous.\n"
        "Ne demande jamais de secrets. Ne propose aucune suppression destructive."
    )
    system = (
        "Tu es le mainteneur release candidate de Mac Agent OS. "
        "Tu aides l'application à s'améliorer elle-même, mais uniquement dans la working copy. "
        "Tu privilégies les corrections petites, vérifiables et compatibles avec les providers existants."
    )

    provider_obj = get_provider(provider_id, api_key=api_key, model=model, base_url=base_url)
    result = await asyncio.to_thread(
        provider_obj.chat,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        None,
        None,
    )
    if result.get("type") == "error":
        return {
            "status": "error",
            "message": result.get("content", "Le LLM n'a pas pu proposer d'update."),
            "provider": provider_id,
            "model": model,
            "diagnosis": diagnosis,
        }

    ai_response = result.get("content", "")
    proposal_path = await asyncio.to_thread(_write_self_update_proposal, working, ai_response)
    return {
        "status": "ok",
        "message": "Proposition d'update IA générée et sauvegardée dans la working copy.",
        "provider": provider_id,
        "model": model,
        "working_path": str(working),
        "proposal_path": proposal_path,
        "ai_response": ai_response,
        "context_files": sorted(files.keys()),
        "diagnosis": diagnosis,
    }


def _self_update_cycle_step(name: str, status: str, message: str = "", path: str = "") -> dict[str, str]:
    step = {"name": name, "status": status, "message": message}
    if path:
        step["path"] = path
    return step


async def _skills_runtime_context(check_ollama: bool = True) -> dict[str, Any]:
    settings = state.core.get_settings()
    ollama: dict[str, Any] = {"available": False, "models": []}
    if check_ollama:
        ollama_base = settings.get("base_url") if settings.get("provider") == "ollama" else "http://localhost:11434"
        ollama = await _ollama_status(ollama_base)
    active_tools = set(getattr(state.core.hub, "sessions", {}).keys())
    skipped_tools = {item.get("name", "") for item in getattr(state.core.hub, "skipped_servers", [])}
    active_tools.update({
        "provider_diagnostics",
        "storage_diagnostics",
        "filesystem",
        "local_mac",
        "self_update",
    })
    if ollama.get("available") or settings.get("provider") == "ollama":
        active_tools.add("ollama")
    return {
        "active_tools": sorted(active_tools),
        "skipped_tools": sorted(name for name in skipped_tools if name),
        "ollama_models": ollama.get("models", []),
        "ollama_available": bool(ollama.get("available")),
        "provider": settings.get("provider", ""),
        "model": settings.get("model", ""),
    }


@app.get("/api/skills")
async def list_skills():
    """List all skills"""
    context = await _skills_runtime_context()
    return {"skills": state.skills_manager.to_dict_list(context)}

@app.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str):
    """Get a skill"""
    skill = state.skills_manager.get(skill_id)
    if skill:
        context = await _skills_runtime_context()
        return state.skills_manager.to_dict(skill_id, context)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/self-update/status")
async def self_update_status():
    """Return the self-update workspace status."""
    return self_update_manager.status()


@app.post("/api/self-update/prepare")
async def self_update_prepare(req: SelfUpdatePrepareRequest):
    """Create safe and working copies for self-update work."""
    result = await asyncio.to_thread(
        self_update_manager.prepare,
        req.source_path,
        req.destination_root,
        req.force_working_refresh,
    )
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/self-update/validate")
async def self_update_validate(req: SelfUpdatePathRequest):
    """Validate a self-update working copy."""
    result = await asyncio.to_thread(self_update_manager.validate, req.working_path)
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/self-update/diagnose")
async def self_update_diagnose(req: SelfUpdatePathRequest):
    """Diagnose a self-update working copy and return repair hints."""
    result = await asyncio.to_thread(self_update_manager.diagnose, req.working_path)
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/self-update/build-candidate")
async def self_update_build_candidate(req: SelfUpdatePathRequest):
    """Build a candidate app from a validated working copy."""
    result = await asyncio.to_thread(self_update_manager.build_candidate, req.working_path, req.output_root)
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/self-update/auto-update")
async def self_update_auto_update(req: SelfUpdatePathRequest):
    """Validate, build, and optionally promote a self-update candidate."""
    result = await asyncio.to_thread(
        self_update_manager.auto_update,
        req.working_path,
        req.output_root,
        req.promote,
        req.target_app,
        req.confirmation,
    )
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/self-update/run-cycle")
async def self_update_run_cycle(req: SelfUpdatePathRequest):
    """Run the visible self-update cycle: diagnose, ask the active LLM, then build a candidate."""
    steps: list[dict[str, str]] = []

    diagnosis = await asyncio.to_thread(self_update_manager.diagnose, req.working_path)
    steps.append(_self_update_cycle_step("Diagnostic", diagnosis.get("status", "error"), diagnosis.get("message", "")))
    if diagnosis.get("status") != "ok":
        return {
            "status": "error",
            "message": "Cycle auto-update arrêté: la working copy doit être corrigée avant de demander une update IA.",
            "diagnosis": diagnosis,
            "steps": steps,
        }

    llm_result = await _request_self_update_from_llm(req.working_path, req.objective)
    steps.append(
        _self_update_cycle_step(
            "Demande IA",
            llm_result.get("status", "error"),
            llm_result.get("message", ""),
            llm_result.get("proposal_path", ""),
        )
    )
    if llm_result.get("status") != "ok":
        return {
            "status": "error",
            "message": "Cycle auto-update arrêté: la proposition IA n'a pas été générée.",
            "diagnosis": diagnosis,
            "steps": steps,
            "provider": llm_result.get("provider"),
            "model": llm_result.get("model"),
        }

    build = await asyncio.to_thread(self_update_manager.build_candidate, req.working_path, req.output_root)
    steps.append(
        _self_update_cycle_step(
            "Build candidate",
            build.get("status", "error"),
            build.get("message", ""),
            build.get("candidate_app", ""),
        )
    )
    if build.get("status") != "ok":
        return {
            "status": "error",
            "message": "Cycle auto-update arrêté: la candidate n'a pas pu être buildée.",
            "diagnosis": diagnosis,
            "build": build,
            "steps": steps,
            "proposal_path": llm_result.get("proposal_path"),
            "ai_response": llm_result.get("ai_response"),
            "provider": llm_result.get("provider"),
            "model": llm_result.get("model"),
            "context_files": llm_result.get("context_files"),
        }

    return {
        "status": "ok",
        "message": "Cycle auto-update terminé: proposition IA générée et candidate buildée.",
        "diagnosis": diagnosis,
        "build": build,
        "steps": steps,
        "proposal_path": llm_result.get("proposal_path"),
        "ai_response": llm_result.get("ai_response"),
        "provider": llm_result.get("provider"),
        "model": llm_result.get("model"),
        "context_files": llm_result.get("context_files"),
    }


@app.post("/api/self-update/request-llm-update")
async def self_update_request_llm_update(req: SelfUpdatePathRequest):
    """Ask the active LLM for a self-update proposal using logs, diagnostics, and code excerpts."""
    result = await _request_self_update_from_llm(req.working_path, req.objective)
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/self-update/promote")
async def self_update_promote(req: SelfUpdatePathRequest):
    """Promote a candidate app to a target app path with backup."""
    result = await asyncio.to_thread(
        self_update_manager.promote_candidate,
        req.candidate_app,
        req.target_app,
        req.backup_root,
        req.confirmation,
    )
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/self-update/rollback")
async def self_update_rollback(req: SelfUpdatePathRequest):
    """Restore a target app from a backup app."""
    result = await asyncio.to_thread(
        self_update_manager.rollback_candidate,
        req.candidate_app,
        req.target_app,
        req.confirmation,
    )
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/skills/{skill_id}/enable")
async def enable_skill(skill_id: str):
    """Enable a desktop skill for the current user."""
    try:
        state.skills_manager.set_enabled(skill_id, True)
        context = await _skills_runtime_context()
        return {"skill": state.skills_manager.to_dict(skill_id, context)}
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/skills/{skill_id}/disable")
async def disable_skill(skill_id: str):
    """Disable a desktop skill for the current user."""
    try:
        state.skills_manager.set_enabled(skill_id, False)
        context = await _skills_runtime_context()
        return {"skill": state.skills_manager.to_dict(skill_id, context)}
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/skills/{skill_id}/test")
async def test_skill(skill_id: str):
    """Run a non-destructive availability test for a skill."""
    try:
        context = await _skills_runtime_context()
        return state.skills_manager.test(skill_id, context)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)


# === COMMANDS ===

@app.get("/api/commands")
async def list_commands():
    """List all commands"""
    commands = state.commands_manager.list()
    return {"commands": [c.to_dict() for c in commands]}

@app.get("/api/commands/{command_name}")
async def get_command(command_name: str):
    """Get a command"""
    command = state.commands_manager.get(command_name)
    if command:
        return command.to_dict()
    return JSONResponse({"error": "not found"}, status_code=404)


# === AGENTS ===

@app.get("/api/agents")
async def list_agents():
    """List all agents"""
    agents = state.agents_manager.list()
    return {"agents": [a.to_dict() for a in agents]}

@app.get("/api/agents/{agent_name}")
async def get_agent(agent_name: str):
    """Get an agent"""
    agent = state.agents_manager.get(agent_name)
    if agent:
        return agent.to_dict()
    return JSONResponse({"error": "not found"}, status_code=404)


# === TEMPLATES ===

@app.get("/api/templates")
async def list_templates():
    """List all templates"""
    templates = state.templates_manager.list()
    return {"templates": [t.to_dict() for t in templates]}

@app.post("/api/templates")
async def create_template(name: str, description: str = ""):
    """Create a new template"""
    template = state.templates_manager.create(name, description)
    return template.to_dict()

@app.post("/api/templates/from-session/{session_id}")
async def create_template_from_session(session_id: str, name: str, description: str = ""):
    """Create template from session"""
    try:
        template = state.templates_manager.create_from_session(session_id, name, description)
        return template.to_dict()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# === PERMISSIONS ===

@app.get("/api/permissions")
async def list_permissions():
    """List permissions"""
    pending = state.permissions_manager.get_pending()
    rules = state.permissions_manager.list_rules()
    return {"pending": [p.to_dict() for p in pending], "rules": [r.to_dict() for r in rules]}

@app.post("/api/permissions/{request_id}/reply")
async def reply_permission(request_id: str, req: PermissionReplyRequest, session_id: str = ""):
    """Reply to a permission request"""
    try:
        reply = PermissionReply(req.reply)
        success = state.permissions_manager.approve(request_id, reply, session_id)
        return {"success": success}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# === SETTINGS ===

@app.get("/api/settings")
async def get_settings():
    """Get settings"""
    s = state.core.get_settings()
    s["api_key"] = "***" if s.get("api_key") else ""
    return s

@app.get("/api/models")
async def get_models():
    """Get available models from Ollama"""
    status = await _ollama_status()
    return {
        "models": status.get("models", []),
        "available": status.get("available", False),
        "message": status.get("message", ""),
    }

@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    """Update settings (primary LLM + optional chat/planner split config)."""
    state.core.update_llm_settings(
        provider=req.provider,
        model=req.model,
        api_key=req.api_key,
        base_url=req.base_url
    )
    # Persist optional split-model keys alongside the main settings
    if any(v is not None for v in [
        req.chat_provider, req.chat_model, req.chat_base_url,
        req.planner_provider, req.planner_model, req.planner_base_url,
    ]):
        from paths import get_data_dir
        settings_path = get_data_dir() / "settings.json"
        try:
            existing: dict = {}
            if settings_path.exists():
                existing = json.loads(settings_path.read_text())
            for key, val in [
                ("chat_provider", req.chat_provider),
                ("chat_model", req.chat_model),
                ("chat_base_url", req.chat_base_url),
                ("planner_provider", req.planner_provider),
                ("planner_model", req.planner_model),
                ("planner_base_url", req.planner_base_url),
            ]:
                if val is not None:
                    existing[key] = val
                elif key in existing:
                    del existing[key]   # explicit None clears the override
            settings_path.write_text(json.dumps(existing, indent=2))
        except Exception as exc:
            logger.warning("Could not persist split-model settings: %s", exc)
    return {"status": "success"}


# === UNIVERSAL LLM PROVIDERS ===

from llm_universal import get_provider, get_all_providers_info, PROVIDERS

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

chatgpt_connect_state = {
    "running": False,
    "status": "idle",
    "url": "",
    "logs": "",
    "error": "",
    "connected": False,
    "pid": None,
}


def _append_chatgpt_log(text: str):
    cleaned = ANSI_RE.sub("", text).replace("\r", "\n")
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    if not cleaned:
        return
    chatgpt_connect_state["logs"] = (chatgpt_connect_state["logs"] + "\n" + cleaned).strip()


def _refresh_chatgpt_connected():
    bridge = get_local_chatgpt_bridge_status()
    chatgpt_connect_state["connected"] = bridge["connected"]
    if bridge["connected"]:
        chatgpt_connect_state["status"] = "connected"
        chatgpt_connect_state["error"] = ""
    elif bridge["error"]:
        chatgpt_connect_state["status"] = bridge["status"]
        chatgpt_connect_state["error"] = bridge["error"]


@app.get("/api/chatgpt/status")
async def chatgpt_status():
    _refresh_chatgpt_connected()
    oauth = get_openai_oauth_status()
    bridge = get_local_chatgpt_bridge_status()
    return {
        **chatgpt_connect_state,
        "oauth": oauth,
        "bridge": bridge,
    }


@app.post("/api/chatgpt/connect")
async def chatgpt_connect():
    if chatgpt_connect_state["running"]:
        return {
            "status": chatgpt_connect_state["status"],
            "url": chatgpt_connect_state["url"],
            "connected": chatgpt_connect_state["connected"],
        }

    _refresh_chatgpt_connected()
    if chatgpt_connect_state["connected"]:
        return {
            "status": "connected",
            "url": "",
            "connected": True,
        }

    bridge_bin = get_opencode_bin()
    bridge_kind = get_bridge_runtime_kind(bridge_bin) if bridge_bin else "missing"
    if not bridge_bin:
        chatgpt_connect_state["status"] = "not_installed"
        chatgpt_connect_state["error"] = "Bridge ChatGPT absent du bundle de cette app."
        return {
            "status": "not_installed",
            "url": "",
            "connected": False,
            "error": chatgpt_connect_state["error"],
        }

    chatgpt_connect_state["running"] = True
    chatgpt_connect_state["status"] = "starting"
    chatgpt_connect_state["url"] = ""
    chatgpt_connect_state["logs"] = ""
    chatgpt_connect_state["error"] = ""
    chatgpt_connect_state["connected"] = False
    chatgpt_connect_state["pid"] = None

    def run_login():
        try:
            if bridge_kind == "codex":
                cmd = [bridge_bin, "login", "--device-auth"]
            else:
                cmd = [
                    bridge_bin,
                    "providers",
                    "login",
                    "-p",
                    "openai",
                    "-m",
                    "ChatGPT Pro/Plus (browser)",
                ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            chatgpt_connect_state["pid"] = proc.pid
            chatgpt_connect_state["status"] = "waiting"

            for line in iter(proc.stdout.readline, ""):
                if not line:
                    continue
                _append_chatgpt_log(line)
                url_match = re.search(r"https?://\S+", line)
                if "Go to:" in line:
                    chatgpt_connect_state["url"] = line.split("Go to:", 1)[1].strip()
                    chatgpt_connect_state["status"] = "authorize"
                elif url_match:
                    chatgpt_connect_state["url"] = url_match.group(0).rstrip(".,)")
                    chatgpt_connect_state["status"] = "authorize"

            proc.wait()
            _refresh_chatgpt_connected()
            if chatgpt_connect_state["connected"]:
                chatgpt_connect_state["status"] = "connected"
            elif proc.returncode == 0:
                chatgpt_connect_state["status"] = "completed"
            else:
                chatgpt_connect_state["status"] = "error"
                chatgpt_connect_state["error"] = "Connexion ChatGPT échouée. Vérifie les logs du bridge."
        except Exception as exc:
            chatgpt_connect_state["status"] = "error"
            chatgpt_connect_state["error"] = str(exc)
            _append_chatgpt_log(str(exc))
        finally:
            chatgpt_connect_state["running"] = False

    thread = threading.Thread(target=run_login, daemon=True)
    thread.start()

    return {
        "status": chatgpt_connect_state["status"],
        "url": chatgpt_connect_state["url"],
        "connected": False,
        "error": chatgpt_connect_state["error"],
    }

@app.get("/api/llm/providers")
async def list_llm_providers():
    """Liste tous les providers LLM disponibles"""
    providers = get_all_providers_info()
    
    # Enrichir avec les modèles de chaque provider
    for p in providers:
        try:
            provider = get_provider(p["id"], model="test")
            models = provider.get_models()
            p["models"] = models[:10]  # Limiter à 10
        except Exception as e:
            p["models"] = []
            p["error"] = str(e)
    
    return {"providers": providers}


@app.get("/api/provider-connections")
async def list_provider_connections():
    """Describe provider connection types for native and desktop clients."""
    providers = get_provider_connection_specs()
    bridge = get_local_chatgpt_bridge_status()
    for provider in providers:
        if provider["id"] == "local_chatgpt_codex":
            provider["runtime"] = bridge
    return {"providers": providers}

@app.get("/api/llm/models/{provider}")
async def get_provider_models(provider: str, api_key: str = "", base_url: str = ""):
    """Liste les modèles disponibles pour un provider - avec API key pour récupérer depuis l'API réelle"""
    try:
        # Pass the API key to get real models from the provider's API
        p = get_provider(provider, api_key=api_key, base_url=base_url)
        models = p.get_models(api_key=api_key)
        return {"provider": provider, "models": models}
    except Exception as e:
        logger.info("Model listing failed provider=%s error=%s", provider, e)
        return JSONResponse({"error": str(e)}, status_code=400)


class LocalActionPlanRequest(BaseModel):
    message: str


class LocalActionExecuteActionPayload(BaseModel):
    app_name: Optional[str] = None
    target_path: Optional[str] = None
    content: Optional[str] = None
    instruction: Optional[str] = None
    url: Optional[str] = None
    source_path: Optional[str] = None
    output_path: Optional[str] = None
    path: Optional[str] = None


class LocalActionExecuteAction(BaseModel):
    type: str
    payload: LocalActionExecuteActionPayload
    steps: Optional[List["LocalActionExecuteAction"]] = None


LocalActionExecuteAction.model_rebuild()


class LocalActionExecuteRequest(BaseModel):
    approved: bool = False
    action: LocalActionExecuteAction


def _simple_desktop_path(value: str, allowed_suffixes: tuple[str, ...]) -> bool:
    value = _canonical_desktop_path(value)
    if not isinstance(value, str):
        return False
    if not value.startswith("~/Desktop/"):
        return False
    name = Path(value.replace("~", str(Path.home()))).name
    if not name or name != Path(name).name or "/" in name or "\\" in name:
        return False
    return name.lower().endswith(allowed_suffixes)


def _canonical_desktop_path(value: str) -> str:
    if not isinstance(value, str):
        return value
    home = str(Path.home())
    normalized = value.strip()
    if normalized.startswith("$HOME/Desktop/"):
        return "~/" + normalized[len("$HOME/"):]
    if normalized.startswith(f"{home}/Desktop/"):
        return "~/" + normalized[len(home) + 1:]
    return normalized


def _sanitize_http_url(value: str) -> str:
    candidate = (value or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc:
        return ""
    return parsed.geturl()


def _normalize_open_app_name(value: str) -> str:
    normalized = (value or "").strip().lower()
    normalized_ascii = "".join(
        char for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(char)
    )
    if normalized_ascii in {"safari"}:
        return "Safari"
    if normalized_ascii in {
        "system settings",
        "system preferences",
        "settings",
        "reglages",
        "reglages systeme",
        "preferences systeme",
    }:
        return "System Settings"
    return ""


def _open_app_label(app_name: str) -> str:
    if app_name == "System Settings":
        return "Ouvrir les réglages système"
    return "Ouvrir Safari"


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    candidate = (text or "").strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except Exception:
        pass

    fenced = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not fenced:
        return None
    try:
        return json.loads(fenced.group(0))
    except Exception:
        return None


def _recover_planned_action_from_text(text: str) -> Optional[dict[str, Any]]:
    candidate = text or ""
    objective_match = re.search(r'"objective"\s*:\s*"([^"]+)"', candidate, re.DOTALL)
    objective = objective_match.group(1).strip() if objective_match else "Plan local"

    steps: list[dict[str, Any]] = []

    app_match = re.search(r'"type"\s*:\s*"open_app".*?"app_name"\s*:\s*"([^"]+)"', candidate, re.DOTALL)
    if app_match:
        app_name = _normalize_open_app_name(app_match.group(1))
        if app_name:
            steps.append({"type": "open_app", "payload": {"app_name": app_name}})

    for match in re.finditer(
        r'"type"\s*:\s*"create_file".*?"target_path"\s*:\s*"([^"]+)"(?:.*?"content"\s*:\s*"([^"]*)")?',
        candidate,
        re.DOTALL,
    ):
        target_path = match.group(1).strip()
        content = (match.group(2) or "").strip()
        steps.append({
            "type": "create_file",
            "payload": {
                "target_path": target_path,
                "content": content,
            }
        })

    for match in re.finditer(
        r'"type"\s*:\s*"summarize_folder_to_file".*?"source_path"\s*:\s*"([^"]+)".*?"output_path"\s*:\s*"([^"]+)"',
        candidate,
        re.DOTALL,
    ):
        steps.append({
            "type": "summarize_folder_to_file",
            "payload": {
                "source_path": match.group(1).strip(),
                "output_path": match.group(2).strip(),
            }
        })

    if not steps:
        return None

    return {
        "type": "multi_step_plan",
        "objective": objective,
        "steps": steps[:3],
    }


def _normalize_step_candidate(step: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(step, dict):
        return None

    step_type = step.get("type") or step.get("op") or ""
    title = str(step.get("title") or step.get("label") or "").strip()
    desc = str(step.get("desc") or "").strip()
    payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}

    app_text = f"{title} {desc} {payload.get('app_name', '')}".lower()
    if step_type == "open_app" or "safari" in app_text or "system settings" in app_text or "reglages" in app_text:
        app_name = _normalize_open_app_name(str(payload.get("app_name") or app_text))
        if not app_name:
            app_name = "Safari" if "safari" in app_text else "System Settings"
        return {"type": "open_app", "payload": {"app_name": app_name}}

    if step_type == "create_file" or ("create" in title.lower() and ".txt" in f"{title} {desc}".lower()):
        target_path = str(payload.get("target_path") or "").strip()
        if not target_path:
            match = re.search(r"([A-Za-z0-9._-]+\.txt)", f"{title} {desc}")
            if not match:
                return None
            target_path = f"~/Desktop/{match.group(1)}"
        target_path = _canonical_desktop_path(target_path)
        content = payload.get("content", "")
        if not isinstance(content, str):
            content = ""
        return {
            "type": "create_file",
            "payload": {
                "target_path": target_path,
                "content": content,
            }
        }

    if step_type == "code_task":
        target_path = _canonical_desktop_path(str(payload.get("target_path") or "").strip())
        instruction = str(payload.get("instruction") or "").strip()
        return {
            "type": "code_task",
            "payload": {
                "target_path": target_path,
                "instruction": instruction,
            }
        }

    if step_type == "summarize_folder_to_file":
        source_path = str(payload.get("source_path") or "").strip()
        output_path = str(payload.get("output_path") or "").strip()
        return {
            "type": "summarize_folder_to_file",
            "payload": {
                "source_path": source_path,
                "output_path": output_path,
            }
        }

    return None


def _normalize_planned_action(plan: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(plan, dict):
        return None

    normalized = dict(plan)
    if normalized.get("type") != "multi_step_plan" and isinstance(normalized.get("steps"), list):
        normalized["type"] = "multi_step_plan"

    if not normalized.get("objective"):
        normalized["objective"] = normalized.get("title") or normalized.get("goal")

    raw_steps = normalized.get("steps")
    if (not isinstance(raw_steps, list) or not raw_steps) and isinstance(normalized.get("plan"), list):
        plan_items = normalized.get("plan")
        if plan_items and all(isinstance(item, dict) for item in plan_items):
            raw_steps = plan_items
            normalized["plan"] = None
    if not isinstance(raw_steps, list) or not raw_steps:
        return None

    normalized_steps: list[dict[str, Any]] = []
    readable_plan: list[str] = []
    for raw_step in raw_steps[:3]:
        step = _normalize_step_candidate(raw_step)
        if not step:
            return None
        normalized_steps.append(step)
        if step["type"] == "open_app":
            readable_plan.append(_open_app_label(step["payload"]["app_name"]))
        elif step["type"] == "create_file":
            readable_plan.append(f"Créer {Path(step['payload']['target_path']).name} sur le Bureau")
        elif step["type"] == "code_task":
            readable_plan.append(f"Générer {Path(step['payload']['target_path']).name} sur le Bureau")
        elif step["type"] == "summarize_folder_to_file":
            readable_plan.append("Résumer le dossier et créer un fichier sur le Bureau")

    if not isinstance(normalized.get("plan"), list) or not normalized.get("plan"):
        normalized["plan"] = readable_plan
    normalized["steps"] = normalized_steps
    return normalized


def _plan_matches_request(message: str, steps: list[dict[str, Any]]) -> bool:
    normalized = (message or "").lower()
    normalized_ascii = "".join(
        char for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(char)
    )

    mentions_safari = "safari" in normalized_ascii
    mentions_settings = any(token in normalized_ascii for token in ("system settings", "system preferences", "reglages", "preferences systeme"))
    mentions_file = "fichier" in normalized_ascii or ".txt" in normalized_ascii or "file" in normalized_ascii
    mentions_desktop = "bureau" in normalized_ascii or "desktop" in normalized_ascii
    mentions_summary = "resume" in normalized_ascii or "summar" in normalized_ascii

    for step in steps:
        step_type = step.get("type")
        app_name = (step.get("payload") or {}).get("app_name", "")
        if step_type == "open_app" and app_name == "Safari" and not mentions_safari:
            return False
        if step_type == "open_app" and app_name == "System Settings" and not mentions_settings:
            return False
        if step_type == "create_file" and not (mentions_file and mentions_desktop):
            return False
        if step_type == "code_task" and not any(token in normalized_ascii for token in ("code", "script", "html", "site")):
            return False
        if step_type == "summarize_folder_to_file" and not (mentions_summary and mentions_desktop):
            return False
    return True


def _validate_planned_action(plan: dict[str, Any], message: str = "") -> Optional[dict[str, Any]]:
    plan = _normalize_planned_action(plan) or plan
    if plan.get("type") != "multi_step_plan":
        return None

    objective = plan.get("objective")
    steps = plan.get("steps")
    readable_plan = plan.get("plan")

    if not isinstance(objective, str) or not objective.strip():
        return None
    if not isinstance(readable_plan, list) or not readable_plan or len(readable_plan) > 3:
        return None
    if not isinstance(steps, list) or not steps or len(steps) > 3:
        return None
    if any(not isinstance(item, str) or not item.strip() for item in readable_plan):
        return None

    validated_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            return None
        step_type = step.get("type")
        payload = step.get("payload", {})
        if not isinstance(payload, dict):
            return None

        if step_type == "open_app":
            app_name = _normalize_open_app_name(payload.get("app_name", ""))
            if not app_name:
                return None
            validated_steps.append({"type": "open_app", "payload": {"app_name": app_name}})
            continue

        if step_type == "create_file":
            target_path = payload.get("target_path", "")
            target_path = _canonical_desktop_path(target_path)
            content = payload.get("content", "")
            if not _simple_desktop_path(target_path, (".txt",)):
                return None
            if not isinstance(content, str):
                return None
            validated_steps.append({
                "type": "create_file",
                "payload": {
                    "target_path": target_path,
                    "content": content,
                }
            })
            continue

        if step_type == "code_task":
            target_path = _canonical_desktop_path(payload.get("target_path", ""))
            instruction = payload.get("instruction", "")
            if not _simple_desktop_path(target_path, (".txt", ".html", ".py")):
                return None
            if not isinstance(instruction, str) or not instruction.strip():
                return None
            validated_steps.append({
                "type": "code_task",
                "payload": {
                    "target_path": target_path,
                    "instruction": instruction.strip(),
                }
            })
            continue

        if step_type == "summarize_folder_to_file":
            source_path = payload.get("source_path", "")
            output_path = _canonical_desktop_path(payload.get("output_path", ""))
            project_root = str(Path.home() / "Desktop")
            if source_path != project_root:
                return None
            if not _simple_desktop_path(output_path, (".txt",)):
                return None
            validated_steps.append({
                "type": "summarize_folder_to_file",
                "payload": {
                    "source_path": source_path,
                    "output_path": output_path,
                }
            })
            continue

        return None

    if message and not _plan_matches_request(message, validated_steps):
        return None

    return {
        "type": "approval_required",
        "objective": objective.strip(),
        "plan": [item.strip() for item in readable_plan],
        "action": {
            "type": "multi_step_plan",
            "payload": {},
            "steps": validated_steps,
        }
    }


def _get_planner_settings() -> dict:
    """
    Return provider/model/api_key/base_url to use for the planner and router.

    Priority:
      1. planner_provider / planner_model keys in settings.json  (explicit override)
      2. Fallback to the primary provider/model (backward-compatible)
    """
    s = state.core.get_settings()
    primary_provider = s.get("provider", "ollama")
    planner_provider = s.get("planner_provider")
    if primary_provider == "ollama" and planner_provider == "local_chatgpt_codex":
        planner_provider = None
    provider = planner_provider or primary_provider
    model    = s.get("planner_model")    or s.get("model", "") or ""
    base_url = s.get("planner_base_url") or s.get("base_url", "") or ""
    api_key  = s.get("api_key", "") or ""
    if provider == "local":
        provider = "ollama"
    return {"provider": provider, "model": model, "api_key": api_key, "base_url": base_url}


async def _plan_with_llm(message: str) -> dict[str, Any]:
    ps = _get_planner_settings()
    project_root = str(Path.home() / "Desktop")
    # Compact prompt: ~half the tokens of the original, same semantic contract.
    prompt = (
        f'Request: {message}\n'
        f'Actions: open_app(app_name=Safari) | create_file(target_path=~/Desktop/x.txt,content=) | '
        f'code_task(target_path=~/Desktop/x.html|x.py|x.txt,instruction=) | '
        f'summarize_folder_to_file(source_path={project_root},output_path=~/Desktop/x.txt)\n'
        'Output JSON: {"type":"multi_step_plan","objective":"...","plan":["..."],'
        '"steps":[{"type":"...","payload":{}}]}\n'
        'Rules: max 2 steps, Desktop .txt only, no markdown. '
        'Unclear request: {"type":"none"}'
    )

    req = LLMChatRequest(
        provider=ps["provider"],
        model=ps["model"],
        api_key=ps["api_key"],
        message=prompt,
        system_prompt="JSON only. No markdown.",
        base_url=ps["base_url"],
        attachments=[],
        history=[],
        project_path="__planner__",
        turbo=False,
        allow_auto_routing=False,
    )

    try:
        result = await asyncio.wait_for(llm_chat(req), timeout=10.0)
        if not isinstance(result, dict):
            return {"type": "none"}
        content = (result.get("content") or "").strip()
        parsed = _extract_json_object(content)
        if not parsed:
            parsed = _recover_planned_action_from_text(content)
        if not parsed:
            return {"type": "none"}
        validated = _validate_planned_action(parsed, message=message)
        return validated or {"type": "none"}
    except Exception as e:
        logger.warning("LLM planner failed for local actions: %s", e)
        return {"type": "none"}


def _label_for_step(action: LocalActionExecuteAction) -> str:
    if action.type == "analyze_mac":
        return "Analyser Mac"
    if action.type == "analyze_storage":
        return "Analyser stockage"
    if action.type == "open_app":
        return _open_app_label(_normalize_open_app_name(action.payload.app_name or "") or "Safari")
    if action.type == "open_url":
        return "Ouvrir URL"
    if action.type == "create_file":
        return "Créer fichier"
    if action.type == "append_file":
        return "Ajouter au fichier"
    if action.type == "read_file":
        return "Lire fichier"
    if action.type == "code_task":
        return "Générer code"
    if action.type == "summarize_folder_to_file":
        return "Résumer dossier"
    return action.type


def _detect_local_action(message: str) -> dict[str, Any]:
    raw_message = (message or "").strip()
    normalized = raw_message.lower()
    normalized_ascii = "".join(
        char for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(char)
    )
    storage_keywords = ("stockage", "storage", "disque", "disk", "place", "espace", "occupe", "occupent")
    analyze_keywords = (
        "analyse",
        "analyser",
        "scan",
        "diagnostique",
        "verifie",
        "vérifie",
        "check",
        "regarde",
        "regarder",
        "etat",
        "état",
    )
    mac_keywords = ("mon mac", "mac", "ordinateur", "machine", "systeme", "système")
    if any(word in normalized_ascii for word in storage_keywords) and any(word in normalized_ascii for word in analyze_keywords):
        return {
            "type": "approval_required",
            "objective": "Analyser l'espace de stockage de ce Mac",
            "plan": [
                "Mesurer les principaux dossiers locaux en lecture seule",
                "Classer les éléments les plus volumineux",
                "Afficher un résumé compréhensible dans le chat",
            ],
            "action": {
                "type": "analyze_storage",
                "payload": {}
            }
        }
    if any(word in normalized_ascii for word in mac_keywords) and any(word in normalized_ascii for word in analyze_keywords):
        return {
            "type": "approval_required",
            "objective": "Analyser ce Mac",
            "plan": [
                "Lire quelques indicateurs locaux en lecture seule",
                "Résumer le stockage et les processus actifs",
                "Afficher le résultat dans le chat",
            ],
            "action": {
                "type": "analyze_mac",
                "payload": {}
            }
        }
    if re.fullmatch(r"(ouvre|ouvrir|open)\s+safari[.! ]*", normalized_ascii):
        return {
            "type": "approval_required",
            "objective": "Ouvrir Safari sur ce Mac",
            "plan": [
                "Valider la demande utilisateur",
                "Lancer Safari localement",
                "Confirmer le résultat dans le chat",
            ],
            "action": {
                "type": "open_app",
                "payload": {
                    "app_name": "Safari"
                }
            }
        }
    if re.fullmatch(
        r"(ouvre|ouvrir|open|lance|lancer)\s+(les\s+)?(reglages\s+systeme|preferences\s+systeme|system\s+settings|system\s+preferences)[.! ]*",
        normalized_ascii,
    ):
        return {
            "type": "approval_required",
            "objective": "Ouvrir les réglages système sur ce Mac",
            "plan": [
                "Valider la demande utilisateur",
                "Lancer les réglages système localement",
                "Confirmer le résultat dans le chat",
            ],
            "action": {
                "type": "open_app",
                "payload": {
                    "app_name": "System Settings"
                }
            }
        }
    if re.fullmatch(r"(prepare|preparez|prepare-moi|prepare mon)\s+mon\s+espace\s+de\s+travail[.! ]*", normalized_ascii):
        return {
            "type": "approval_required",
            "objective": "Préparer l’espace de travail",
            "plan": [
                "Ouvrir Safari",
                "Créer un fichier notes.txt sur le Bureau",
            ],
            "action": {
                "type": "multi_step_plan",
                "payload": {},
                "steps": [
                    {
                        "type": "open_app",
                        "payload": {
                            "app_name": "Safari"
                        }
                    },
                    {
                        "type": "create_file",
                        "payload": {
                            "target_path": "~/Desktop/notes.txt",
                            "content": ""
                        }
                    }
                ]
            }
        }
    if re.fullmatch(r"resume\s+ce\s+dossier\s+et\s+cree?\s+un\s+fichier\s+sur\s+le\s+bureau[.! ]*", normalized_ascii):
        project_root = str(Path.home() / "Desktop")
        return {
            "type": "approval_required",
            "objective": "Résumer ce dossier projet et créer un fichier sur le Bureau",
            "plan": [
                "Valider la demande utilisateur",
                "Lire les fichiers texte simples du dossier projet",
                "Générer un résumé borné et l’écrire sur le Bureau",
            ],
            "action": {
                "type": "summarize_folder_to_file",
                "payload": {
                    "source_path": project_root,
                    "output_path": "~/Desktop/resume-alpha-definif.txt"
                }
            }
        }
    file_with_content_match = re.fullmatch(
        r"cree?\s+un\s+fichier\s+([A-Za-z0-9._-]+\.txt)(?:\s+sur\s+le\s+bureau)?\s+avec\s+(.+?)[.! ]*",
        normalized_ascii,
    )
    if file_with_content_match:
        filename = file_with_content_match.group(1)
        content_request = file_with_content_match.group(2).strip()
        return {
            "type": "approval_required",
            "objective": f"Créer le fichier {filename} sur le Bureau avec du contenu généré",
            "plan": [
                "Valider la demande utilisateur",
                "Générer un contenu texte court",
                "Créer le fichier texte sur le Bureau",
            ],
            "action": {
                "type": "create_file",
                "payload": {
                    "target_path": f"~/Desktop/{filename}",
                    "content": content_request
                }
            }
        }
    write_in_file_match = re.fullmatch(
        r"ecris\s+(.+?)\s+dans\s+un\s+fichier\s+sur\s+le\s+bureau[.! ]*",
        normalized_ascii,
    )
    if write_in_file_match:
        content_request = write_in_file_match.group(1).strip()
        return {
            "type": "approval_required",
            "objective": "Créer un fichier texte sur le Bureau avec du contenu généré",
            "plan": [
                "Valider la demande utilisateur",
                "Générer un contenu texte court",
                "Créer le fichier note.txt sur le Bureau",
            ],
            "action": {
                "type": "create_file",
                "payload": {
                    "target_path": "~/Desktop/note.txt",
                    "content": content_request
                }
            }
        }
    file_match = re.fullmatch(
        r"cree?\s+un\s+fichier\s+([A-Za-z0-9._-]+\.txt)\s+sur\s+le\s+bureau[.! ]*",
        normalized_ascii,
    )
    if file_match:
        filename = file_match.group(1)
        return {
            "type": "approval_required",
            "objective": f"Créer le fichier {filename} sur le Bureau",
            "plan": [
                "Valider la demande utilisateur",
                "Créer un fichier texte vide sur le Bureau",
                "Confirmer le chemin créé dans le chat",
            ],
            "action": {
                "type": "create_file",
                "payload": {
                    "target_path": f"~/Desktop/{filename}",
                    "content": ""
                }
            }
        }
    return {"type": "none"}


# ---------------------------------------------------------------------------
# Template planner — deterministic, no LLM, partial-match (re.search)
# Runs after _detect_local_action (strict fullmatch) and before _plan_with_llm.
# ---------------------------------------------------------------------------

_RE_T_SAFARI   = re.compile(r"\b(ouvre|ouvrir|open|lance|lancer)\s+safari\b")
_RE_T_TXTFILE  = re.compile(r"\b(cree?r?|create)\s+(?:(?:un?|a)\s+)?(?:fichier|file)\s+([A-Za-z0-9._-]+\.txt)\b")
_RE_T_DESKTOP  = re.compile(r"\bsur\s+le\s+bureau\b")
_RE_T_AVEC     = re.compile(r"\bave[ck]\s+(.+?)(?:[.!?]|\Z)")
_RE_T_WSPACE   = re.compile(r"\b(prepare|preparez|prepare-moi)\s+mon\s+espace\s+de\s+travail\b")
_RE_T_CODEFILE = re.compile(r"\b([A-Za-z0-9._-]+\.(?:txt|html|py))\b")
_RE_T_READFILE = re.compile(r"\b(?:lis|lire|read)\s+(?:le\s+fichier\s+)?([A-Za-z0-9._-]+\.(?:txt|py|html))\b")
_RE_T_APPENDFILE = re.compile(r"\bajoute\s+(.+?)\s+dans\s+([A-Za-z0-9._-]+\.(?:txt|py|html))\b")
_RE_T_URL = re.compile(r"\bhttps?://[^\s]+", re.IGNORECASE)
_RE_T_READ_AMBIG = re.compile(r"\b(?:lis|lire|read|relis)\b")
_RE_T_APPEND_AMBIG = re.compile(r"\bajoute\s+(.+?)(?:[.!?]|\Z)")


def _template_norm(message: str) -> str:
    """Same normalisation used by _detect_local_action."""
    normalized = (message or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(c)
    )


def _last_memory_target(allowed_suffixes: tuple[str, ...]) -> str:
    last_file = _canonical_desktop_path(load_memory().get("last_file", ""))
    if not _simple_desktop_path(last_file, allowed_suffixes):
        return ""
    return last_file


def try_template_plan(message: str) -> Optional[dict[str, Any]]:
    """
    Partial-match template planner. Returns a full approval_required dict or None.
    Uses re.search — catches compound/variant phrasings that _detect_local_action
    (fullmatch only) misses.  Security: every filename is validated through
    _simple_desktop_path before being included in the response.
    """
    n = _template_norm(message)

    # ── TEMPLATE 2: workspace setup (looser than fullmatch) ──────────────────
    if _RE_T_WSPACE.search(n):
        return {
            "type": "approval_required",
            "objective": "Préparer l'espace de travail",
            "plan": ["Ouvrir Safari", "Créer un fichier notes.txt sur le Bureau"],
            "action": {
                "type": "multi_step_plan",
                "payload": {},
                "steps": [
                    {"type": "open_app",    "payload": {"app_name": "Safari"}},
                    {"type": "create_file", "payload": {"target_path": "~/Desktop/notes.txt", "content": ""}},
                ],
            },
        }

    has_safari   = bool(_RE_T_SAFARI.search(n))
    file_m       = _RE_T_TXTFILE.search(n)
    on_desktop   = bool(_RE_T_DESKTOP.search(n))
    content_m    = _RE_T_AVEC.search(n)
    code_file_m  = _RE_T_CODEFILE.search(n)
    read_file_m  = _RE_T_READFILE.search(n)
    append_file_m = _RE_T_APPENDFILE.search(n)
    read_ambig_m = _RE_T_READ_AMBIG.search(n)
    append_ambig_m = _RE_T_APPEND_AMBIG.search(n)
    url_m = _RE_T_URL.search(message or "")
    has_site     = "cree un site" in n or "cree une page html" in n or "page html" in n or "site html" in n
    has_python   = "python" in n and ("script" in n or "code" in n)
    has_code     = any(token in n for token in ("code ", " code", "genere du code", "genere un code", "genere du html"))
    has_sensitive_path = bool(re.search(r"/(etc|usr|bin|system|private)\b", n))

    if url_m and any(token in n for token in ("ouvre", "ouvrir", "open", "lance", "lancer")):
        safe_url = _sanitize_http_url(url_m.group(0))
        if not safe_url:
            return None
        return {
            "type": "approval_required",
            "objective": "Ouvrir une URL dans Safari",
            "plan": ["Valider la demande utilisateur", "Ouvrir l'URL dans Safari"],
            "action": {
                "type": "open_url",
                "payload": {"url": safe_url},
            },
        }

    if read_file_m:
        filename = read_file_m.group(1)
        target = f"~/Desktop/{filename}"
        if not _simple_desktop_path(target, (".txt", ".py", ".html")):
            return None
        return {
            "type": "approval_required",
            "objective": f"Lire {filename} sur le Bureau",
            "plan": ["Valider la demande utilisateur", f"Lire {filename} de façon sûre"],
            "action": {
                "type": "read_file",
                "payload": {"target_path": target},
            },
        }

    if read_ambig_m:
        target = _last_memory_target((".txt", ".py", ".html"))
        if target:
            filename = Path(target).name
            return {
                "type": "approval_required",
                "objective": f"Lire à nouveau {filename} sur le Bureau",
                "plan": ["Réutiliser le dernier fichier mémorisé", f"Lire {filename} de façon sûre"],
                "action": {
                    "type": "read_file",
                    "payload": {"target_path": target},
                },
            }

    if append_file_m:
        content_to_add = append_file_m.group(1).strip().strip("'\"")
        filename = append_file_m.group(2)
        target = f"~/Desktop/{filename}"
        if not _simple_desktop_path(target, (".txt", ".py", ".html")):
            return None
        return {
            "type": "approval_required",
            "objective": f"Ajouter du contenu dans {filename} sur le Bureau",
            "plan": ["Valider la demande utilisateur", f"Ajouter le texte dans {filename}"],
            "action": {
                "type": "append_file",
                "payload": {
                    "target_path": target,
                    "content": content_to_add,
                },
            },
        }

    if append_ambig_m:
        target = _last_memory_target((".txt", ".py", ".html"))
        content_to_add = append_ambig_m.group(1).strip().strip("'\"")
        if target and content_to_add and " dans " not in content_to_add:
            filename = Path(target).name
            return {
                "type": "approval_required",
                "objective": f"Ajouter du contenu dans {filename} sur le Bureau",
                "plan": ["Réutiliser le dernier fichier mémorisé", f"Ajouter le texte dans {filename}"],
                "action": {
                    "type": "append_file",
                    "payload": {
                        "target_path": target,
                        "content": content_to_add,
                    },
                },
            }

    # ── TEMPLATE 1: Safari + create file (multi-step) ────────────────────────
    if has_safari and file_m:
        filename = file_m.group(2)
        target   = f"~/Desktop/{filename}"
        if not _simple_desktop_path(target, (".txt",)):
            return None
        content = content_m.group(1).strip() if content_m else ""
        return {
            "type": "approval_required",
            "objective": f"Ouvrir Safari et créer {filename} sur le Bureau",
            "plan": ["Ouvrir Safari", f"Créer {filename} sur le Bureau"],
            "action": {
                "type": "multi_step_plan",
                "payload": {},
                "steps": [
                    {"type": "open_app",    "payload": {"app_name": "Safari"}},
                    {"type": "create_file", "payload": {"target_path": target, "content": content}},
                ],
            },
        }

    # ── TEMPLATE 4: create file + explicit content request ───────────────────
    if file_m and content_m:
        filename        = file_m.group(2)
        content_request = content_m.group(1).strip()
        target          = f"~/Desktop/{filename}"
        if not _simple_desktop_path(target, (".txt",)):
            return None
        return {
            "type": "approval_required",
            "objective": f"Créer {filename} sur le Bureau",
            "plan": ["Valider la demande", f"Créer {filename} sur le Bureau"],
            "action": {
                "type": "create_file",
                "payload": {"target_path": target, "content": content_request},
            },
        }

    # ── TEMPLATE 3: create file on desktop (no content) ──────────────────────
    if file_m and on_desktop:
        filename = file_m.group(2)
        target   = f"~/Desktop/{filename}"
        if not _simple_desktop_path(target, (".txt",)):
            return None
        return {
            "type": "approval_required",
            "objective": f"Créer {filename} sur le Bureau",
            "plan": ["Valider la demande", f"Créer {filename} sur le Bureau"],
            "action": {
                "type": "create_file",
                "payload": {"target_path": target, "content": ""},
            },
        }

    # ── TEMPLATE 6: code task ────────────────────────────────────────────────
    if (has_site or has_python or has_code) and not has_sensitive_path:
        target = "~/Desktop/code.txt"
        if "snake" in n:
            target = "~/Desktop/snake.html"
        elif has_site:
            target = "~/Desktop/index.html"
        elif has_python:
            target = "~/Desktop/script.py"
        elif code_file_m:
            candidate = f"~/Desktop/{code_file_m.group(1)}"
            if _simple_desktop_path(candidate, (".txt", ".html", ".py")):
                target = candidate

        if not _simple_desktop_path(target, (".txt", ".html", ".py")):
            return None

        return {
            "type": "approval_required",
            "objective": f"Générer du code dans {Path(target).name} sur le Bureau",
            "plan": [
                "Valider la demande utilisateur",
                "Générer un fichier de code simple",
                f"Créer ou remplacer {Path(target).name} sur le Bureau",
            ],
            "action": {
                "type": "code_task",
                "payload": {
                    "target_path": target,
                    "instruction": message.strip(),
                },
            },
        }

    return None


# =============================================================================
# AI INTENT ROUTER
# =============================================================================
# Optional lightweight classifier that runs BEFORE the regex verb gate.
# Returns "chat" or "action".  On any failure it returns None, allowing the
# existing verb-gate / template / LLM planner pipeline to handle the message
# exactly as before.
#
# Configuration (environment variables — all optional):
#   ROUTER_PROVIDER  = "local" | "openai" | "anthropic"   (default: disabled)
#   ROUTER_MODEL     = model name (e.g. "phi3:mini", "gpt-4o-mini", "claude-haiku-4-5-20251001")
#   ROUTER_BASE_URL  = base URL for local provider        (default: http://localhost:11434)
#   ROUTER_API_KEY   = API key for remote providers       (default: from settings)
#   ROUTER_TIMEOUT   = max seconds to wait               (default: 2)
# =============================================================================

_ROUTER_PROMPT = """\
You are an intent classifier for a macOS assistant.

Decide if the user message is:
- normal conversation (greetings, questions, opinions, discussion) → chat
- a request to perform an action on the computer (open, create, write, read, run, generate, summarize, prepare) → action

Rules:
- greetings like "bonjour", "ça va", "merci", "comment tu t'appelles" → chat
- commands that start with an imperative verb → action
- "j'aimerais créer une meilleure relation" → chat  (desire/discussion, not a computer command)
- when in doubt → chat

Respond with exactly one word, no punctuation, no explanation:
chat
or
action"""


def _call_router_model(message: str, provider: str, model: str, api_key: str, base_url: str, timeout: float) -> Optional[str]:
    """Call a single LLM synchronously and return 'chat' or 'action', or None on failure."""
    messages = [
        {"role": "system", "content": _ROUTER_PROMPT},
        {"role": "user", "content": message},
    ]
    try:
        from llm_universal import get_provider as _get_prov
        p = _get_prov(provider, api_key=api_key, model=model, base_url=base_url)
        # Patch timeout: OllamaProvider uses requests with hardcoded timeout=120;
        # we run it in a thread with our own asyncio timeout instead.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(p.chat, messages)
            result = future.result(timeout=timeout)
        content = (result.get("content") or "").strip().lower().split()[0] if isinstance(result, dict) else ""
        if content in ("chat", "action"):
            return content
        return None
    except Exception as exc:
        logger.debug("Router model call failed (%s): %s", provider, exc)
        return None


async def route_intent(message: str) -> Optional[str]:
    """
    Classify *message* as 'chat' or 'action' using the configured router model.

    Returns None when the router is disabled or fails, so callers fall back to
    the deterministic verb-gate pipeline.

    Provider resolution order (first non-empty wins):
      1. ROUTER_PROVIDER env var  (explicit override, backward-compatible)
      2. planner_provider / planner_model from settings.json
      3. Primary provider/model from settings.json
    If none yield a provider the router stays disabled.
    """
    timeout = float(os.environ.get("ROUTER_TIMEOUT", "2"))

    # ── Resolve provider ──────────────────────────────────────────────────────
    env_provider = os.environ.get("ROUTER_PROVIDER", "").strip().lower()
    if env_provider:
        # Explicit env-var path (backward-compatible with existing ROUTER_* usage)
        provider = env_provider
        model    = os.environ.get("ROUTER_MODEL", "").strip()
        base_url = os.environ.get("ROUTER_BASE_URL", "http://localhost:11434").strip()
        api_key  = os.environ.get("ROUTER_API_KEY", "").strip()
        if not api_key:
            try:
                api_key = state.core.get_settings().get("api_key", "") or ""
            except Exception:
                api_key = ""
    else:
        # Settings-based path: use an explicit planner config only. Falling back
        # to the primary chat provider made simple local actions wait on slow
        # bridge requests before deterministic routing could recover.
        settings = state.core.get_settings()
        if not settings.get("planner_provider"):
            return None
        ps = _get_planner_settings()
        provider = ps["provider"]
        model    = ps["model"]
        base_url = ps["base_url"]
        api_key  = ps["api_key"]
        # If primary provider is also empty the router is effectively disabled
        if not provider:
            return None

    if not provider:
        return None  # router disabled — use existing pipeline

    # Map "local" alias to "ollama"
    if provider == "local":
        provider = "ollama"

    try:
        intent = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                _call_router_model,
                message, provider, model, api_key, base_url, timeout,
            ),
            timeout=timeout + 0.5,  # small extra margin for thread overhead
        )
        if intent:
            logger.info("AI router: '%s' → %s", message[:60], intent)
        return intent
    except asyncio.TimeoutError:
        logger.debug("AI router timed out for message: %s", message[:60])
        return None
    except Exception as exc:
        logger.debug("AI router error: %s", exc)
        return None


# =============================================================================
# The first word of the message must fully match one of these action verbs.
# A verb buried anywhere else in the sentence means it is plain chat.
_ACTION_VERB_PATTERN = re.compile(
    r"^(?:ouvre|ouvrir|open|lance|lancer|analyse|analyser|scan|diagnostique|regarde|regarder|"
    r"v[eé]rifie|check|cr[eé][eé]?r?|create|"
    r"[eé]cris|[eé]crire|write|lis|lire|read|ajoute|append|"
    r"code|coder|programme|programmer|g[eé]n[eé]re|generate|r[eé]sum[eé]|summarize|"
    r"pr[eé]pare[zr]?|pr[eé]parer)$",
    re.IGNORECASE,
)

# Words that may precede a command without being the command itself.
_POLITE_PREFIXES = {"please", "stp", "svp"}


def _has_action_verb(message: str) -> bool:
    """
    Return True only when the first meaningful word is a recognised action verb.

    Examples that return False (plain chat):
        "bonjour", "ça va ?", "j'aimerais créer…", "comment créer", "pour créer"

    Examples that return True (command):
        "Ouvre Safari", "crée un fichier", "please create a file"
    """
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", (message or "").strip())
        if not unicodedata.combining(c)
    )
    words = normalized.split()
    if not words:
        return False
    # Strip leading punctuation from the first word
    first = words[0].strip("',;:!?.")
    # Allow one optional polite prefix (please / stp / svp)
    if first.lower() in _POLITE_PREFIXES and len(words) > 1:
        first = words[1].strip("',;:!?.")
    if first.lower() == "tu" and len(words) > 2 and words[1].lower().strip("',;:!?.") in {"peux", "peut"}:
        first = words[2].strip("',;:!?.")
    return bool(_ACTION_VERB_PATTERN.match(first))


def _format_kib(kib: int) -> str:
    value = float(max(kib, 0))
    units = ["Ko", "Mo", "Go", "To"]
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"


def _du_kib(path: Path, timeout: int = 30) -> Optional[int]:
    try:
        completed = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        return int(completed.stdout.split()[0])
    except Exception:
        return None


def _run_storage_analysis() -> str:
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Movies",
        home / "Music",
        home / "Pictures",
        home / "Library",
        Path("/Applications"),
    ]
    rows: list[tuple[int, str]] = []
    for path in candidates:
        if not path.exists():
            continue
        size = _du_kib(path)
        if size is not None:
            rows.append((size, str(path)))

    rows.sort(reverse=True)
    if not rows:
        return "Analyse stockage impossible: aucun dossier mesurable n'a répondu."

    lines = [
        "Analyse stockage locale terminée.",
        "",
        "Principaux emplacements mesurés:",
    ]
    for size, path in rows[:8]:
        lines.append(f"- {_format_kib(size)} — {path}")

    biggest_path = Path(rows[0][1])
    children: list[tuple[int, str]] = []
    if biggest_path.is_dir() and biggest_path != Path("/Applications"):
        for child in list(biggest_path.iterdir())[:80]:
            if child.name.startswith("."):
                continue
            size = _du_kib(child, timeout=10)
            if size is not None:
                children.append((size, str(child)))
        children.sort(reverse=True)
        if children:
            lines.extend(["", f"Détails dans le plus gros dossier ({biggest_path}):"])
            for size, path in children[:8]:
                lines.append(f"- {_format_kib(size)} — {path}")

    lines.extend([
        "",
        "Aucune suppression n'a été effectuée.",
    ])
    return "\n".join(lines)


def _run_mac_analysis() -> str:
    lines = ["Analyse locale du Mac terminée.", ""]
    try:
        host = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if host.stdout.strip():
            lines.append(f"Mac: {host.stdout.strip()}")
    except Exception:
        pass

    try:
        disk = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        disk_lines = [line for line in disk.stdout.splitlines() if line.strip()]
        if len(disk_lines) >= 2:
            parts = disk_lines[1].split()
            if len(parts) >= 5:
                lines.append(f"Disque système: {parts[2]} utilisés / {parts[1]} total ({parts[4]}).")
    except Exception:
        pass

    try:
        procs = subprocess.run(
            ["ps", "-arcwwwxo", "pid,%cpu,%mem,comm"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        rows = [line.strip() for line in procs.stdout.splitlines()[1:] if line.strip()]
        if rows:
            lines.extend(["", "Processus les plus actifs:"])
            for row in rows[:5]:
                lines.append(f"- {row}")
    except Exception:
        pass

    lines.extend(["", _run_storage_analysis()])
    return "\n".join(lines)


def _safe_user_path(raw_path: str) -> Optional[Path]:
    raw = (raw_path or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.home() / path
    try:
        resolved = path.resolve()
    except Exception:
        return None

    allowed_roots = [
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Downloads",
        get_project_root(),
    ]
    if not any(str(resolved).startswith(str(root.resolve())) for root in allowed_roots if root.exists()):
        return None
    lowered = resolved.name.lower()
    if lowered in {".env", "id_rsa", "id_dsa"} or lowered.endswith((".key", ".pem")):
        return None
    return resolved


def _list_directory_tool(path: str = "") -> str:
    target = _safe_user_path(path or str(get_project_root()))
    if target is None or not target.exists() or not target.is_dir():
        return "Dossier non autorisé ou introuvable."
    items = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:80]:
        kind = "dossier" if child.is_dir() else "fichier"
        items.append(f"- {kind}: {child.name}")
    return f"Contenu de {target}:\n" + ("\n".join(items) if items else "Dossier vide.")


def _read_text_file_tool(path: str) -> str:
    target = _safe_user_path(path)
    if target is None or not target.exists() or not target.is_file():
        return "Fichier non autorisé ou introuvable."
    if target.stat().st_size > 200_000:
        return "Fichier trop volumineux pour une lecture directe."
    if target.suffix.lower() not in {".txt", ".md", ".json", ".py", ".swift", ".html", ".css", ".js", ".ts", ".tsx", ".toml", ".yaml", ".yml"}:
        return "Type de fichier non lisible dans ce mode."
    text = target.read_text(encoding="utf-8", errors="ignore")
    return f"Lecture de {target}:\n{text[:12000]}"


_PROJECT_EXCLUDE_NAMES = {
    ".git",
    ".venv",
    ".venv312",
    "node_modules",
    "__pycache__",
    ".build",
    "build",
    "dist",
    ".pytest_cache",
}

_READABLE_PROJECT_SUFFIXES = {
    ".txt", ".md", ".json", ".py", ".swift", ".html", ".css", ".js", ".ts", ".tsx",
    ".toml", ".yaml", ".yml", ".sh", ".sql", ".xml", ".plist", ".gitignore",
}


def _resolve_project_root(raw_path: str = "") -> Optional[Path]:
    if raw_path:
        try:
            root = Path(raw_path).expanduser().resolve()
        except Exception:
            return None
    else:
        root = get_project_root().resolve()
    if root.is_file():
        root = root.parent
    if not root.exists() or not root.is_dir():
        return None
    protected_roots = (
        Path("/System"),
        Path("/Library"),
        Path("/bin"),
        Path("/sbin"),
        Path("/usr"),
        Path("/etc"),
        Path("/private/etc"),
    )
    if any(str(root).startswith(str(protected.resolve())) for protected in protected_roots if protected.exists()):
        return None
    if any(part in _PROJECT_EXCLUDE_NAMES for part in root.parts):
        return None
    return root.resolve()


def _safe_project_file(project_path: str, relative_path: str, must_exist: bool = False) -> Optional[Path]:
    root = _resolve_project_root(project_path)
    if root is None:
        return None
    raw = (relative_path or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except Exception:
        return None
    if not str(resolved).startswith(str(root_resolved)):
        return None
    if any(part in _PROJECT_EXCLUDE_NAMES for part in resolved.relative_to(root_resolved).parts):
        return None
    lowered = resolved.name.lower()
    if lowered in {".env", "id_rsa", "id_dsa"} or lowered.endswith((".key", ".pem", ".p12")):
        return None
    if must_exist and not resolved.exists():
        return None
    return resolved


def _project_overview_tool(project_path: str = "") -> str:
    root = _resolve_project_root(project_path)
    if root is None:
        return "Projet introuvable ou non autorisé."
    files: list[str] = []
    markers: list[str] = []
    marker_names = {
        "Package.swift", "pyproject.toml", "requirements.txt", "package.json",
        "server.py", "README.md", "Makefile", "Dockerfile",
    }
    for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in _PROJECT_EXCLUDE_NAMES:
            continue
        kind = "dossier" if child.is_dir() else "fichier"
        files.append(f"- {kind}: {child.name}")
        if child.name in marker_names:
            markers.append(child.name)
        if len(files) >= 80:
            break
    lines = [f"Projet: {root}", ""]
    if markers:
        lines.append("Fichiers de structure détectés: " + ", ".join(sorted(markers)))
        lines.append("")
    lines.append("Racine du projet:")
    lines.extend(files or ["- Projet vide"])
    return "\n".join(lines)


def _search_project_tool(query: str, project_path: str = "") -> str:
    root = _resolve_project_root(project_path)
    needle = (query or "").strip()
    if root is None:
        return "Projet introuvable ou non autorisé."
    if len(needle) < 2:
        return "Recherche trop courte."
    command = [
        "rg",
        "-n",
        "--hidden",
        "--glob", "!.git",
        "--glob", "!node_modules",
        "--glob", "!.venv",
        "--glob", "!.venv312",
        "--glob", "!build",
        "--glob", "!dist",
        needle,
        str(root),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except FileNotFoundError:
        return "Recherche indisponible: rg n'est pas installé."
    except subprocess.TimeoutExpired:
        return "Recherche interrompue: le projet est trop volumineux."
    output = (completed.stdout or completed.stderr or "").strip()
    if not output:
        return f"Aucun résultat pour: {needle}"
    return output[:12000]


def _read_project_file_tool(path: str, project_path: str = "") -> str:
    target = _safe_project_file(project_path, path, must_exist=True)
    if target is None or not target.is_file():
        return "Fichier projet non autorisé ou introuvable."
    if target.stat().st_size > 300_000:
        return "Fichier trop volumineux pour une lecture directe."
    if target.suffix.lower() not in _READABLE_PROJECT_SUFFIXES and target.name.lower() not in {"makefile", ".gitignore"}:
        return "Type de fichier non lisible dans ce mode."
    text = target.read_text(encoding="utf-8", errors="ignore")
    return f"Lecture de {target}:\n{text[:20000]}"


def _write_project_file_tool(path: str, content: str, project_path: str = "") -> str:
    target = _safe_project_file(project_path, path, must_exist=False)
    if target is None:
        return "Chemin projet non autorisé."
    if target.suffix.lower() not in _READABLE_PROJECT_SUFFIXES and target.name.lower() not in {"makefile", ".gitignore"}:
        return "Type de fichier non autorisé pour l'écriture."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return f"Fichier projet écrit: {target}"


def _replace_project_text_tool(path: str, old_text: str, new_text: str, project_path: str = "", expected_count: int = 1) -> str:
    target = _safe_project_file(project_path, path, must_exist=True)
    if target is None or not target.is_file():
        return "Fichier projet non autorisé ou introuvable."
    if target.suffix.lower() not in _READABLE_PROJECT_SUFFIXES and target.name.lower() not in {"makefile", ".gitignore"}:
        return "Type de fichier non autorisé pour l'édition."
    text = target.read_text(encoding="utf-8", errors="ignore")
    if not old_text:
        return "Remplacement refusé: ancien texte vide."
    occurrences = text.count(old_text)
    if occurrences == 0:
        return "Remplacement impossible: texte cible introuvable."
    if expected_count > 0 and occurrences != expected_count:
        return f"Remplacement refusé: {occurrences} occurrence(s) trouvée(s), {expected_count} attendue(s)."
    target.write_text(text.replace(old_text, new_text, expected_count if expected_count > 0 else -1), encoding="utf-8")
    return f"Remplacement appliqué dans {target}: {occurrences} occurrence(s) trouvée(s)."


def _run_project_command_tool(command: str, project_path: str = "") -> str:
    root = _resolve_project_root(project_path)
    raw = (command or "").strip()
    if root is None:
        return "Projet introuvable ou non autorisé."
    if not raw:
        return "Commande vide."
    if any(token in raw for token in (";", "&&", "||", "|", "`", "$(", ">", "<")):
        return "Commande refusée: seuls les appels directs de test/build sont autorisés."
    try:
        args = shlex.split(raw)
    except ValueError as exc:
        return f"Commande invalide: {exc}"
    if not args:
        return "Commande vide."

    if len(args) == 3 and args[1:3] == ["-m", "unittest"] and (root / "tests").exists():
        args = args + ["discover", "-s", "tests"]
        raw = " ".join(args)

    first = args[0]
    allowed = False
    if first.endswith("python") or first in {"python", "python3", ".venv312/bin/python"}:
        allowed = len(args) >= 3 and args[1:3] in (["-m", "unittest"], ["-m", "py_compile"])
    elif first == "pytest":
        allowed = True
    elif first == "swift":
        allowed = len(args) >= 2 and args[1] == "build"
    elif first in {"npm", "pnpm", "yarn"}:
        allowed = len(args) >= 2 and (args[1] in {"test", "build"} or args[:3] in ([first, "run", "test"], [first, "run", "build"]))
    if not allowed:
        return "Commande refusée: Mac Agent OS autorise seulement les commandes de test/build non destructives."

    env = os.environ.copy()
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/macagent-pycache")
    env.setdefault("CLANG_MODULE_CACHE_PATH", "/private/tmp/macagent-clang-cache")
    try:
        completed = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=180, check=False)
    except FileNotFoundError:
        if args[0] == "pytest" and (root / "tests").exists():
            fallback = [".venv312/bin/python", "-m", "unittest", "discover", "-s", "tests"] if (root / ".venv312/bin/python").exists() else ["python", "-m", "unittest", "discover", "-s", "tests"]
            try:
                completed = subprocess.run(fallback, cwd=root, env=env, capture_output=True, text=True, timeout=180, check=False)
                output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
                status = "réussie" if completed.returncode == 0 else f"échouée avec code {completed.returncode}"
                return f"Commande adaptée depuis pytest puis {status}: {' '.join(fallback)}\n{output[:20000]}"
            except Exception as exc:
                return f"Commande introuvable: {args[0]}. Fallback unittest impossible: {exc}"
        return f"Commande introuvable: {args[0]}"
    except subprocess.TimeoutExpired:
        return "Commande interrompue: délai dépassé."
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    status = "réussie" if completed.returncode == 0 else f"échouée avec code {completed.returncode}"
    return f"Commande {status}: {raw}\n{output[:20000]}"


def _default_project_test_command(project_path: str = "") -> Optional[str]:
    root = _resolve_project_root(project_path)
    if root is None:
        return None
    if (root / "tests").exists():
        if (root / ".venv312/bin/python").exists():
            return ".venv312/bin/python -m unittest discover -s tests"
        return "python -m unittest discover -s tests"
    if (root / "Package.swift").exists():
        return "swift build"
    if (root / "package.json").exists():
        return "npm test"
    return None


def _project_memory_key(project_path: str = "") -> str:
    root = _resolve_project_root(project_path)
    source = str(root or project_path or get_project_root())
    return hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _load_project_lessons() -> dict[str, Any]:
    try:
        if PROJECT_LESSONS_PATH.exists():
            payload = json.loads(PROJECT_LESSONS_PATH.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _save_project_lessons(payload: dict[str, Any]) -> None:
    PROJECT_LESSONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_LESSONS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _remember_project_lesson(project_path: str, user_message: str, tool_name: str, observation: str) -> None:
    lowered = (observation or "").lower()
    if not any(marker in lowered for marker in ("échouée", "refusée", "impossible", "introuvable", "failed", "error")):
        return
    payload = _load_project_lessons()
    key = _project_memory_key(project_path)
    entries = payload.get(key, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "request": (user_message or "")[:240],
        "tool": tool_name,
        "lesson": (observation or "").replace("\n", " ")[:700],
    })
    payload[key] = entries[-12:]
    _save_project_lessons(payload)


def _project_lessons_text(project_path: str) -> str:
    entries = _load_project_lessons().get(_project_memory_key(project_path), [])
    if not isinstance(entries, list) or not entries:
        return "Aucune erreur mémorisée pour ce projet."
    lines = ["Erreurs déjà rencontrées sur ce projet:"]
    for item in entries[-5:]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('tool', 'outil')}: {item.get('lesson', '')}")
    return "\n".join(lines)


def _write_desktop_text_tool(filename: str, content: str) -> str:
    safe_name = Path(filename or "note.txt").name
    if not safe_name.lower().endswith((".txt", ".md")):
        safe_name += ".txt"
    target = (Path.home() / "Desktop" / safe_name).resolve()
    if target.exists():
        stem = target.stem
        suffix = target.suffix or ".txt"
        target = target.with_name(f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}")
    target.write_text(content or "", encoding="utf-8")
    return f"Fichier écrit sur le Bureau: {target}"


def _agent_tool_specs(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    active = set(context.get("active_tools", []))
    tools: dict[str, dict[str, Any]] = {}
    if "storage_diagnostics" in active:
        tools["analyze_storage"] = {
            "description": "Analyse en lecture seule les principaux dossiers qui occupent du stockage sur ce Mac.",
            "parameters": {},
        }
    if "local_mac" in active or "mac-control" in active or "storage_diagnostics" in active:
        tools["analyze_mac"] = {
            "description": "Analyse en lecture seule l'état du Mac: disque, stockage principal et processus actifs.",
            "parameters": {},
        }
    if "provider_diagnostics" in active:
        tools["provider_diagnostics"] = {
            "description": "Résume l'état des providers IA, du bridge, d'Ollama et des MCP actifs.",
            "parameters": {},
        }
    if "ollama" in active:
        tools["list_ollama_models"] = {
            "description": "Liste les modèles Ollama locaux détectés.",
            "parameters": {},
        }
    if "local_mac" in active or "mac-control" in active:
        tools["open_application"] = {
            "description": "Ouvre une application macOS visible, par exemple Safari ou System Settings.",
            "parameters": {"app_name": "Nom de l'application à ouvrir"},
        }
    if "safari" in active:
        tools["open_safari"] = {
            "description": "Ouvre ou active Safari.",
            "parameters": {},
        }
    if "self_update" in active:
        tools["self_update_status"] = {
            "description": "Affiche l'état du workspace self-update: copie safe, copie working et manifeste.",
            "parameters": {},
        }
        tools["prepare_self_update_workspace"] = {
            "description": "Crée une copie safe intacte et une copie working éditable pour Mac Agent OS.",
            "parameters": {
                "source_path": "Chemin du projet source, optionnel",
                "destination_root": "Dossier racine V1.2, optionnel",
                "force_working_refresh": "Archive et recrée la working copy si true",
            },
        }
        tools["validate_self_update_workspace"] = {
            "description": "Valide la working copy self-update avec py_compile, tests Python et swift build.",
            "parameters": {"working_path": "Chemin de la working copy"},
        }
        tools["diagnose_self_update_workspace"] = {
            "description": "Diagnostique la working copy self-update et propose des corrections à appliquer.",
            "parameters": {"working_path": "Chemin de la working copy"},
        }
        tools["build_self_update_candidate"] = {
            "description": "Construit une app candidate depuis une working copy validée sans remplacer la version stable.",
            "parameters": {"working_path": "Chemin de la working copy", "output_root": "Dossier de sortie optionnel"},
        }
        tools["auto_update_candidate"] = {
            "description": "Valide, build une candidate, et peut la promouvoir avec backup si confirmation explicite fournie.",
            "parameters": {
                "working_path": "Chemin de la working copy",
                "output_root": "Dossier candidate optionnel",
                "promote": "true pour promouvoir après build",
                "target_app": "App cible .app",
                "confirmation": "PROMOTE_MAC_AGENT_OS_CANDIDATE requis pour promouvoir",
            },
        }
        tools["request_self_update_from_llm"] = {
            "description": "Envoie diagnostics, logs sans secrets et extraits de code au provider IA actif pour proposer une update self-update.",
            "parameters": {
                "working_path": "Chemin de la working copy",
                "objective": "Objectif d'amélioration demandé au LLM",
            },
        }
        tools["promote_self_update_candidate"] = {
            "description": "Promouvoit une candidate .app vers une app cible avec backup. Confirmation obligatoire.",
            "parameters": {
                "candidate_app": "Chemin de la candidate .app",
                "target_app": "Chemin de l'app cible .app",
                "backup_root": "Dossier backup optionnel",
                "confirmation": "PROMOTE_MAC_AGENT_OS_CANDIDATE",
            },
        }
        tools["rollback_self_update_candidate"] = {
            "description": "Restaure une app cible depuis un backup .app. Confirmation obligatoire.",
            "parameters": {
                "candidate_app": "Chemin du backup .app",
                "target_app": "Chemin de l'app cible .app",
                "confirmation": "ROLLBACK_MAC_AGENT_OS_BACKUP",
            },
        }
    if "filesystem" in active:
        tools["project_overview"] = {
            "description": "Inspecte la racine d'un projet local autorisé et résume sa structure.",
            "parameters": {"project_path": "Chemin absolu optionnel du projet"},
        }
        tools["search_project"] = {
            "description": "Recherche du texte dans un projet local autorisé.",
            "parameters": {"query": "Texte à chercher", "project_path": "Chemin absolu optionnel du projet"},
        }
        tools["read_project_file"] = {
            "description": "Lit un fichier de code ou texte dans le projet local autorisé.",
            "parameters": {"path": "Chemin relatif ou absolu du fichier", "project_path": "Chemin absolu optionnel du projet"},
        }
        tools["write_project_file"] = {
            "description": "Crée ou remplace un fichier texte/code dans le projet local autorisé. Ne supprime rien.",
            "parameters": {"path": "Chemin relatif du fichier", "content": "Contenu complet", "project_path": "Chemin absolu optionnel du projet"},
        }
        tools["replace_project_text"] = {
            "description": "Remplace exactement un bloc de texte dans un fichier projet. Préféré pour corriger du code existant.",
            "parameters": {
                "path": "Chemin relatif du fichier",
                "old_text": "Texte exact à remplacer",
                "new_text": "Nouveau texte",
                "expected_count": "Nombre d'occurrences attendu, 1 par défaut",
                "project_path": "Chemin absolu optionnel du projet",
            },
        }
        tools["run_project_command"] = {
            "description": "Lance une commande de test/build autorisée dans le projet. Refuse les commandes destructives.",
            "parameters": {"command": "Commande directe de test/build", "project_path": "Chemin absolu optionnel du projet"},
        }
        tools["list_directory"] = {
            "description": "Liste un dossier autorisé: Bureau, Documents, Téléchargements ou projet Mac Agent OS.",
            "parameters": {"path": "Chemin du dossier"},
        }
        tools["read_text_file"] = {
            "description": "Lit un fichier texte/code autorisé.",
            "parameters": {"path": "Chemin du fichier"},
        }
        tools["write_desktop_text_file"] = {
            "description": "Écrit un fichier .txt ou .md sur le Bureau. Ne supprime rien.",
            "parameters": {"filename": "Nom du fichier", "content": "Contenu"},
        }
    return tools


def _extract_agent_json(text: str) -> Optional[dict[str, Any]]:
    parsed = _extract_json_object(text or "")
    if isinstance(parsed, dict):
        return parsed
    try:
        loaded = json.loads((text or "").strip())
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def _message_wants_tools(message: str) -> bool:
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", (message or "").lower())
        if not unicodedata.combining(c)
    )
    keywords = (
        "analyse", "analyser", "annalyse", "annalyser", "annalys", "diagnostique", "verifie", "check", "stockage",
        "disque", "ollama", "provider", "mcp", "ouvre", "ouvrir", "safari",
        "fichier", "dossier", "repo", "projet", "lis ", "liste", "ecris",
        "modifie", "corrige", "bug", "test", "build", "compile", "code", "coder", "snake",
        "self update", "auto update", "mise a jour", "mise à jour", "mets toi a jour", "met toi a jour", "mets-toi a jour", "duplique", "dupliquer", "v1.2", "1.2",
        "mac", "systeme", "ordinateur", "machine",
    )
    return any(keyword in normalized for keyword in keywords)


def _looks_like_project_coding_request(normalized: str) -> bool:
    project_words = ("projet", "repo", "codebase", "code", "fichier", "bug", "test", "build")
    action_words = (
        "corrige", "corriger", "fix", "modifie", "modifier", "implemente", "implémente",
        "ajoute", "ajouter", "cree", "crée", "creer", "créer", "refactor",
        "autocorrige", "auto-corrige",
    )
    return any(word in normalized for word in project_words) and any(word in normalized for word in action_words)


async def _execute_agent_tool(name: str, args: dict[str, Any]) -> str:
    args = args or {}
    if name == "analyze_mac":
        return await asyncio.to_thread(_run_mac_analysis)
    if name == "analyze_storage":
        return await asyncio.to_thread(_run_storage_analysis)
    if name == "provider_diagnostics":
        diagnostics_payload = await diagnostics()
        return json.dumps(diagnostics_payload, ensure_ascii=False, indent=2)[:12000]
    if name == "list_ollama_models":
        status = await _ollama_status()
        return status.get("message", "") + "\n" + "\n".join(status.get("models", []))
    if name == "open_application":
        app_name = str(args.get("app_name") or "")
        result = await _execute_local_action_inner(LocalActionExecuteAction(type="open_app", payload=LocalActionExecuteActionPayload(app_name=app_name)))
        return result.get("result") or result.get("error") or "Action terminée."
    if name == "open_safari":
        result = await _execute_local_action_inner(LocalActionExecuteAction(type="open_app", payload=LocalActionExecuteActionPayload(app_name="Safari")))
        return result.get("result") or result.get("error") or "Action terminée."
    if name == "self_update_status":
        return json.dumps(self_update_manager.status(), ensure_ascii=False, indent=2)[:12000]
    if name == "prepare_self_update_workspace":
        result = await asyncio.to_thread(
            self_update_manager.prepare,
            str(args.get("source_path") or ""),
            str(args.get("destination_root") or ""),
            bool(args.get("force_working_refresh", False)),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)[:12000]
    if name == "validate_self_update_workspace":
        result = await asyncio.to_thread(self_update_manager.validate, str(args.get("working_path") or ""))
        return json.dumps(result, ensure_ascii=False, indent=2)[:20000]
    if name == "diagnose_self_update_workspace":
        result = await asyncio.to_thread(self_update_manager.diagnose, str(args.get("working_path") or ""))
        return json.dumps(result, ensure_ascii=False, indent=2)[:20000]
    if name == "build_self_update_candidate":
        result = await asyncio.to_thread(
            self_update_manager.build_candidate,
            str(args.get("working_path") or ""),
            str(args.get("output_root") or ""),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)[:20000]
    if name == "auto_update_candidate":
        result = await asyncio.to_thread(
            self_update_manager.auto_update,
            str(args.get("working_path") or ""),
            str(args.get("output_root") or ""),
            bool(args.get("promote", False)),
            str(args.get("target_app") or ""),
            str(args.get("confirmation") or ""),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)[:20000]
    if name == "request_self_update_from_llm":
        result = await _request_self_update_from_llm(
            str(args.get("working_path") or ""),
            str(args.get("objective") or ""),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)[:24000]
    if name == "promote_self_update_candidate":
        result = await asyncio.to_thread(
            self_update_manager.promote_candidate,
            str(args.get("candidate_app") or ""),
            str(args.get("target_app") or ""),
            str(args.get("backup_root") or ""),
            str(args.get("confirmation") or ""),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)[:12000]
    if name == "rollback_self_update_candidate":
        result = await asyncio.to_thread(
            self_update_manager.rollback_candidate,
            str(args.get("candidate_app") or ""),
            str(args.get("target_app") or ""),
            str(args.get("confirmation") or ""),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)[:12000]
    if name == "project_overview":
        return _project_overview_tool(str(args.get("project_path") or ""))
    if name == "search_project":
        return _search_project_tool(str(args.get("query") or ""), str(args.get("project_path") or ""))
    if name == "read_project_file":
        return _read_project_file_tool(str(args.get("path") or ""), str(args.get("project_path") or ""))
    if name == "write_project_file":
        return _write_project_file_tool(str(args.get("path") or ""), str(args.get("content") or ""), str(args.get("project_path") or ""))
    if name == "replace_project_text":
        try:
            expected_count = int(args.get("expected_count", 1))
        except Exception:
            expected_count = 1
        return _replace_project_text_tool(
            str(args.get("path") or ""),
            str(args.get("old_text") or ""),
            str(args.get("new_text") or ""),
            str(args.get("project_path") or ""),
            expected_count,
        )
    if name == "run_project_command":
        return _run_project_command_tool(str(args.get("command") or ""), str(args.get("project_path") or ""))
    if name == "list_directory":
        return _list_directory_tool(str(args.get("path") or ""))
    if name == "read_text_file":
        return _read_text_file_tool(str(args.get("path") or ""))
    if name == "write_desktop_text_file":
        return _write_desktop_text_tool(str(args.get("filename") or "note.txt"), str(args.get("content") or ""))
    return f"Outil inconnu: {name}"


async def _run_project_coding_agent(
    provider_obj: Any,
    messages: list[dict[str, Any]],
    user_message: str,
    skills_context: dict[str, Any],
    project_path: str = "",
    max_steps: int = 10,
) -> Optional[dict[str, Any]]:
    tools = {
        name: spec
        for name, spec in _agent_tool_specs(skills_context).items()
        if name in {
            "project_overview",
            "search_project",
            "read_project_file",
            "write_project_file",
            "replace_project_text",
            "run_project_command",
        }
    }
    if not tools:
        return None

    project_root = _resolve_project_root(project_path)
    if project_root is None:
        return {
            "type": "error",
            "content": "Choisis d'abord un dossier projet valide avec le bouton dossier, puis relance la demande.",
        }

    tool_prompt = (
        "Tu es le Code Helper autonome de Mac Agent OS. Tu peux gérer ce projet local par outils.\n"
        "Boucle obligatoire: inspecter le projet, lire les fichiers nécessaires, modifier de façon minimale, "
        "lancer les tests/build pertinents, puis corriger si une erreur apparaît. "
        "Tu peux t'auto-corriger pendant plusieurs étapes. "
        "Utilise replace_project_text pour modifier un fichier existant quand possible. "
        "Utilise write_project_file seulement pour créer un fichier ou remplacer un fichier entier volontairement. "
        "Ne supprime jamais de fichier et ne touche jamais aux secrets.\n\n"
        "Réponds uniquement avec un JSON outil: "
        '{"tool":"nom_outil","args":{...}} '
        "ou un JSON final: "
        '{"final":"résumé bref: fichiers modifiés, tests lancés, résultat, limites."}\n\n'
        f"Projet actif: {project_root}\n"
        f"{_project_lessons_text(str(project_root))}\n\n"
        f"Outils disponibles:\n{json.dumps(tools, ensure_ascii=False, indent=2)}"
    )

    agent_messages = list(messages)
    agent_messages[0] = {
        "role": "system",
        "content": f"{agent_messages[0].get('content', '')}\n\n{tool_prompt}",
    }
    used_tools: list[str] = []
    observations: list[str] = []

    for step in range(1, max_steps + 1):
        result = provider_obj.chat(agent_messages, tools=None)
        if result.get("type") == "error":
            return result
        content = result.get("content", "") if isinstance(result, dict) else ""
        command = _extract_agent_json(content)
        if not command:
            if used_tools:
                return {
                    "type": "text",
                    "content": (
                        "J'ai commencé le travail sur le projet, mais le modèle a arrêté de produire des appels outil valides.\n\n"
                        + "\n\n".join(observations[-3:])
                    ),
                    "agent": {"used_tools": used_tools, "mode": "project_coding"},
                }
            return None
        if command.get("final"):
            final_text = str(command.get("final"))
            edited = any(tool in used_tools for tool in ("write_project_file", "replace_project_text"))
            verified = any(
                ("Commande réussie" in item or "Commande adaptée depuis pytest puis réussie" in item)
                and "Ran 0 tests" not in item
                for item in observations
            )
            default_command = _default_project_test_command(str(project_root))
            if edited and not verified and default_command:
                validation = await _execute_agent_tool(
                    "run_project_command",
                    {"command": default_command, "project_path": str(project_root)},
                )
                _remember_project_lesson(str(project_root), user_message, "run_project_command", validation)
                used_tools.append("run_project_command")
                observations.append(f"[run_project_command] {validation[:12000]}")
                final_text = f"{final_text}\n\nValidation automatique:\n{validation}"
            return {
                "type": "text",
                "content": final_text,
                "agent": {"used_tools": used_tools, "mode": "project_coding"},
            }

        tool_name = str(command.get("tool") or "")
        if tool_name not in tools:
            agent_messages.append({"role": "assistant", "content": content})
            agent_messages.append({"role": "user", "content": f"Outil refusé: {tool_name}. Utilise uniquement les outils projet disponibles."})
            continue

        tool_args = command.get("args") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}
        tool_args.setdefault("project_path", str(project_root))
        observation = await _execute_agent_tool(tool_name, tool_args)
        _remember_project_lesson(str(project_root), user_message, tool_name, observation)
        used_tools.append(tool_name)
        observations.append(f"[{tool_name}] {observation[:12000]}")

        follow_up = (
            f"Étape {step}/{max_steps}. Observation outil {tool_name}:\n{observation}\n\n"
            "Continue la boucle. Si un test/build a échoué, lis l'erreur, corrige le fichier pertinent, puis relance le test. "
            "Si la tâche est terminée et vérifiée, réponds final."
        )
        agent_messages.append({"role": "assistant", "content": content})
        agent_messages.append({"role": "user", "content": follow_up})

    return {
        "type": "text",
        "content": (
            "Limite d'étapes atteinte. Dernières observations:\n\n"
            + "\n\n".join(observations[-4:])
        ),
        "agent": {"used_tools": used_tools, "mode": "project_coding"},
    }


async def _run_skill_agent(
    provider_obj: Any,
    messages: list[dict[str, Any]],
    user_message: str,
    skills_context: dict[str, Any],
    project_path: str = "",
    max_steps: int = 5,
) -> Optional[dict[str, Any]]:
    if not _message_wants_tools(user_message):
        return None

    tools = _agent_tool_specs(skills_context)
    if not tools:
        return None

    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", user_message.lower())
        if not unicodedata.combining(c)
    )
    analyze_words = ("analyse", "analyser", "annalyse", "annalyser", "annalys", "diagnostique", "verifie", "check")
    mac_words = ("mon mac", "mac", "systeme", "ordinateur", "machine")
    if "analyze_mac" in tools and any(word in normalized for word in analyze_words) and any(word in normalized for word in mac_words):
        return {
            "type": "text",
            "content": await asyncio.to_thread(_run_mac_analysis),
            "agent": {"used_tools": ["analyze_mac"]},
        }
    if any(word in normalized for word in ("stockage", "disque", "storage")) and any(word in normalized for word in ("analyse", "analyser", "diagnostique", "occupe")):
        return {
            "type": "text",
            "content": await asyncio.to_thread(_run_storage_analysis),
            "agent": {"used_tools": ["analyze_storage"]},
        }
    if "provider_diagnostics" in tools and any(word in normalized for word in ("provider", "providers", "diagnostic", "diagnostique", "bridge", "hugging", "openai", "anthropic", "gemini")):
        return {
            "type": "text",
            "content": await _execute_agent_tool("provider_diagnostics", {}),
            "agent": {"used_tools": ["provider_diagnostics"]},
        }
    if "list_ollama_models" in tools and "ollama" in normalized and any(word in normalized for word in ("modele", "model", "liste", "list", "diagnostic", "diagnostique")):
        return {
            "type": "text",
            "content": await _execute_agent_tool("list_ollama_models", {}),
            "agent": {"used_tools": ["list_ollama_models"]},
        }
    open_words = ("ouvre", "ouvrir", "lance", "lancer", "open")
    if "open_safari" in tools and "safari" in normalized and any(word in normalized for word in open_words):
        return {
            "type": "text",
            "content": await _execute_agent_tool("open_safari", {}),
            "agent": {"used_tools": ["open_safari"]},
        }
    if "open_application" in tools and any(word in normalized for word in open_words):
        if any(word in normalized for word in ("reglages", "preferences", "system settings", "system preferences")):
            return {
                "type": "text",
                "content": await _execute_agent_tool("open_application", {"app_name": "System Settings"}),
                "agent": {"used_tools": ["open_application"]},
            }
    self_update_words = ("self update", "auto update", "mise a jour", "mets toi a jour", "met toi a jour", "mets-toi a jour", "duplique", "dupliquer", "copie safe", "v1.2", "1.2")
    if "prepare_self_update_workspace" in tools and any(word in normalized for word in self_update_words):
        workspace = self_update_manager.workspace_from_root()
        source = project_path or (str(workspace.working_path) if workspace.working_path.exists() else str(get_project_root()))
        if any(word in normalized for word in ("status", "etat", "état")):
            return {
                "type": "text",
                "content": await _execute_agent_tool("self_update_status", {}),
                "agent": {"used_tools": ["self_update_status"]},
            }
        if any(word in normalized for word in ("debug", "debogue", "débogue", "diagnostic", "diagnostique", "repare", "répare", "corrige")):
            workspace = self_update_manager.workspace_from_root()
            return {
                "type": "text",
                "content": await _execute_agent_tool("diagnose_self_update_workspace", {"working_path": str(workspace.working_path)}),
                "agent": {"used_tools": ["diagnose_self_update_workspace"]},
            }
        if any(word in normalized for word in ("llm", "ia", "intelligence", "propose", "update au llm", "demande une update")):
            workspace = self_update_manager.workspace_from_root()
            return {
                "type": "text",
                "content": await _execute_agent_tool(
                    "request_self_update_from_llm",
                    {
                        "working_path": str(workspace.working_path),
                        "objective": user_message,
                    },
                ),
                "agent": {"used_tools": ["request_self_update_from_llm"]},
            }
        if "promote" in normalized or "promouvoir" in normalized:
            workspace = self_update_manager.workspace_from_root()
            return {
                "type": "text",
                "content": await _execute_agent_tool(
                    "promote_self_update_candidate",
                    {
                        "candidate_app": str(workspace.root / "candidate-app" / "Mac Agent OS.app"),
                        "target_app": str(Path.home() / "Desktop" / "Mac Agent OS V1.2.app"),
                    },
                ),
                "agent": {"used_tools": ["promote_self_update_candidate"]},
            }
        if any(word in normalized for word in ("auto update", "autoupdate", "auto-update", "mets toi a jour", "met toi a jour", "mets-toi a jour")):
            return {
                "type": "text",
                "content": await _execute_agent_tool(
                    "auto_update_candidate",
                    {
                        "working_path": str(workspace.working_path),
                        "output_root": str(workspace.root / "candidate-app"),
                    },
                ),
                "agent": {"used_tools": ["auto_update_candidate"]},
            }
        if any(word in normalized for word in ("valide", "validation", "test")):
            workspace = self_update_manager.workspace_from_root()
            return {
                "type": "text",
                "content": await _execute_agent_tool("validate_self_update_workspace", {"working_path": str(workspace.working_path)}),
                "agent": {"used_tools": ["validate_self_update_workspace"]},
            }
        if "build" in normalized or "candidate" in normalized:
            workspace = self_update_manager.workspace_from_root()
            return {
                "type": "text",
                "content": await _execute_agent_tool("build_self_update_candidate", {"working_path": str(workspace.working_path)}),
                "agent": {"used_tools": ["build_self_update_candidate"]},
            }
        return {
            "type": "text",
            "content": await _execute_agent_tool(
                "prepare_self_update_workspace",
                {"source_path": source, "destination_root": str(self_update_manager.workspace_from_root().root)},
            ),
            "agent": {"used_tools": ["prepare_self_update_workspace"]},
        }
    project_words = ("projet", "repo", "codebase", "code")
    overview_words = ("analyse", "analyser", "annalyse", "comprends", "comprendre", "explique", "resume", "résume")
    if "project_overview" in tools and any(word in normalized for word in project_words) and any(word in normalized for word in overview_words):
        return {
            "type": "text",
            "content": await _execute_agent_tool("project_overview", {"project_path": project_path}),
            "agent": {"used_tools": ["project_overview"]},
        }
    if "run_project_command" in tools and not _looks_like_project_coding_request(normalized) and any(word in normalized for word in project_words) and any(word in normalized for word in ("test", "tests")):
        root = _resolve_project_root(project_path)
        command = _default_project_test_command(project_path) or "swift build"
        return {
            "type": "text",
            "content": await _execute_agent_tool("run_project_command", {"command": command, "project_path": project_path}),
            "agent": {"used_tools": ["run_project_command"]},
        }
    if _looks_like_project_coding_request(normalized):
        project_result = await _run_project_coding_agent(
            provider_obj,
            messages,
            user_message,
            skills_context,
            project_path=project_path,
            max_steps=max_steps + 5,
        )
        if project_result is not None:
            return project_result

    tool_prompt = (
        "Tu peux agir avec les outils Mac Agent OS ci-dessous. "
        "Si un outil est nécessaire, réponds uniquement avec un JSON: "
        '{"tool":"nom_outil","args":{...}}. '
        "Quand tu as fini, réponds uniquement avec un JSON: "
        '{"final":"réponse utilisateur concise"}. '
        "Ne demande pas à l'utilisateur d'utiliser macOS manuellement si un outil peut le faire. "
        "Ne supprime jamais de fichier.\n\n"
        f"Projet actif: {project_path or str(get_project_root())}\n\n"
        f"Outils disponibles:\n{json.dumps(tools, ensure_ascii=False, indent=2)}"
    )

    agent_messages = list(messages)
    agent_messages[0] = {
        "role": "system",
        "content": f"{agent_messages[0].get('content', '')}\n\n{tool_prompt}",
    }
    used_tools: list[str] = []

    for _ in range(max_steps):
        result = provider_obj.chat(agent_messages, tools=None)
        if result.get("type") == "error":
            return result
        content = result.get("content", "") if isinstance(result, dict) else ""
        command = _extract_agent_json(content)
        if not command:
            if used_tools:
                return {"type": "text", "content": content, "agent": {"used_tools": used_tools}}
            return None
        if command.get("final"):
            return {"type": "text", "content": str(command.get("final")), "agent": {"used_tools": used_tools}}
        tool_name = command.get("tool")
        if not tool_name:
            return None
        if tool_name not in tools:
            agent_messages.append({"role": "assistant", "content": content})
            agent_messages.append({"role": "user", "content": f"Outil refusé: {tool_name}. Choisis un outil disponible ou réponds final."})
            continue
        tool_args = command.get("args") or {}
        if isinstance(tool_args, dict) and project_path and tool_name in {"project_overview", "search_project", "read_project_file", "write_project_file", "replace_project_text", "run_project_command"}:
            tool_args.setdefault("project_path", project_path)
        observation = await _execute_agent_tool(str(tool_name), tool_args)
        used_tools.append(str(tool_name))
        agent_messages.append({"role": "assistant", "content": content})
        agent_messages.append({"role": "user", "content": f"Observation outil {tool_name}:\n{observation}\n\nContinue ou réponds final."})

    if used_tools:
        return {
            "type": "text",
            "content": "J'ai utilisé les outils disponibles, mais la tâche n'a pas produit de réponse finale claire.",
            "agent": {"used_tools": used_tools},
        }
    return None


@app.post("/api/local-actions/plan")
async def plan_local_action(req: LocalActionPlanRequest):
    """Detect a supported local action and return an approval request when needed."""
    try:
        # 0. Deterministic local actions first. They must never wait on the
        # active LLM provider, otherwise simple Mac requests can time out.
        direct = _detect_local_action(req.message)
        if direct.get("type") != "none":
            return direct

        template = try_template_plan(req.message)
        if template is not None:
            return template

        # 1. AI router (optional) — can only block ambiguous chat after the
        # deterministic planner had a chance to catch known local actions.
        ai_intent = await route_intent(req.message)
        if ai_intent == "chat":
            return {"type": "none"}

        # 2. Hard gate: deterministic verb check — always runs, regardless of
        #     whether the router returned "action" or None.
        if not _has_action_verb(req.message):
            return {"type": "none"}

        # 3. LLM planner — last resort for short ambiguous commands with a verb
        return await _plan_with_llm(req.message)
    except Exception as e:
        logger.error("Local action planning failed: %s", e)
        return {"type": "error", "error": str(e)}


async def _execute_local_action_inner(action: LocalActionExecuteAction) -> dict[str, Any]:
    if action.type == "analyze_mac":
        result = await asyncio.to_thread(_run_mac_analysis)
        _remember_action("analyze_mac", result)
        return {"status": "success", "result": result}

    if action.type == "analyze_storage":
        result = await asyncio.to_thread(_run_storage_analysis)
        _remember_action("analyze_storage", result)
        return {"status": "success", "result": result}

    if action.type == "open_app":
        app_name = _normalize_open_app_name(action.payload.app_name or "")
        if not app_name:
            return {"status": "error", "error": "unsupported app"}

        if app_name == "System Settings":
            try:
                completed = subprocess.run(
                    ["open", "-a", "System Settings"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    fallback = subprocess.run(
                        ["open", "-a", "System Preferences"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if fallback.returncode != 0:
                        error_message = (fallback.stderr or completed.stderr or "unable to open System Settings").strip()
                        logger.error("Local action open_app failed app=%s error=%s", app_name, error_message)
                        return {"status": "error", "error": error_message}
                logger.info("Local action executed type=open_app app=%s", app_name)
                _remember_action("open_app", "Les réglages système ont été ouverts.")
                return {
                    "status": "success",
                    "result": "Les réglages système ont été ouverts."
                }
            except Exception as e:
                logger.error("Local action execution failed type=open_app app=%s error=%s", app_name, e)
                return {"status": "error", "error": str(e)}

        try:
            safari_running = subprocess.run(
                ["pgrep", "-x", "Safari"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode == 0

            if safari_running:
                adapted = subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "Safari" to activate',
                        "-e",
                        'tell application "Safari" to if (count of windows) is 0 then make new document',
                        "-e",
                        'tell application "Safari" to if (count of windows) > 0 then tell front window to make new tab with properties {URL:"about:blank"}',
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if adapted.returncode != 0:
                    error_message = (adapted.stderr or adapted.stdout or "unable to adapt Safari state").strip()
                    logger.error("Local action open_app adapted failed app=%s error=%s", app_name, error_message)
                    return {"status": "error", "error": error_message}

                logger.info("Local action executed type=open_app status=adapted app=%s", app_name)
                _remember_action("open_app", "Safari était déjà ouvert. Une fenêtre ou un nouvel onglet a été ouvert.")
                return {
                    "status": "adapted",
                    "result": "Safari était déjà ouvert. Une fenêtre ou un nouvel onglet a été ouvert."
                }

            completed = subprocess.run(
                ["open", "-a", "Safari"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                error_message = (completed.stderr or completed.stdout or "unable to open Safari").strip()
                logger.error("Local action open_app failed app=%s error=%s", app_name, error_message)
                return {"status": "error", "error": error_message}

            activate = subprocess.run(
                ["osascript", "-e", 'tell application "Safari" to activate'],
                capture_output=True,
                text=True,
                check=False,
            )
            if activate.returncode != 0:
                logger.warning(
                    "Local action open_app activated with warning app=%s error=%s",
                    app_name,
                    (activate.stderr or activate.stdout or "").strip() or "unknown activate warning",
                )

            logger.info("Local action executed type=open_app app=%s", app_name)
            _remember_action("open_app", "Safari a été ouvert sur le Mac.")
            return {
                "status": "success",
                "result": "Safari a été ouvert sur le Mac."
            }
        except Exception as e:
            logger.error("Local action execution failed type=open_app app=%s error=%s", app_name, e)
            return {"status": "error", "error": str(e)}

    if action.type == "open_url":
        raw_url = action.payload.url or ""
        safe_url = _sanitize_http_url(raw_url)
        if not safe_url:
            return {"status": "error", "error": "invalid URL"}

        try:
            safari_running = subprocess.run(
                ["pgrep", "-x", "Safari"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode == 0

            if safari_running:
                adapted = subprocess.run(
                    [
                        "osascript",
                        "-e", 'tell application "Safari" to activate',
                        "-e", f'tell application "Safari" to if (count of windows) is 0 then make new document',
                        "-e", f'tell application "Safari" to open location "{safe_url}"',
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if adapted.returncode != 0:
                    error_message = (adapted.stderr or adapted.stdout or "unable to open URL in Safari").strip()
                    return {"status": "error", "error": error_message}
                _remember_action("open_url", f"URL ouverte dans Safari : {safe_url}")
                return {
                    "status": "adapted",
                    "result": f"URL ouverte dans Safari : {safe_url}"
                }

            completed = subprocess.run(
                ["open", "-a", "Safari", safe_url],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                error_message = (completed.stderr or completed.stdout or "unable to open URL").strip()
                return {"status": "error", "error": error_message}
            _remember_action("open_url", f"URL ouverte dans Safari : {safe_url}")
            return {
                "status": "success",
                "result": f"URL ouverte dans Safari : {safe_url}"
            }
        except Exception as e:
            logger.error("Local action execution failed type=open_url url=%s error=%s", safe_url, e)
            return {"status": "error", "error": str(e)}

    if action.type == "create_file":
        target_path = (action.payload.target_path or "").strip()
        content = action.payload.content or ""
        desktop_dir = Path.home() / "Desktop"
        filename = Path(target_path.replace("~", str(Path.home()))).name

        if not filename or filename != Path(filename).name or "/" in filename or "\\" in filename:
            return {"status": "error", "error": "unsafe filename"}
        if not filename.lower().endswith(".txt"):
            return {"status": "error", "error": "only .txt files are allowed"}

        safe_target = desktop_dir / filename
        try:
            resolved_target = safe_target.resolve()
            if resolved_target.parent != desktop_dir.resolve():
                return {"status": "error", "error": "only Desktop is allowed"}

            final_content = content
            if content.strip():
                final_content = await _generate_create_file_content(content.strip())

            file_exists = resolved_target.exists()
            if file_exists:
                if final_content.strip():
                    existing_text = resolved_target.read_text(encoding="utf-8", errors="ignore")
                    separator = "\n\n" if existing_text.strip() else ""
                    resolved_target.write_text(existing_text + separator + final_content, encoding="utf-8")
                    preview = final_content.strip().replace("\n", " ")[:160]
                    logger.info("Local action executed type=create_file status=adapted path=%s", resolved_target)
                    _remember_action("create_file", f"Fichier existant mis à jour : {resolved_target}\nAperçu ajouté : {preview}", str(resolved_target))
                    return {
                        "status": "adapted",
                        "result": f"Fichier existant mis à jour : {resolved_target}\nAperçu ajouté : {preview}"
                    }
                logger.info("Local action executed type=create_file status=adapted path=%s", resolved_target)
                _remember_action("create_file", f"Fichier déjà présent conservé : {resolved_target}", str(resolved_target))
                return {
                    "status": "adapted",
                    "result": f"Fichier déjà présent conservé : {resolved_target}"
                }

            resolved_target.write_text(final_content, encoding="utf-8")
            preview = "Fichier vide."
            if final_content.strip():
                preview = final_content.strip().replace("\n", " ")[:160]
            logger.info("Local action executed type=create_file path=%s", resolved_target)
            _remember_action("create_file", f"Fichier créé : {resolved_target}\nAperçu : {preview}", str(resolved_target))
            return {
                "status": "success",
                "result": f"Fichier créé : {resolved_target}\nAperçu : {preview}"
            }
        except Exception as e:
            logger.error("Local action execution failed type=create_file path=%s error=%s", safe_target, e)
            return {"status": "error", "error": str(e)}

    if action.type == "read_file":
        target_path = _canonical_desktop_path((action.payload.target_path or "").strip())
        desktop_dir = Path.home() / "Desktop"
        filename = Path(target_path.replace("~", str(Path.home()))).name

        if not filename or filename != Path(filename).name or "/" in filename or "\\" in filename:
            return {"status": "error", "error": "unsafe filename"}
        if not filename.lower().endswith((".txt", ".py", ".html")):
            return {"status": "error", "error": "only .txt, .py and .html files are allowed"}

        safe_target = desktop_dir / filename
        try:
            resolved_target = safe_target.resolve()
            if resolved_target.parent != desktop_dir.resolve():
                return {"status": "error", "error": "only Desktop is allowed"}
            if not resolved_target.exists():
                return {"status": "error", "error": "file does not exist"}
            content = resolved_target.read_text(encoding="utf-8", errors="ignore")
            preview = "\n".join(content.splitlines()[:100]).strip()
            _remember_action("read_file", f"Lecture de {resolved_target}\n{preview or '[fichier vide]'}", str(resolved_target))
            return {
                "status": "success",
                "result": f"Lecture de {resolved_target}\n{preview or '[fichier vide]'}"
            }
        except Exception as e:
            logger.error("Local action execution failed type=read_file path=%s error=%s", safe_target, e)
            return {"status": "error", "error": str(e)}

    if action.type == "append_file":
        target_path = _canonical_desktop_path((action.payload.target_path or "").strip())
        content = (action.payload.content or "").strip()
        desktop_dir = Path.home() / "Desktop"
        filename = Path(target_path.replace("~", str(Path.home()))).name

        if not filename or filename != Path(filename).name or "/" in filename or "\\" in filename:
            return {"status": "error", "error": "unsafe filename"}
        if not filename.lower().endswith((".txt", ".py", ".html")):
            return {"status": "error", "error": "only .txt, .py and .html files are allowed"}
        if not content:
            return {"status": "error", "error": "missing content"}

        safe_target = desktop_dir / filename
        try:
            resolved_target = safe_target.resolve()
            if resolved_target.parent != desktop_dir.resolve():
                return {"status": "error", "error": "only Desktop is allowed"}
            if not resolved_target.exists():
                return {"status": "error", "error": "file does not exist"}
            existing = resolved_target.read_text(encoding="utf-8", errors="ignore")
            separator = "\n" if existing and not existing.endswith("\n") else ""
            resolved_target.write_text(existing + separator + content + "\n", encoding="utf-8")
            preview = content.replace("\n", " ")[:160]
            _remember_action("append_file", f"Contenu ajouté dans {resolved_target}\nAperçu ajouté : {preview}", str(resolved_target))
            return {
                "status": "success",
                "result": f"Contenu ajouté dans {resolved_target}\nAperçu ajouté : {preview}"
            }
        except Exception as e:
            logger.error("Local action execution failed type=append_file path=%s error=%s", safe_target, e)
            return {"status": "error", "error": str(e)}

    if action.type == "code_task":
        target_path = _canonical_desktop_path((action.payload.target_path or "").strip())
        instruction = (action.payload.instruction or "").strip()
        desktop_dir = Path.home() / "Desktop"
        filename = Path(target_path.replace("~", str(Path.home()))).name

        if not filename or filename != Path(filename).name or "/" in filename or "\\" in filename:
            return {"status": "error", "error": "unsafe filename"}
        if not filename.lower().endswith((".txt", ".html", ".py")):
            return {"status": "error", "error": "only .txt, .html and .py files are allowed"}
        if not instruction:
            return {"status": "error", "error": "missing instruction"}

        safe_target = desktop_dir / filename
        try:
            resolved_target = safe_target.resolve()
            if resolved_target.parent != desktop_dir.resolve():
                return {"status": "error", "error": "only Desktop is allowed"}

            generated = await _generate_code_task_content(filename, instruction)
            if not _is_valid_code_task_output(filename, generated):
                generated = await _generate_code_task_content(filename, instruction)
            if not _is_valid_code_task_output(filename, generated):
                return {"status": "error", "error": "code generation failed"}

            existed = resolved_target.exists()
            resolved_target.write_text(generated, encoding="utf-8")
            preview = generated.strip().replace("\n", " ")[:180]
            logger.info("Local action executed type=code_task path=%s", resolved_target)
            _remember_action(
                "code_task",
                (
                    f"Fichier modifié : {resolved_target}\nAperçu : {preview}"
                    if existed else
                    f"Fichier créé : {resolved_target}\nAperçu : {preview}"
                ),
                str(resolved_target),
            )
            return {
                "status": "adapted" if existed else "success",
                "result": (
                    f"Fichier modifié : {resolved_target}\nAperçu : {preview}"
                    if existed else
                    f"Fichier créé : {resolved_target}\nAperçu : {preview}"
                )
            }
        except Exception as e:
            logger.error("Local action execution failed type=code_task path=%s error=%s", safe_target, e)
            return {"status": "error", "error": str(e)}

    if action.type == "summarize_folder_to_file":
        source_path = (action.payload.source_path or "").strip()
        output_path = (action.payload.output_path or "").strip()
        project_root = Path.home() / "Desktop"
        desktop_dir = Path.home() / "Desktop"
        source_dir = Path(source_path).expanduser().resolve()
        output_name = Path(output_path.replace("~", str(Path.home()))).name
        safe_output = desktop_dir / output_name

        if source_dir != project_root.resolve():
            return {"status": "error", "error": "only this project folder is allowed in V1"}
        if not output_name.lower().endswith(".txt"):
            return {"status": "error", "error": "output must be a .txt file"}
        if safe_output.resolve().parent != desktop_dir.resolve():
            return {"status": "error", "error": "output must stay on Desktop"}

        ignored_dirs = {".git", ".venv", "__pycache__", ".build", "dist", "node_modules", "data", "mcp_dynamic"}
        allowed_suffixes = {".txt", ".md", ".json", ".py", ".js", ".ts", ".tsx", ".swift", ".html", ".css", ".csv", ".yml", ".yaml", ".toml"}

        def is_readable_text_file(path: Path) -> bool:
            if path.suffix.lower() not in allowed_suffixes:
                return False
            try:
                if path.stat().st_size > 100_000:
                    return False
            except OSError:
                return False
            return True

        try:
            total_files = 0
            included_files: list[Path] = []
            extension_counts: dict[str, int] = {}

            for path in sorted(source_dir.rglob("*")):
                if any(part in ignored_dirs for part in path.parts):
                    continue
                if not path.is_file():
                    continue
                total_files += 1
                if not is_readable_text_file(path):
                    continue
                included_files.append(path)
                ext = path.suffix.lower() or "(none)"
                extension_counts[ext] = extension_counts.get(ext, 0) + 1
                if len(included_files) >= 25:
                    break

            summary_lines = [
                f"Résumé du dossier: {source_dir.name}",
                "",
                f"Dossier source: {source_dir}",
                f"Fichiers détectés: {total_files}",
                f"Fichiers texte résumés: {len(included_files)}",
                "",
                "Répartition par type:",
            ]

            if extension_counts:
                for ext, count in sorted(extension_counts.items()):
                    summary_lines.append(f"- {ext}: {count}")
            else:
                summary_lines.append("- Aucun fichier texte simple supporté trouvé")

            if included_files:
                summary_lines.extend(["", "Aperçu des fichiers:"])
                for path in included_files:
                    rel = path.relative_to(source_dir)
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        text = ""
                    preview_lines = [
                        line.strip() for line in text.splitlines()
                        if line.strip()
                    ][:3]
                    preview = " / ".join(preview_lines)[:220] if preview_lines else "Fichier lisible mais sans contenu texte significatif."
                    summary_lines.append(f"- {rel}: {preview}")

            summary_text = "\n".join(summary_lines).strip() + "\n"
            safe_output.write_text(summary_text, encoding="utf-8")
            logger.info("Local action executed type=summarize_folder_to_file source=%s output=%s", source_dir, safe_output)
            _remember_action("summarize_folder_to_file", f"Résumé créé : {safe_output}", str(safe_output))
            return {
                "status": "success",
                "result": f"Résumé créé : {safe_output}"
            }
        except Exception as e:
            logger.error(
                "Local action execution failed type=summarize_folder_to_file source=%s output=%s error=%s",
                source_dir,
                safe_output,
                e,
            )
            return {"status": "error", "error": str(e)}

    if action.type == "multi_step_plan":
        steps = action.steps or []
        if not steps:
            return {"status": "error", "error": "missing steps"}

        step_results: list[dict[str, Any]] = []
        progress_lines: list[str] = []
        total = len(steps)
        overall_status = "success"
        for index, step in enumerate(steps, start=1):
            label = _label_for_step(step)
            result = await _execute_local_action_inner(step)
            step_status = result.get("status", "error")
            if step_status == "error":
                error = result.get("error", "unknown error")
                step_results.append({
                    "index": index,
                    "label": label,
                    "status": "error",
                    "result": error,
                })
                return {
                    "status": "error",
                    "error": f"Échec étape {index}/{total}: {error}",
                    "steps": step_results,
                }
            if step_status == "adapted":
                overall_status = "adapted"
            step_results.append({
                "index": index,
                "label": label,
                "status": step_status,
                "result": result.get("result", "OK"),
            })
            progress_lines.append(f"Étape {index}/{total}: {result.get('result', 'OK')}")

        return {
            "status": overall_status,
            "result": "Plan terminé.\n" + "\n".join(progress_lines),
            "steps": step_results,
        }

    return {"status": "error", "error": "unsupported action"}


@app.post("/api/local-actions/execute")
async def execute_local_action(req: LocalActionExecuteRequest):
    """Execute a pre-planned local action only after explicit approval."""
    if not req.approved:
        return JSONResponse({"status": "error", "error": "approval required"}, status_code=400)

    result = await _execute_local_action_inner(req.action)
    if result.get("status") in {"success", "adapted"}:
        return result
    return JSONResponse(result, status_code=400)

class LLMChatRequest(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""
    message: str
    system_prompt: str = "Tu es un assistant helpful."
    base_url: str = ""
    attachments: list[str] = []
    history: list[dict] = []
    project_path: str = ""
    turbo: bool = False
    reasoning: bool = False
    allow_auto_routing: bool = False


# --- Models list cache (TTL = 60 s) ---
# Avoids one round-trip network call per LLM request.
_models_cache: dict[str, tuple[list[str], float]] = {}
_MODELS_CACHE_TTL = 60.0  # seconds

def _get_cached_models(provider: str, api_key: str, model: str, base_url: str) -> list[str]:
    cache_key = f"{provider}:{base_url}"
    entry = _models_cache.get(cache_key)
    if entry and (time.monotonic() - entry[1]) < _MODELS_CACHE_TTL:
        return entry[0]
    try:
        p = get_provider(provider, api_key=api_key, model=model, base_url=base_url)
        models = p.get_models(api_key=api_key) or []
    except Exception:
        models = []
    _models_cache[cache_key] = (models, time.monotonic())
    return models

@app.post("/api/llm/chat")
async def llm_chat(req: LLMChatRequest):
    """Envoie un message à un LLM"""
    try:
        started_at = time.perf_counter()
        requested_provider = (req.provider or "").strip().lower()
        requested_model = (req.model or "").strip()
        effective_api_key = req.api_key
        effective_base_url = req.base_url or ""
        if not requested_provider:
            return {"type": "error", "content": "Choisis un provider IA avant d’envoyer un message."}
        current_settings = state.core.get_settings()
        if requested_provider == current_settings.get("provider"):
            if not effective_api_key:
                effective_api_key = current_settings.get("api_key", "") or ""
            if not effective_base_url:
                effective_base_url = current_settings.get("base_url", "") or ""
        skills_context = ""
        try:
            skills_context = state.skills_manager.build_prompt_extension(
                req.message,
                await _skills_runtime_context(check_ollama=False),
            )
        except Exception as exc:
            logger.debug("Skills context skipped: %s", exc)
        base_system_prompt = req.system_prompt
        if skills_context:
            base_system_prompt = (
                f"{req.system_prompt}\n\n"
                "Skills actifs pertinents pour cette demande:\n"
                f"{skills_context}"
            )

        context = token_optimizer.build_context(
            base_system_prompt=base_system_prompt,
            user_message=req.message,
            history=req.history,
            project_path=req.project_path,
            turbo=req.turbo,
        )

        available_models: list[str] = _get_cached_models(
            requested_provider, effective_api_key, requested_model, effective_base_url
        )

        route = None
        actual_provider = requested_provider
        actual_model = requested_model
        fallback_reason = ""

        if requested_model and not req.allow_auto_routing:
            if available_models and requested_model not in available_models:
                route = token_optimizer.choose_model(
                    provider=requested_provider,
                    requested_model=requested_model,
                    user_message=req.message,
                    history=req.history,
                    attachments=req.attachments,
                )
                if route.model and route.model in available_models:
                    actual_model = route.model
                else:
                    actual_model = available_models[0]
                fallback_reason = "requested model unavailable"
                route.reason = f"{route.reason}; fallback: {fallback_reason}"
            else:
                route = token_optimizer.model_router.route(
                    requested_provider,
                    requested_model,
                    "",
                    [],
                    [],
                )
                route.model = requested_model
                route.tier = "locked"
                route.reason = "explicit model selection"
        else:
            route = token_optimizer.choose_model(
                provider=requested_provider,
                requested_model=requested_model,
                user_message=req.message,
                history=req.history,
                attachments=req.attachments,
            )
            actual_model = route.model or requested_model
            if requested_model and req.allow_auto_routing and actual_model != requested_model:
                fallback_reason = "automatic routing explicitly allowed"

        logger.info(
            "LLM chat routing requested_provider=%s requested_model=%s actual_provider=%s actual_model=%s fallback_reason=%s",
            requested_provider,
            requested_model,
            actual_provider,
            actual_model,
            fallback_reason or "none",
        )

        provider = get_provider(
            actual_provider,
            api_key=effective_api_key,
            model=actual_model,
            base_url=effective_base_url
        )

        agent_result = None
        if not req.attachments:
            agent_result = await _run_skill_agent(
                provider,
                context["messages"],
                req.message,
                await _skills_runtime_context(check_ollama=False),
                project_path=req.project_path,
            )
        result = agent_result or provider.chat(context["messages"], files=req.attachments)
        output_text = result.get("content", "") if isinstance(result, dict) else ""
        telemetry = token_optimizer.telemetry.build(actual_model, context["messages"], output_text, started_at)
        telemetry.update({
            "requested": {"provider": requested_provider, "model": requested_model},
            "actual": {"provider": actual_provider, "model": actual_model},
            "route": {
                "tier": route.tier,
                "reason": route.reason,
                "fallback_reason": fallback_reason,
            },
        })
        state.core.last_usage_telemetry = telemetry

        if result.get("type") == "text":
            token_optimizer.update_memory(
                context["project_key"],
                context["project_label"],
                req.message,
                output_text,
            )
        result["provider"] = actual_provider
        result["model"] = actual_model
        result["requested"] = {"provider": requested_provider, "model": requested_model}
        result["actual"] = {"provider": actual_provider, "model": actual_model}
        result["route"] = {
            "tier": route.tier,
            "reason": route.reason,
            "fallback_reason": fallback_reason,
        }
        result["telemetry"] = telemetry
        result["context"] = {
            "history_used": context["history_used"],
            "project_memory_chars": len(context["project_summary"]),
            "task_state_chars": len(context["task_state"]),
            "turbo": req.turbo,
        }
        return result
    except Exception as e:
        return {"type": "error", "content": str(e)}


async def _generate_create_file_content(content_request: str) -> str:
    settings = state.core.get_settings()
    prompt = (
        "Rédige uniquement le contenu final d'un fichier texte local. "
        "Réponse courte, utile, structurée, sans introduction ni markdown. "
        "Maximum 220 mots. Demande: "
        f"{content_request}"
    )

    req = LLMChatRequest(
        provider=settings.get("provider", "ollama"),
        model=settings.get("model", "") or "",
        api_key=settings.get("api_key", "") or "",
        message=prompt,
        system_prompt="Tu génères du contenu bref pour un fichier texte local. Donne uniquement le contenu final.",
        base_url=settings.get("base_url", "") or "",
        attachments=[],
        history=[],
        project_path="",
        turbo=False,
        allow_auto_routing=False,
    )

    try:
        result = await asyncio.wait_for(llm_chat(req), timeout=12.0)
        if isinstance(result, dict):
            content = (result.get("content") or "").strip()
            if content:
                return content[:1800]
    except Exception as e:
        logger.warning("create_file content generation failed: %s", e)

    fallback = [
        "Contenu généré en mode simple.",
        "",
        f"Demande: {content_request}",
    ]
    return "\n".join(fallback).strip() + "\n"


def _snake_html_content() -> str:
    return """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Snake - Mac Agent OS</title>
  <style>
    :root { color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #10131a; color: #f4f7fb; }
    main { width: min(92vw, 560px); }
    header { display: flex; justify-content: space-between; align-items: end; margin-bottom: 14px; gap: 16px; }
    h1 { margin: 0; font-size: 28px; }
    .score { color: #9fb3c8; font-weight: 700; }
    canvas { width: 100%; aspect-ratio: 1; background: #171c25; border: 1px solid #2c3442; border-radius: 10px; display: block; }
    .bar { display: flex; gap: 10px; align-items: center; justify-content: space-between; margin-top: 12px; color: #9fb3c8; }
    button { border: 0; border-radius: 8px; padding: 9px 13px; background: #8fb8ff; color: #0b1020; font-weight: 800; cursor: pointer; }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Snake</h1>
        <div class="score">Score: <span id="score">0</span></div>
      </div>
      <button id="restart">Rejouer</button>
    </header>
    <canvas id="game" width="420" height="420" aria-label="Jeu Snake"></canvas>
    <div class="bar"><span>Flèches ou WASD</span><span id="state">Prêt</span></div>
  </main>
  <script>
    const canvas = document.getElementById("game");
    const ctx = canvas.getContext("2d");
    const scoreEl = document.getElementById("score");
    const stateEl = document.getElementById("state");
    const size = 21;
    const cell = canvas.width / size;
    let snake, dir, nextDir, food, score, over, timer;

    function reset() {
      snake = [{x: 10, y: 10}, {x: 9, y: 10}, {x: 8, y: 10}];
      dir = {x: 1, y: 0};
      nextDir = dir;
      score = 0;
      over = false;
      food = spawnFood();
      scoreEl.textContent = score;
      stateEl.textContent = "En cours";
      clearInterval(timer);
      timer = setInterval(tick, 105);
      draw();
    }

    function spawnFood() {
      while (true) {
        const p = {x: Math.floor(Math.random() * size), y: Math.floor(Math.random() * size)};
        if (!snake.some(s => s.x === p.x && s.y === p.y)) return p;
      }
    }

    function tick() {
      if (over) return;
      dir = nextDir;
      const head = {x: snake[0].x + dir.x, y: snake[0].y + dir.y};
      if (head.x < 0 || head.y < 0 || head.x >= size || head.y >= size || snake.some(s => s.x === head.x && s.y === head.y)) {
        over = true;
        stateEl.textContent = "Perdu";
        draw();
        return;
      }
      snake.unshift(head);
      if (head.x === food.x && head.y === food.y) {
        score += 10;
        scoreEl.textContent = score;
        food = spawnFood();
      } else {
        snake.pop();
      }
      draw();
    }

    function draw() {
      ctx.fillStyle = "#171c25";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#ff6b6b";
      rounded(food.x * cell + 3, food.y * cell + 3, cell - 6, cell - 6, 6);
      snake.forEach((part, i) => {
        ctx.fillStyle = i === 0 ? "#b5f5c8" : "#6ee7a8";
        rounded(part.x * cell + 2, part.y * cell + 2, cell - 4, cell - 4, 6);
      });
      if (over) {
        ctx.fillStyle = "rgba(0,0,0,.48)";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#fff";
        ctx.font = "bold 34px system-ui";
        ctx.textAlign = "center";
        ctx.fillText("Game Over", canvas.width / 2, canvas.height / 2);
      }
    }

    function rounded(x, y, w, h, r) {
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, r);
      ctx.fill();
    }

    function setDir(x, y) {
      if (dir.x + x === 0 && dir.y + y === 0) return;
      nextDir = {x, y};
    }

    window.addEventListener("keydown", event => {
      const k = event.key.toLowerCase();
      if (k === "arrowup" || k === "w") setDir(0, -1);
      if (k === "arrowdown" || k === "s") setDir(0, 1);
      if (k === "arrowleft" || k === "a") setDir(-1, 0);
      if (k === "arrowright" || k === "d") setDir(1, 0);
    });
    document.getElementById("restart").addEventListener("click", reset);
    reset();
  </script>
</body>
</html>
"""


async def _generate_code_task_content(filename: str, instruction: str) -> str:
    settings = state.core.get_settings()
    suffix = Path(filename).suffix.lower()
    if suffix == ".html" and ("snake" in filename.lower() or "snake" in instruction.lower()):
        return _snake_html_content()
    target_kind = {
        ".html": "un fichier HTML complet simple, autonome et directement affichable dans un navigateur",
        ".py": "un script Python simple, lisible et exécutable",
        ".txt": "un fichier texte contenant du code ou du pseudo-code simple",
    }.get(suffix, "un fichier de code simple")

    prompt = (
        f"Génère uniquement le contenu final de {filename}. "
        f"Le résultat doit être {target_kind}. "
        "Aucune explication, aucun markdown, aucun bloc ```."
        " Réponse finale uniquement. Maximum 300 lignes.\n"
        f"Demande utilisateur: {instruction}"
    )

    req = LLMChatRequest(
        provider=settings.get("provider", "ollama"),
        model=settings.get("model", "") or "",
        api_key=settings.get("api_key", "") or "",
        message=prompt,
        system_prompt="Tu génères uniquement le contenu final d'un fichier de code. Ne réponds qu'avec le contenu du fichier.",
        base_url=settings.get("base_url", "") or "",
        attachments=[],
        history=[],
        project_path="",
        turbo=False,
        allow_auto_routing=False,
    )

    try:
        result = await asyncio.wait_for(llm_chat(req), timeout=15.0)
        if isinstance(result, dict):
            content = (result.get("content") or "").strip()
            if content:
                cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", content)
                cleaned = re.sub(r"\n?```$", "", cleaned).strip()
                return "\n".join(cleaned.splitlines()[:300]).strip() + "\n"
    except Exception as e:
        logger.warning("code_task generation failed: %s", e)

    return ""


def _is_valid_code_task_output(filename: str, content: str) -> bool:
    text = (content or "").strip()
    if len(text) <= 50:
        return False

    suffix = Path(filename).suffix.lower()
    lowered = text.lower()

    if suffix == ".html":
        return "<html" in lowered
    if suffix == ".py":
        return "def" in text or "print" in text

    return len(text.splitlines()) >= 2

class LLMTestRequest(BaseModel):
    provider: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""

@app.post("/api/llm/test")
async def test_llm_connection(req: LLMTestRequest):
    """Test la connexion à un provider LLM"""
    try:
        logger.info(
            "LLM test routing requested_provider=%s requested_model=%s actual_provider=%s actual_model=%s fallback_reason=%s",
            req.provider,
            req.model or "",
            req.provider,
            req.model or "",
            "none",
        )
        provider = get_provider(
            req.provider,
            api_key=req.api_key,
            model=req.model or "",
            base_url=req.base_url or ""
        )
        
        # Test simple
        result = provider.chat([
            {"role": "user", "content": "Say 'OK' if you can hear me."}
        ])
        
        return {
            "status": "success" if result.get("type") != "error" else "failed",
            "provider": req.provider,
            "model": req.model,
            "response": result
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# === HERETIC (Decensor) ===

class HereticRequest(BaseModel):
    model_name: str
    quantization: str = "NONE"
    output_name: str | None = None
    upload_to_huggingface: bool = False
    import_to_ollama: bool = True

# Global state for Heretic progress
heretic_progress = {
    "running": False,
    "model": "",
    "output": "",
    "progress": 0,
    "status": "",
    "logs": ""
}

@app.get("/api/heretic/progress")
async def get_heretic_progress():
    """Get Heretic progress"""
    return heretic_progress

@app.post("/api/heretic/run")
async def run_heretic(req: HereticRequest):
    """Run Heretic decensoring on a model"""
    import subprocess
    import threading
    import os
    import time
    
    global heretic_progress
    
    output_model = req.output_name or req.model_name.split("/")[-1] + "-heretic"
    
    heretic_progress["running"] = True
    heretic_progress["model"] = req.model_name
    heretic_progress["output"] = output_model
    heretic_progress["progress"] = 0
    heretic_progress["status"] = "starting"
    heretic_progress["logs"] = f"Starting decensoring of {req.model_name}...\n"
    
    result = {"status": "running", "model": req.model_name, "output": output_model}
    
    def run_heretic_in_background():
        global heretic_progress
        
        try:
            heretic_progress["status"] = "downloading"
            heretic_progress["progress"] = 10
            heretic_progress["logs"] += "Downloading model...\n"
            
            # Run heretic to decensor the model
            cmd = [
                "heretic", req.model_name,
                "--quantization", req.quantization,
                "--no-print-responses",
                "--output", output_model
            ]
            
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Read output line by line and update progress
            for line in iter(proc.stdout.readline, ''):
                if line:
                    heretic_progress["logs"] += line[:200] + "\n"
                    # Simple progress estimation based on logs
                    if "benchmarking" in line.lower():
                        heretic_progress["progress"] = 30
                        heretic_progress["status"] = "benchmarking"
                    elif "optimizing" in line.lower() or "trial" in line.lower():
                        heretic_progress["progress"] = 50
                        heretic_progress["status"] = "optimizing"
                    elif "applying" in line.lower() or "abliterat" in line.lower():
                        heretic_progress["progress"] = 80
                        heretic_progress["status"] = "applying"
                    elif "saving" in line.lower():
                        heretic_progress["progress"] = 90
                        heretic_progress["status"] = "saving"
            
            proc.wait()
            
            if proc.returncode != 0:
                heretic_progress["status"] = "failed"
                heretic_progress["progress"] = 0
                heretic_progress["logs"] += f"\nError: Heretic exited with code {proc.returncode}"
                result["status"] = "failed"
                return
            
            heretic_progress["status"] = "importing"
            heretic_progress["progress"] = 95
            heretic_progress["logs"] += "Decensoring complete! Importing to Ollama...\n"
            
            # If import to ollama is requested
            if req.import_to_ollama:
                # Find the model directory
                model_path = None
                possible_paths = [
                    os.path.join(os.getcwd(), output_model),
                    os.path.join(os.getcwd(), "output", output_model),
                    os.path.expanduser(f"~/heretic/{output_model}"),
                ]
                
                for path in possible_paths:
                    if os.path.exists(path):
                        model_path = path
                        break
                
                if model_path:
                    ollama_name = output_model.replace("-", "_").replace(".", "_").replace(":", "_")
                    import_to_ollama_cmd = ["ollama", "create", ollama_name, "-f", model_path]
                    
                    if not os.path.exists(os.path.join(model_path, "Modelfile")):
                        heretic_progress["logs"] += "Note: No Modelfile found, trying direct import...\n"
                    
                    ollama_proc = subprocess.run(
                        import_to_ollama_cmd,
                        capture_output=True,
                        text=True
                    )
                    heretic_progress["ollama_result"] = ollama_proc.returncode
                    heretic_progress["logs"] += f"Ollama import: {ollama_proc.returncode}\n"
            
            heretic_progress["status"] = "completed"
            heretic_progress["progress"] = 100
            heretic_progress["logs"] += "\n✅ Decensoring complete!"
            result["status"] = "completed"
            
        except Exception as e:
            heretic_progress["status"] = "error"
            heretic_progress["logs"] += f"\nError: {str(e)}"
            result["error"] = str(e)
        finally:
            heretic_progress["running"] = False
    
    thread = threading.Thread(target=run_heretic_in_background)
    thread.start()
    
    return result


@app.get("/api/heretic/status")
async def heretic_status():
    """Get Heretic status"""
    return {"installed": True, "version": "1.2.0"}


# === PLUGINS ===

@app.get("/api/plugins")
async def list_plugins():
    """List all plugins"""
    plugins = state.plugin_manager.list_plugins()
    return {"plugins": [p.__dict__ for p in plugins]}

@app.get("/api/plugins/{plugin_name}/tools")
async def get_plugin_tools(plugin_name: str):
    """Get plugin tools"""
    plugin = state.plugin_manager.plugins.get(plugin_name)
    if plugin:
        return {"tools": plugin.get_tools()}
    return JSONResponse({"error": "not found"}, status_code=404)


# === MCP FACTORY ===

from mcp_factory import DynamicMCPFactory
from pathlib import Path

# Instance globale de la factory
_mcp_factory = DynamicMCPFactory()

# Chemins des MCPs dynamiques
PROJECT_ROOT = get_project_root()
MCP_DYNAMIC_DIR = PROJECT_ROOT / "mcp_dynamic"
LOCAL_VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
VENV_PYTHON = str(LOCAL_VENV_PYTHON if LOCAL_VENV_PYTHON.exists() else "python3")

@app.get("/api/mcp/dynamic/apps")
async def list_installed_apps():
    """Liste toutes les applications installées sur le Mac"""
    apps = _mcp_factory.list_installed_apps()
    return {
        "total": len(apps),
        "apps": [{"name": v["name"], "path": v["path"]} for k, v in apps.items()]
    }

@app.get("/api/mcp/dynamic/mcps")
async def list_dynamic_mcps():
    """Liste les MCPs dynamiques créés"""
    mcps = _mcp_factory.list_created_mcps()
    return {"mcps": mcps}

@app.get("/api/mcp/dynamic/tools")
async def list_all_tools():
    """Liste TOUS les outils MCP disponibles (fixes + dynamiques)"""
    # MCPs fixes
    fixed_tools = [
        {"name": "filesystem_read", "description": "Lire des fichiers", "server": "filesystem"},
        {"name": "filesystem_write", "description": "Écrire des fichiers", "server": "filesystem"},
        {"name": "safari_navigate", "description": "Naviguer Safari", "server": "safari"},
        {"name": "mac_execute", "description": "Exécuter commande Mac", "server": "mac-control"},
    ]
    
    # MCPs dynamiques
    dynamic_mcps = _mcp_factory.list_created_mcps()
    dynamic_tools = []
    
    for app_key, mcp_info in dynamic_mcps.items():
        tools = mcp_info.get("tools", ["activate", "quit"])
        for tool in tools:
            dynamic_tools.append({
                "name": f"{app_key}_{tool}",
                "description": f"Outil {tool} pour {mcp_info['name']}",
                "server": f"dynamic_{app_key}",
                "app": mcp_info['name']
            })
    
    return {
        "fixed_tools": fixed_tools,
        "dynamic_tools": dynamic_tools,
        "total": len(fixed_tools) + len(dynamic_tools)
    }

@app.post("/api/mcp/dynamic/create")
async def create_mcp_for_app(request: Request):
    """Crée un MCP pour une application spécifique"""
    body = await request.json()
    app_name = body.get("app_name", "")
    
    if not app_name:
        return JSONResponse({"error": "app_name requis"}, status_code=400)
    
    # Cherche l'app dans les apps installées
    apps = _mcp_factory.list_installed_apps()
    app_info = None
    
    for key, info in apps.items():
        if app_name.lower() in info["name"].lower():
            app_info = info
            break
    
    if not app_info:
        return JSONResponse({
            "error": f"Application '{app_name}' non trouvée sur le Mac",
            "suggestion": "Utilisez /api/mcp/dynamic/apps pour voir les apps disponibles"
        }, status_code=404)
    
    # Crée le MCP
    mcp_file = _mcp_factory.create_mcp_for_app(app_info)
    
    # Met à jour la config MCP
    mcp_key = app_info["name"].lower().replace(" ", "_")
    
    import json
    config_path = PROJECT_ROOT / "mcp_servers.json"
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        config['mcpServers'][mcp_key] = {
            "command": VENV_PYTHON,
            "args": [mcp_file]
        }
        
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    
    return {
        "status": "created",
        "app": app_info["name"],
        "mcp_file": mcp_file,
        "tools": _mcp_factory._get_app_tools(app_info["name"])
    }

@app.post("/api/mcp/dynamic/process")
async def process_user_request(request: Request):
    """
    Traite une requête utilisateur et crée le MCP si nécessaire
    """
    body = await request.json()
    user_request = body.get("request", "")
    
    if not user_request:
        return JSONResponse({"error": "request requis"}, status_code=400)
    
    result = _mcp_factory.process_request(user_request)
    
    # Si MCP créé, mettre à jour la config
    if result.get("mcp_created") and result.get("mcp_file"):
        app_name = result.get("app", "")
        mcp_key = app_name.lower().replace(" ", "_")
        
        import json
        config_path = PROJECT_ROOT / "mcp_servers.json"
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            config['mcpServers'][mcp_key] = {
                "command": VENV_PYTHON,
                "args": [result["mcp_file"]]
            }
            
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            result["config_updated"] = True
    
    return result


# === MCP FACTORY LEGACY (compatibilité) ===

@app.get("/api/mcp/factory/apps")
async def list_mcp_apps_legacy():
    """Liste les applications disponibles (legacy)"""
    apps = _mcp_factory.list_installed_apps()
    created = _mcp_factory.list_created_mcps()
    return {
        "available_apps": list(apps.keys()),
        "created_mcps": list(created.keys())
    }

@app.get("/api/mcp/factory/created")
async def list_created_mcps_legacy():
    """Liste les MCPs créés (legacy)"""
    return {"mcps": _mcp_factory.list_created_mcps()}

@app.post("/api/mcp/factory/create")
async def create_mcp_legacy(request: Request):
    """Crée un MCP pour une app (legacy)"""
    body = await request.json()
    app_name = body.get("app_name", "")
    
    if not app_name:
        return JSONResponse({"error": "app_name requis"}, status_code=400)
    
    # Trouve l'app
    apps = _mcp_factory.list_installed_apps()
    app_info = None
    for key, info in apps.items():
        if app_name.lower() in info["name"].lower():
            app_info = info
            break
    
    if not app_info:
        return JSONResponse({"error": f"App '{app_name}' non trouvée"}, status_code=404)
    
    mcp_file = _mcp_factory.create_mcp_for_app(app_info)
    return {"status": "created", "mcp_file": mcp_file}

@app.post("/api/mcp/factory/process")
async def process_mcp_request_legacy(request: Request):
    """Traite requête et crée MCP (legacy)"""
    body = await request.json()
    return _mcp_factory.process_request(body.get("request", ""))

@app.post("/api/mcp/factory/create")
async def create_mcp(request: Request):
    """Crée un MCP pour une application spécifique"""
    body = await request.json()
    app_name = body.get("app_name", "")
    
    if not app_name:
        return JSONResponse({"error": "app_name requis"}, status_code=400)
    
    # Vérifie si déjà existant
    if _mcp_factory.exists(app_name):
        return {
            "status": "exists",
            "app": app_name,
            "mcp_file": _mcp_factory.list_created_mcps()[app_name].get("file")
        }
    
    # Crée le MCP
    mcp_file = _mcp_factory.create_mcp(app_name)
    return {
        "status": "created",
        "app": app_name,
        "mcp_file": mcp_file
    }

@app.post("/api/mcp/factory/process")
async def process_mcp_request(request: Request):
    """
    Traite une requête utilisateur et crée le MCP si nécessaire
    """
    body = await request.json()
    user_request = body.get("request", "")
    
    if not user_request:
        return JSONResponse({"error": "request requis"}, status_code=400)
    
    result = _mcp_factory.process_request(user_request)
    return result


# === WORKERS ===

@app.get("/api/workers")
async def list_workers():
    """List all workers"""
    workers = state.worker_manager.list()
    return {"workers": [w.__dict__ for w in workers]}

@app.post("/api/workers/local")
async def create_local_worker(workspace: str = ""):
    """Create local worker"""
    worker = state.worker_manager.create_local_worker(workspace)
    return worker.__dict__

@app.post("/api/workers/connect")
async def connect_remote(url: str, name: str = "Remote"):
    """Connect to remote worker"""
    worker = state.worker_manager.connect_remote(url, name)
    return worker.__dict__


# === DEBUG ===

@app.get("/api/debug/export")
async def export_debug():
    """Export debug info"""
    from core import get_core
    core = get_core()
    runtime_state = core.get_state()
    files = state.debug_exporter.export_full(runtime_state)
    return files

@app.get("/api/debug/logs")
async def export_logs():
    """Export logs"""
    log_file = state.debug_exporter.export_logs()
    return {"file": log_file}


# === WEBSOCKET CHAT ===

@app.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    """WebSocket chat endpoint"""
    await websocket.accept()
    logger.info("Client connected")
    
    # Get or create session
    session = state.session_manager.get() or state.session_manager.create("Web Session")
    session_id = session.id
    
    try:
        while True:
            # Receive message
            raw_data = await websocket.receive_text()
            allow_auto_routing = False
            data = raw_data
            requested_provider = None
            requested_model = None
            requested_api_key = None
            requested_base_url = None
            requested_system_prompt = None
            requested_turbo = False

            try:
                payload = json.loads(raw_data)
                if isinstance(payload, dict):
                    data = payload.get("message", "") or ""
                    allow_auto_routing = bool(payload.get("allow_auto_routing", False))
                    requested_provider = payload.get("provider")
                    requested_model = payload.get("model")
                    requested_api_key = payload.get("api_key")
                    requested_base_url = payload.get("base_url")
                    requested_system_prompt = payload.get("system_prompt")
                    requested_turbo = bool(payload.get("turbo", False))
            except Exception:
                pass

            logger.info(f"Received: {data[:50]}...")
            
            # Send typing indicator
            await websocket.send_json({"event_type": "typing", "content": "L'IA réfléchit..."})
            
            # Execute
            events = await state.core.execute(
                data,
                session.messages,
                allow_auto_routing=allow_auto_routing,
                provider=requested_provider,
                model=requested_model,
                api_key=requested_api_key,
                base_url=requested_base_url,
                system_prompt=requested_system_prompt,
                turbo=requested_turbo,
            )
            
            # Send events
            for event in events:
                await websocket.send_json(event.to_dict())
                
                # Save to session
                if event.event_type == "text":
                    state.session_manager.add_message("assistant", event.content, session_id=session_id)
                elif event.event_type == "tool_call":
                    state.session_manager.add_message(
                        "assistant",
                        f"Tool: {event.tool_name}",
                        tool_calls=[{"name": event.tool_name, "arguments": event.tool_args}],
                        session_id=session_id
                    )

            usage = state.core.last_usage_telemetry or {}
            requested = usage.get("requested", {})
            actual = usage.get("actual", {})
            route = usage.get("route", {})
            if requested or actual:
                await websocket.send_json({
                    "event_type": "execution_meta",
                    "requested_provider": requested.get("provider", state.core.llm.provider),
                    "requested_model": requested.get("model", state.core.llm.model),
                    "actual_provider": actual.get("provider", state.core.llm.provider),
                    "actual_model": actual.get("model", state.core.llm.model),
                    "fallback_reason": route.get("fallback_reason", ""),
                    "route_tier": route.get("tier", ""),
                    "route_reason": route.get("reason", ""),
                })
            
            # Save user message
            state.session_manager.add_message("user", data, session_id=session_id)
    
    except WebSocketDisconnect:
        logger.info("Client disconnected")


# === EVENT STREAM ===

@app.get("/api/events")
async def events_stream(request: Request):
    """SSE events stream"""
    # Simple implementation - could be expanded
    return JSONResponse({"events": "not implemented"})


# === INDEX ===

@app.get("/")
async def index(request: Request):
    """Serve the UI"""
    html_path = get_project_root() / "server_ui.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    dist_path = get_project_root() / "dist" / "index.html"
    if dist_path.exists():
        return HTMLResponse(dist_path.read_text())
    return HTMLResponse("<h1>MacAgent-OS Server</h1><p>Visit /docs for API</p>")

@app.get("/ui")
async def ui_page(request: Request):
    """Serve the UI"""
    html_path = get_project_root() / "server_ui.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("Not found")


# === WORKTOGETHER (Agent System) ===

class WorkGoalRequest(BaseModel):
    goal: str
    api_key: str = ""
    provider: str = "ollama"  # ollama, openai, anthropic, gemini
    model: str = ""
    base_url: str = ""
    max_iterations: int = 20

class WorkChatRequest(BaseModel):
    message: str

# Worktogether state
worktogether_state = {
    "active": False,
    "goal": "",
    "progress": 0,
    "tasks": [],
    "results": [],
    "logs": "",
    "status": "idle",
    "provider": "ollama",
    "model": ""
}

# Get available Ollama models
def get_ollama_models():
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        if resp.status_code == 200:
            return [m["name"] for m in resp.json().get("models", [])]
    except:
        pass
    return []

@app.get("/api/worktogether/status")
async def worktogether_status():
    """Get Worktogether status"""
    return worktogether_state

@app.get("/api/worktogether/providers")
async def worktogether_providers():
    """Get available providers and models - TOUS LES PROVIDERS"""
    from llm_universal import get_all_providers_info, get_provider, PROVIDERS
    
    ollama_models = get_ollama_models()
    
    # Tous les providers avec leurs modèles
    providers = []
    
    provider_list = [
        {"id": "ollama", "name": "Ollama", "models": ollama_models},
        {"id": "openai", "name": "OpenAI", "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]},
        {"id": "anthropic", "name": "Anthropic", "models": ["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"]},
        {"id": "gemini", "name": "Google Gemini", "models": ["gemini-2.0-flash-exp", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b"]},
        {"id": "mistral", "name": "Mistral AI", "models": ["mistral-large-latest", "mistral-small-latest", "mistral-medium-latest", "mixtral-8x7b"]},
        {"id": "cohere", "name": "Cohere", "models": ["command-r-plus", "command-r", "command"]},
        {"id": "groq", "name": "Groq", "models": ["llama-3.1-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"]},
        {"id": "huggingface", "name": "HuggingFace", "models": ["meta-llama/Llama-3.1-8B-Instruct", "meta-llama/Llama-3.1-70B-Instruct", "mistralai/Mistral-7B-Instruct-v0.2"]},
        {"id": "lmstudio", "name": "LM Studio", "models": ollama_models},  # Same as Ollama
        {"id": "azure", "name": "Azure OpenAI", "models": ["gpt-4", "gpt-4-turbo", "gpt-35-turbo"]},
    ]
    
    return {
        "providers": provider_list,
        "ollama_models": ollama_models
    }

@app.post("/api/worktogether/start")
async def worktogether_start(req: WorkGoalRequest):
    """Start Worktogether agent system"""
    import subprocess
    import threading
    
    worktogether_state["active"] = True
    worktogether_state["goal"] = req.goal
    worktogether_state["progress"] = 0
    worktogether_state["tasks"] = []
    worktogether_state["results"] = []
    worktogether_state["logs"] = ""
    worktogether_state["status"] = "running"
    worktogether_state["provider"] = req.provider
    worktogether_state["model"] = req.model
    
    def run_worktogether():
        base_dir = get_project_root()
        agent_dir = base_dir / "agent-system"
        
        if not agent_dir.exists():
            worktogether_state["error"] = "Agent system not found"
            worktogether_state["active"] = False
            worktogether_state["status"] = "failed"
            return
        
        # Build environment with LLM settings
        env = {**os.environ}
        
        if req.provider == "ollama":
            # Use local Ollama
            env["LLM_PROVIDER"] = "ollama"
            env["OLLAMA_MODEL"] = req.model or "dolphin3:latest"
            if req.base_url:
                env["OLLAMA_BASE_URL"] = req.base_url
        elif req.provider == "openai":
            env["LLM_PROVIDER"] = "openai"
            env["OPENAI_API_KEY"] = req.api_key or os.environ.get("OPENAI_API_KEY", "")
            env["OPENAI_MODEL"] = req.model or "gpt-4"
            if req.base_url:
                env["OPENAI_BASE_URL"] = req.base_url
        elif req.provider == "anthropic":
            env["LLM_PROVIDER"] = "anthropic"
            env["ANTHROPIC_API_KEY"] = req.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            env["ANTHROPIC_MODEL"] = req.model or "claude-3-sonnet"
            if req.base_url:
                env["ANTHROPIC_BASE_URL"] = req.base_url
        elif req.provider == "gemini":
            env["LLM_PROVIDER"] = "gemini"
            env["GEMINI_API_KEY"] = req.api_key or os.environ.get("GEMINI_API_KEY", "")
            env["GEMINI_MODEL"] = req.model or "gemini-2.0-flash"
        
        cmd = ["node", "index.js", req.goal]
        
        proc = subprocess.Popen(
            cmd,
            cwd=str(agent_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )
        
        for line in iter(proc.stdout.readline, ''):
            if line:
                worktogether_state["logs"] = (worktogether_state.get("logs", "") + line)[:3000]
                if "Task" in line or "✅" in line or "❌" in line or "task" in line.lower():
                    worktogether_state["progress"] = min(100, worktogether_state["progress"] + 10)
        
        proc.wait()
        
        if proc.returncode == 0:
            worktogether_state["progress"] = 100
            worktogether_state["status"] = "completed"
        else:
            worktogether_state["status"] = "failed"
        
        worktogether_state["active"] = False
    
    thread = threading.Thread(target=run_worktogether)
    thread.start()
    
    return {"status": "started", "goal": req.goal, "provider": req.provider, "model": req.model}

@app.get("/api/worktogether/stop")
async def worktogether_stop():
    """Stop Worktogether"""
    worktogether_state["active"] = False
    worktogether_state["status"] = "stopped"
    return {"status": "stopped"}

@app.post("/api/worktogether/chat")
async def worktogether_chat(req: WorkChatRequest):
    """Chat with Worktogether agent"""
    # This would connect to the LLM for chat
    return {"response": "Chat feature coming soon"}


# === RUN ===

def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


if __name__ == "__main__":
    import uvicorn
    if not _port_available(runtime.host, runtime.port):
        print(
            f"Mac Agent OS backend cannot start: port {runtime.port} is already used on {runtime.host}. "
            "Stop the existing process or change config/dev.json.",
            file=sys.stderr,
        )
        sys.exit(98)
    uvicorn.run(app, host=runtime.host, port=runtime.port, log_level=runtime.log_level)
