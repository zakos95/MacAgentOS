import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from opencode_auth import get_bridge_runtime_kind, get_openai_oauth_status, run_opencode_command


DEFAULT_OPENAI_MODEL = "gpt-5.4"


def _workspace_dir() -> str:
    return str(Path(__file__).parent)


def _normalize_openai_model(model: str) -> str:
    selected = (model or "").strip()
    if not selected:
        return f"openai/{DEFAULT_OPENAI_MODEL}"
    if "/" in selected:
        return selected
    return f"openai/{selected}"


def get_local_chatgpt_bridge_status() -> Dict[str, Any]:
    oauth = get_openai_oauth_status()
    installed = oauth.get("installed", False)

    if not installed:
        return {
            "provider": "local_chatgpt_codex",
            "installed": False,
            "bridge_source": oauth.get("bridge_source", "missing"),
            "connected": False,
            "expired": False,
            "status": "not_installed",
            "error": "Bridge ChatGPT absent du bundle de cette app.",
            "login_hint": oauth.get("login_hint", ""),
            "models_supported": False,
            "chat_supported": False,
            "oauth": oauth,
        }

    if oauth.get("expired"):
        return {
            "provider": "local_chatgpt_codex",
            "installed": True,
            "bridge_source": oauth.get("bridge_source", ""),
            "connected": False,
            "expired": True,
            "status": "expired",
            "error": "La session ChatGPT du bridge a expiré.",
            "login_hint": oauth.get("login_hint", ""),
            "models_supported": True,
            "chat_supported": False,
            "oauth": oauth,
        }

    if oauth.get("status") == "invalid":
        return {
            "provider": "local_chatgpt_codex",
            "installed": True,
            "bridge_source": oauth.get("bridge_source", ""),
            "connected": False,
            "expired": False,
            "status": "invalid",
            "error": "La session ChatGPT du bridge est invalide ou incomplète.",
            "login_hint": oauth.get("login_hint", ""),
            "models_supported": True,
            "chat_supported": False,
            "oauth": oauth,
        }

    if not oauth.get("connected"):
        return {
            "provider": "local_chatgpt_codex",
            "installed": True,
            "bridge_source": oauth.get("bridge_source", ""),
            "connected": False,
            "expired": False,
            "status": "not_logged_in",
            "error": "Connecte-toi à ChatGPT pour activer le bridge.",
            "login_hint": oauth.get("login_hint", ""),
            "models_supported": True,
            "chat_supported": False,
            "oauth": oauth,
        }

    return {
        "provider": "local_chatgpt_codex",
        "installed": True,
        "bridge_source": oauth.get("bridge_source", ""),
        "connected": True,
        "expired": False,
        "status": "ready",
        "error": "",
        "login_hint": oauth.get("login_hint", ""),
        "models_supported": True,
        "chat_supported": True,
        "oauth": oauth,
    }


def list_openai_models_via_opencode() -> List[str]:
    status = get_local_chatgpt_bridge_status()
    if not status["installed"] or not status["connected"]:
        return []

    if status.get("oauth", {}).get("bridge_kind") == "codex" or status.get("bridge_source") == "codex":
        return list(dict.fromkeys([
            DEFAULT_OPENAI_MODEL,
            "gpt-5.4",
            "gpt-5.3-codex",
            "gpt-5.2",
        ]))

    result = run_opencode_command(["models", "openai"], timeout=20)
    if not result["ok"]:
        return []

    models: List[str] = []
    for line in result["stdout"].splitlines():
        value = line.strip()
        if not value or value.startswith("ERROR "):
            continue
        models.append(value)
    return models


def _messages_to_prompt(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> str:
    system_parts: List[str] = []
    other_parts: List[str] = []

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        else:
            other_parts.append(f"{role.upper()}:\n{content}")

    prompt_parts: List[str] = []
    if system_parts:
        prompt_parts.append("SYSTEM:\n" + "\n\n".join(system_parts))

    if tools:
        prompt_parts.append(
            "AVAILABLE TOOLS:\n"
            "If tool use is needed, describe the action in plain text. "
            "Do not output JSON tool calls.\n"
            + json.dumps(tools, ensure_ascii=True, indent=2)
        )

    if other_parts:
        prompt_parts.append("\n\n".join(other_parts))

    return "\n\n---\n\n".join(prompt_parts).strip()


def test_local_chatgpt_bridge(model: str = "") -> Dict[str, Any]:
    return chat_with_opencode_openai(
        [{"role": "user", "content": "Say only OK."}],
        model=model or f"openai/{DEFAULT_OPENAI_MODEL}",
    )


def chat_with_opencode_openai(
    messages: List[Dict[str, Any]],
    model: str = "",
    tools: Optional[List[Dict[str, Any]]] = None,
    files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    status = get_local_chatgpt_bridge_status()
    if not status["installed"]:
        return {"type": "error", "content": status["error"], "provider": "local_chatgpt_codex"}
    if not status["connected"]:
        return {"type": "error", "content": status["error"], "provider": "local_chatgpt_codex"}

    prompt = _messages_to_prompt(messages, tools)
    if not prompt:
        return {"type": "error", "content": "Empty prompt.", "provider": "local_chatgpt_codex"}

    if status.get("oauth", {}).get("bridge_kind") == "codex" or get_bridge_runtime_kind() == "codex":
        return _chat_with_codex_bridge(prompt, model=model, files=files)

    command = [
        "run",
        "-m",
        _normalize_openai_model(model),
        "--format",
        "json",
    ]
    for file_path in files or []:
        if file_path:
            command.append(f"--file={file_path}")
    command.append("--")
    command.append(prompt)

    result = run_opencode_command(command, timeout=120)
    if not result["ok"]:
        message = result["stderr"] or result["stdout"] or result["error"] or "Le bridge ChatGPT a renvoyé une erreur inconnue."
        return {"type": "error", "content": message, "provider": "local_chatgpt_codex", "model": _normalize_openai_model(model)}

    final_text = ""
    for line in result["stdout"].splitlines():
        row = line.strip()
        if not row or not row.startswith("{"):
            continue
        try:
            event = json.loads(row)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            part = event.get("part") or {}
            text = part.get("text")
            if isinstance(text, str):
                final_text = text

    if not final_text:
        return {"type": "error", "content": "Le bridge ChatGPT n’a renvoyé aucun texte.", "provider": "local_chatgpt_codex"}

    return {"type": "text", "content": final_text, "provider": "local_chatgpt_codex", "model": _normalize_openai_model(model)}


def _normalize_codex_model(model: str) -> str:
    selected = (model or "").strip()
    if selected.startswith("openai/"):
        selected = selected.split("/", 1)[1]
    return selected or DEFAULT_OPENAI_MODEL


def _chat_with_codex_bridge(prompt: str, model: str = "", files: Optional[List[str]] = None) -> Dict[str, Any]:
    with tempfile.NamedTemporaryFile(prefix="macagent-codex-", suffix=".txt", delete=True) as handle:
        output_path = handle.name
        command = [
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            _workspace_dir(),
            "-m",
            _normalize_codex_model(model),
            "-o",
            output_path,
        ]
        for file_path in files or []:
            if file_path and file_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                command.extend(["-i", file_path])
        command.append("--")
        command.append(prompt)

        result = run_opencode_command(command, timeout=180)
        if not result["ok"]:
            message = result["stderr"] or result["stdout"] or result["error"] or "Le bridge Codex a renvoyé une erreur inconnue."
            return {"type": "error", "content": message, "provider": "local_chatgpt_codex", "model": _normalize_codex_model(model)}

        try:
            final_text = Path(output_path).read_text(encoding="utf-8").strip()
        except Exception:
            final_text = ""
        if not final_text:
            final_text = (result.get("stdout") or "").strip()
        if not final_text:
            return {"type": "error", "content": "Le bridge Codex n’a renvoyé aucun texte.", "provider": "local_chatgpt_codex"}

        return {"type": "text", "content": final_text, "provider": "local_chatgpt_codex", "model": _normalize_codex_model(model)}
