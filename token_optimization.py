import json
import math
import re
import time
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, List, Optional


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def estimate_message_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += 6
        content = message.get("content", "")
        if isinstance(content, list):
            total += estimate_tokens(json.dumps(content, ensure_ascii=False))
        else:
            total += estimate_tokens(str(content))
    return total


def _shorten(text: str, limit: int = 280) -> str:
    value = re.sub(r"\s+", " ", (text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _extract_keywords(text: str, limit: int = 6) -> List[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9_./:-]{4,}", text or "")
    seen = []
    for word in words:
        lowered = word.lower()
        if lowered not in seen:
            seen.append(lowered)
        if len(seen) >= limit:
            break
    return seen


@dataclass
class RouteDecision:
    model: str
    tier: str
    reason: str


class JsonStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, payload: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ProjectMemory:
    def __init__(self, data_dir: Path):
        self.store = JsonStore(data_dir / "project_memory.json")

    def get_summary(self, project_key: str) -> str:
        return self.store.load().get(project_key, {}).get("summary", "")

    def update(self, project_key: str, project_label: str, user_message: str, assistant_text: str) -> str:
        payload = self.store.load()
        entry = payload.get(project_key, {})
        lines = entry.get("lines", [])

        additions = []
        if not entry.get("project"):
            additions.append(f"Projet: {project_label}")
        keywords = _extract_keywords(user_message)
        if keywords:
            additions.append("Mots-clés: " + ", ".join(keywords))
        if user_message:
            additions.append("Dernière demande: " + _shorten(user_message, 180))
        if assistant_text:
            additions.append("Dernière réponse: " + _shorten(assistant_text, 160))

        for item in additions:
            if item and item not in lines:
                lines.append(item)

        compact_lines = lines[-6:]
        summary = " | ".join(compact_lines)
        summary = _shorten(summary, 520)
        payload[project_key] = {
            "project": project_label,
            "summary": summary,
            "lines": compact_lines,
            "updated_at": time.time(),
        }
        self.store.save(payload)
        return summary


class TaskState:
    def __init__(self, data_dir: Path):
        self.store = JsonStore(data_dir / "task_state.json")

    def get_state(self, project_key: str) -> str:
        return self.store.load().get(project_key, {}).get("state", "")

    def update(self, project_key: str, user_message: str, assistant_text: str = "") -> str:
        payload = self.store.load()
        state = _shorten(user_message or assistant_text, 180)
        if assistant_text:
            state = _shorten(f"{_shorten(user_message, 90)} -> {_shorten(assistant_text, 90)}", 180)
        payload[project_key] = {"state": state, "updated_at": time.time()}
        self.store.save(payload)
        return state


class ContextBuilder:
    def __init__(self, max_messages: int = 4, max_chars_per_message: int = 500):
        self.max_messages = max_messages
        self.max_chars_per_message = max_chars_per_message

    def build(
        self,
        base_system_prompt: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        project_summary: str = "",
        task_state: str = "",
        turbo: bool = False,
    ) -> Dict[str, Any]:
        trimmed_history = []
        for item in (history or [])[-self.max_messages:]:
            role = item.get("role", "user")
            content = str(item.get("content", ""))
            if not turbo:
                content = _shorten(content, self.max_chars_per_message)
            if content:
                trimmed_history.append({"role": role, "content": content})

        system_parts = [
            base_system_prompt.strip(),
            "Réponds brièvement. Garde uniquement le contexte utile. Si tu proposes un correctif, fais un patch minimal.",
        ]
        if project_summary:
            system_parts.append(f"Mémoire projet: {project_summary}")
        if task_state:
            system_parts.append(f"Tâche en cours: {task_state}")

        messages = [{"role": "system", "content": "\n\n".join(part for part in system_parts if part)}]
        messages.extend(trimmed_history)
        final_user_message = user_message if turbo else _shorten(user_message, 2400)
        messages.append({"role": "user", "content": final_user_message})

        return {
            "messages": messages,
            "history_used": len(trimmed_history),
            "system_prompt": messages[0]["content"],
            "user_message": messages[-1]["content"],
        }


class ModelRouter:
    SIMPLE_KEYWORDS = {"résume", "resume", "traduis", "traduis", "corrige", "titre", "renomme", "formatte"}
    COMPLEX_KEYWORDS = {
        "analyse", "debug", "corrige bug", "refactor", "architecture", "streaming",
        "optimise", "cyber", "sécurité", "security", "outil", "mcp", "multimodal",
        "compile", "swift", "python", "traceback", "exception", "erreur"
    }

    SMALL_MODELS = {
        "openai": "openai/gpt-5.4-mini",
        "ollama": "phi3:mini",
        "anthropic": "claude-3-5-haiku-20240307",
        "gemini": "gemini-2.0-flash",
        "google": "gemini-2.0-flash",
        "mistral": "mistral-small-latest",
        "groq": "llama-3.1-8b-instant",
        "cohere": "command-r",
    }

    LARGE_MODELS = {
        "openai": "openai/gpt-5.4",
        "ollama": "dolphin3:latest",
        "anthropic": "claude-sonnet-4-20250514",
        "gemini": "gemini-2.5-pro",
        "google": "gemini-2.5-pro",
        "mistral": "mistral-large-latest",
        "groq": "llama-3.1-70b-versatile",
        "cohere": "command-r-plus",
    }

    def route(
        self,
        provider: str,
        requested_model: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[str]] = None,
    ) -> RouteDecision:
        provider = (provider or "").lower()
        score = 0
        lowered = (user_message or "").lower()

        if len(user_message or "") > 280:
            score += 1
        if len(user_message or "") > 900:
            score += 1
        if len(history or []) > 4:
            score += 1
        if attachments:
            score += 1
        if "```" in (user_message or "") or "/Users/" in (user_message or ""):
            score += 1
        if any(keyword in lowered for keyword in self.COMPLEX_KEYWORDS):
            score += 2
        elif any(keyword in lowered for keyword in self.SIMPLE_KEYWORDS):
            score -= 1

        if requested_model:
            normalized = requested_model.lower()
            if any(small in normalized for small in ["mini", "haiku", "flash", "8b", "small"]):
                score -= 1
            if any(big in normalized for big in ["opus", "sonnet", "pro", "70b", "5.4"]):
                score += 1

        if score >= 3:
            return RouteDecision(
                model=requested_model or self.LARGE_MODELS.get(provider, requested_model),
                tier="large",
                reason="tâche complexe ou contexte riche",
            )

        return RouteDecision(
            model=self.SMALL_MODELS.get(provider, requested_model) or requested_model,
            tier="small",
            reason="tâche simple ou courte",
        )


class UsageTelemetry:
    RATES_PER_MILLION = {
        "openai/gpt-5.4": (5.0, 15.0),
        "openai/gpt-5.4-mini": (0.6, 2.0),
        "openai/gpt-4o": (5.0, 15.0),
        "openai/gpt-4o-mini": (0.15, 0.6),
        "claude-sonnet-4-20250514": (3.0, 15.0),
        "gemini-2.5-pro": (2.5, 10.0),
        "gemini-2.0-flash": (0.2, 0.8),
    }

    def build(self, model: str, messages: List[Dict[str, Any]], output_text: str, started_at: float) -> Dict[str, Any]:
        input_tokens = estimate_message_tokens(messages)
        output_tokens = estimate_tokens(output_text)
        input_rate, output_rate = self.RATES_PER_MILLION.get(model, (0.0, 0.0))
        estimated_cost = ((input_tokens * input_rate) + (output_tokens * output_rate)) / 1_000_000
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": round(estimated_cost, 6),
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        }


class TokenOptimizer:
    def __init__(self, data_dir: Path):
        self.project_memory = ProjectMemory(data_dir)
        self.task_state = TaskState(data_dir)
        self.context_builder = ContextBuilder()
        self.model_router = ModelRouter()
        self.telemetry = UsageTelemetry()

    def project_key(self, project_path: str = "") -> str:
        normalized = (project_path or "global").strip() or "global"
        return sha1(normalized.encode("utf-8")).hexdigest()[:12]

    def project_label(self, project_path: str = "") -> str:
        value = (project_path or "global").strip()
        if not value:
            return "global"
        return Path(value).name or value

    def build_context(
        self,
        base_system_prompt: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        project_path: str = "",
        turbo: bool = False,
    ) -> Dict[str, Any]:
        project_key = self.project_key(project_path)
        return {
            "project_key": project_key,
            "project_label": self.project_label(project_path),
            "project_summary": self.project_memory.get_summary(project_key),
            "task_state": self.task_state.get_state(project_key),
            **self.context_builder.build(
                base_system_prompt=base_system_prompt,
                user_message=user_message,
                history=history,
                project_summary=self.project_memory.get_summary(project_key),
                task_state=self.task_state.get_state(project_key),
                turbo=turbo,
            ),
        }

    def choose_model(
        self,
        provider: str,
        requested_model: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[str]] = None,
    ) -> RouteDecision:
        return self.model_router.route(provider, requested_model, user_message, history, attachments)

    def update_memory(self, project_key: str, project_label: str, user_message: str, assistant_text: str) -> Dict[str, str]:
        summary = self.project_memory.update(project_key, project_label, user_message, assistant_text)
        task = self.task_state.update(project_key, user_message, assistant_text)
        return {"project_memory": summary, "task_state": task}
