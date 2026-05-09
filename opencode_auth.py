import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
BRIDGE_BINARY_NAMES = (
    "codex",
    "CodexBridge",
    "ChatGPTBridge",
    "chatgpt-bridge",
    "MacAgentChatGPTBridge",
    "opencode",
)


def get_auth_path() -> Path:
    override = os.getenv("OPENCODE_AUTH_PATH", "").strip()
    return Path(override) if override else DEFAULT_AUTH_PATH


def get_opencode_bin() -> str:
    override = os.getenv("CHATGPT_BRIDGE_BIN", "").strip() or os.getenv("OPENCODE_BIN", "").strip()
    if override:
        candidate = Path(override).expanduser()
        return str(candidate) if candidate.exists() and os.access(candidate, os.X_OK) else ""

    for directory in _bridge_binary_directories():
        for name in BRIDGE_BINARY_NAMES:
            candidate = directory / name
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)

    resolved = shutil.which("opencode") or ""
    return resolved if resolved and os.access(resolved, os.X_OK) else ""


def has_opencode() -> bool:
    return bool(get_opencode_bin())


def _bridge_binary_directories() -> List[Path]:
    directories: List[Path] = []
    for raw in [
        os.getenv("MACAGENT_BRIDGE_DIR", "").strip(),
        os.getenv("MACAGENT_RESOURCES_DIR", "").strip(),
    ]:
        if raw:
            directories.append(Path(raw).expanduser())

    executable = Path(sys.executable).resolve()
    directories.extend([
        executable.parent,
        Path(__file__).resolve().parent,
    ])
    try:
        directories.append(Path.cwd())
    except FileNotFoundError:
        pass
    return list(dict.fromkeys(directories))


def get_bridge_runtime_source() -> str:
    binary = get_opencode_bin()
    if not binary:
        return "missing"
    path = Path(binary).resolve()
    if os.getenv("CHATGPT_BRIDGE_BIN") or os.getenv("OPENCODE_BIN"):
        return "env"
    if any(path.parent == directory.resolve() for directory in _bridge_binary_directories()):
        return "bundled"
    return "system"


def get_bridge_runtime_kind(binary: str = "") -> str:
    path = Path(binary or get_opencode_bin())
    name = path.name.lower()
    if "codex" in name:
        return "codex"
    return "opencode"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def run_opencode_command(args: List[str], timeout: int = 20) -> Dict[str, Any]:
    binary = get_opencode_bin()
    if not binary:
        return {
            "ok": False,
            "code": None,
            "stdout": "",
            "stderr": "",
            "error": "ChatGPT Bridge runtime is not available in this app bundle.",
        }

    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home()),
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": strip_ansi(proc.stdout),
            "stderr": strip_ansi(proc.stderr),
            "error": "",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "code": None,
            "stdout": "",
            "stderr": "",
            "error": f"Le bridge ChatGPT/Codex n'a pas répondu en moins de {timeout} secondes.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": None,
            "stdout": "",
            "stderr": "",
            "error": str(exc),
        }


def read_opencode_auth() -> Dict[str, Any]:
    auth_path = get_auth_path()
    if not auth_path.exists():
        return {}

    try:
        return json.loads(auth_path.read_text())
    except Exception:
        return {}


def get_openai_oauth_entry() -> Optional[Dict[str, Any]]:
    auth = read_opencode_auth()
    entry = auth.get("openai")
    if not isinstance(entry, dict):
        return None
    if entry.get("type") != "oauth":
        return None
    return entry


def get_openai_access_token() -> str:
    entry = get_openai_oauth_entry()
    if not entry:
        return ""

    expires = entry.get("expires")
    if isinstance(expires, (int, float)) and expires <= int(time.time() * 1000):
        return ""

    access = entry.get("access")
    return access if isinstance(access, str) else ""


def list_configured_opencode_providers() -> List[Dict[str, str]]:
    if get_bridge_runtime_kind() == "codex":
        return [{"name": "openai", "auth_type": "chatgpt"}]

    result = run_opencode_command(["providers", "list"], timeout=15)
    if not result["ok"]:
        return []

    providers: List[Dict[str, str]] = []
    for raw_line in result["stdout"].splitlines():
        line = raw_line.strip()
        if not line.startswith("●"):
            continue
        cleaned = line.lstrip("●").strip()
        if "  " in cleaned:
            name, auth_type = cleaned.rsplit("  ", 1)
        else:
            parts = cleaned.rsplit(" ", 1)
            name = parts[0]
            auth_type = parts[1] if len(parts) > 1 else ""
        providers.append({"name": name.strip(), "auth_type": auth_type.strip()})
    return providers


def get_openai_oauth_status() -> Dict[str, Any]:
    binary = get_opencode_bin()
    bridge_available = bool(binary)
    bridge_kind = get_bridge_runtime_kind(binary) if binary else "missing"
    bridge_source = get_bridge_runtime_source()

    if bridge_kind == "codex":
        status_result = run_opencode_command(["login", "status"], timeout=10)
        status_text = f"{status_result.get('stdout', '')}\n{status_result.get('stderr', '')}".strip()
        connected = bool(status_result.get("ok")) and "logged in" in status_text.lower()
        return {
            "installed": bridge_available,
            "bridge_available": bridge_available,
            "bridge_source": bridge_source,
            "bridge_kind": bridge_kind,
            "auth_path": str(Path.home() / ".codex"),
            "auth_file_exists": connected,
            "connected": connected,
            "expired": False,
            "expires": None,
            "account_id": None,
            "provider_name": "ChatGPT",
            "auth_type": "chatgpt",
            "status": "ready" if connected else "not_logged_in",
            "cli_error": "" if bridge_available else "ChatGPT Bridge runtime is not available in this app bundle.",
            "has_cli_provider_entry": True,
            "has_auth_entry": connected,
            "login_hint": "Clique sur “Se connecter avec ChatGPT” dans Mac Agent OS.",
        }

    entry = get_openai_oauth_entry()
    providers = list_configured_opencode_providers() if has_opencode() else []
    cli_openai = next((p for p in providers if p["name"].lower() == "openai"), None)

    expires = entry.get("expires") if entry else None
    expired = isinstance(expires, (int, float)) and expires <= int(time.time() * 1000)
    account_id = entry.get("accountId") if entry else None
    access_token = get_openai_access_token()

    connected = bool(cli_openai) and bool(entry) and bool(access_token) and not bool(expired)

    status = "ready" if connected else "not_logged_in"
    if not has_opencode():
        status = "not_installed"
    elif expired:
        status = "expired"
    elif cli_openai and not entry:
        status = "not_logged_in"
    elif entry and not access_token:
        status = "invalid"

    return {
        "installed": has_opencode(),
        "bridge_available": has_opencode(),
        "bridge_source": bridge_source,
        "bridge_kind": bridge_kind,
        "auth_path": str(get_auth_path()),
        "auth_file_exists": get_auth_path().exists(),
        "connected": connected,
        "expired": bool(expired),
        "expires": expires if isinstance(expires, (int, float)) else None,
        "account_id": account_id if isinstance(account_id, str) else None,
        "provider_name": "OpenAI",
        "auth_type": cli_openai["auth_type"] if cli_openai else "oauth",
        "status": status,
        "cli_error": "" if has_opencode() else "ChatGPT Bridge runtime is not available in this app bundle.",
        "has_cli_provider_entry": bool(cli_openai),
        "has_auth_entry": bool(entry),
        "login_hint": "Clique sur “Se connecter avec ChatGPT” dans Mac Agent OS.",
    }
