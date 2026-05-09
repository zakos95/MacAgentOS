"""
Desktop Skills registry for Mac Agent OS.

The registry is intentionally small: declarative definitions live in this file,
while user state is persisted in the existing settings.json under skill_states.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from paths import get_data_dir


VALID_RISKS = {"low", "medium", "high"}


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    category: str
    system_instructions: str
    allowed_tools: List[str]
    triggers: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    enabled_by_default: bool = False
    risk: str = "low"
    examples: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.risk not in VALID_RISKS:
            raise ValueError(f"Invalid skill risk for {self.id}: {self.risk}")


DEFAULT_SKILLS: List[SkillDefinition] = [
    SkillDefinition(
        id="mac_control",
        name="Mac Control",
        description="Ouvre des applications, les réglages système et exécute des actions macOS simples.",
        category="Desktop",
        system_instructions=(
            "Pour les demandes macOS simples, privilégie les outils mac-control disponibles. "
            "Demande confirmation avant toute action sensible ou destructive."
        ),
        allowed_tools=["mac-control"],
        triggers=["ouvre", "ouvrir", "lance", "réglages", "reglages", "system settings", "application"],
        parameters={"confirm_destructive_actions": True},
        enabled_by_default=True,
        risk="medium",
        examples=["Ouvre Safari", "Ouvre les réglages système"],
    ),
    SkillDefinition(
        id="safari_assistant",
        name="Safari Assistant",
        description="Ouvre Safari et peut lire ou agir sur Safari quand le MCP Safari est disponible.",
        category="Browser",
        system_instructions=(
            "Pour les demandes liées à Safari, utilise uniquement les capacités Safari disponibles "
            "et garde les actions visibles et réversibles."
        ),
        allowed_tools=["safari"],
        triggers=["safari", "onglet", "page web", "navigateur", "browser"],
        parameters={"read_current_page": True},
        enabled_by_default=True,
        risk="medium",
        examples=["Ouvre Safari", "Lis la page Safari active"],
    ),
    SkillDefinition(
        id="local_models",
        name="Local Models",
        description="Utilise Ollama et aide à diagnostiquer les modèles locaux.",
        category="IA locale",
        system_instructions=(
            "Pour les demandes de modèles locaux, explique l'état Ollama et ne suppose jamais "
            "qu'Ollama est obligatoire."
        ),
        allowed_tools=["ollama", "provider_diagnostics"],
        triggers=["ollama", "modèle local", "modele local", "qwen", "llama", "local"],
        parameters={"preferred_provider": "ollama"},
        enabled_by_default=True,
        risk="low",
        examples=["Quels modèles Ollama sont disponibles ?", "Diagnostique mes modèles locaux"],
    ),
    SkillDefinition(
        id="provider_doctor",
        name="Provider Doctor",
        description="Diagnostique OpenAI, Hugging Face, Anthropic, Gemini, Ollama et le bridge ChatGPT/Codex.",
        category="Diagnostics",
        system_instructions=(
            "Pour les problèmes de providers, résume l'état utilisateur clairement et ne journalise jamais de secret."
        ),
        allowed_tools=["provider_diagnostics"],
        triggers=["provider", "diagnostic", "clé api", "cle api", "hugging face", "openai", "anthropic", "gemini", "bridge"],
        parameters={"redact_secrets": True},
        enabled_by_default=True,
        risk="low",
        examples=["Pourquoi mon provider ne marche pas ?", "Diagnostique Hugging Face"],
    ),
    SkillDefinition(
        id="code_helper",
        name="Code Helper",
        description="Aide sur un repo local et peut lancer des tests si les outils nécessaires sont disponibles.",
        category="Code",
        system_instructions=(
            "Pour le code local, préfère lire avant de modifier. Ne supprime aucun fichier sans confirmation explicite."
        ),
        allowed_tools=["filesystem"],
        triggers=["repo", "code", "test", "bug", "fichier", "projet"],
        parameters={"require_confirmation_for_delete": True},
        enabled_by_default=True,
        risk="high",
        examples=["Aide-moi à comprendre ce repo", "Lance les tests si c'est autorisé"],
    ),
    SkillDefinition(
        id="self_update_lab",
        name="Self Update Lab",
        description="Prépare une copie safe et une copie working pour permettre à Mac Agent OS d'améliorer son propre code sans casser la version stable.",
        category="Maintenance",
        system_instructions=(
            "Pour les demandes d'auto-mise à jour, crée d'abord une copie safe intacte et une copie working éditable. "
            "Diagnostique et répare la working copy, valide toujours avant de builder, puis propose une candidate. "
            "Ne remplace jamais l'app stable sans confirmation explicite et backup."
        ),
        allowed_tools=["self_update", "filesystem"],
        triggers=["self update", "auto update", "autoupdate", "mise à jour", "debug", "débug", "duplique", "dupliquer", "copie safe", "v1.2", "1.2"],
        parameters={"safe_copy_required": True, "promote_only_after_validation": True, "backup_before_promote": True},
        enabled_by_default=True,
        risk="high",
        examples=[
            "Prépare un workspace self-update V1.2",
            "Valide la working copy avant de builder une candidate",
            "Auto-update la candidate sans remplacer la version stable",
        ],
    ),
]


class SkillsManager:
    def __init__(
        self,
        settings_path: Optional[Path] = None,
        definitions: Optional[Iterable[SkillDefinition]] = None,
    ):
        self.settings_path = Path(settings_path) if settings_path else get_data_dir() / "settings.json"
        self.definitions: Dict[str, SkillDefinition] = {
            skill.id: skill for skill in (definitions or DEFAULT_SKILLS)
        }

    def _load_settings(self) -> Dict[str, Any]:
        try:
            if self.settings_path.exists():
                loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
        except Exception:
            return {}
        return {}

    def _write_settings(self, settings: Dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _states(self) -> Dict[str, Dict[str, Any]]:
        raw = self._load_settings().get("skill_states", {})
        return raw if isinstance(raw, dict) else {}

    def _state_for(self, skill_id: str) -> Dict[str, Any]:
        skill = self.definitions[skill_id]
        state = self._states().get(skill_id, {})
        if not isinstance(state, dict):
            state = {}
        return {
            "enabled": bool(state.get("enabled", skill.enabled_by_default)),
            "parameters": state.get("parameters", skill.parameters) if isinstance(state.get("parameters", skill.parameters), dict) else {},
        }

    def set_enabled(self, skill_id: str, enabled: bool) -> Dict[str, Any]:
        if skill_id not in self.definitions:
            raise KeyError(skill_id)
        settings = self._load_settings()
        states = settings.get("skill_states", {})
        if not isinstance(states, dict):
            states = {}
        current = states.get(skill_id, {})
        if not isinstance(current, dict):
            current = {}
        current["enabled"] = bool(enabled)
        current.setdefault("parameters", self.definitions[skill_id].parameters)
        states[skill_id] = current
        settings["skill_states"] = states
        self._write_settings(settings)
        return self.to_dict(skill_id, {})

    def list(self) -> List[SkillDefinition]:
        return list(self.definitions.values())

    def get(self, skill_id: str) -> Optional[SkillDefinition]:
        return self.definitions.get(skill_id)

    def _availability(self, skill: SkillDefinition, context: Dict[str, Any]) -> tuple[bool, str]:
        active_tools = set(context.get("active_tools", []))
        skipped_tools = set(context.get("skipped_tools", []))
        missing = [tool for tool in skill.allowed_tools if tool not in active_tools]
        if not missing:
            return True, "Disponible"
        skipped = [tool for tool in missing if tool in skipped_tools]
        if skipped:
            return False, f"Indisponible: outil optionnel absent ({', '.join(skipped)})."
        return False, f"Indisponible: outil non actif ({', '.join(missing)})."

    def to_dict(self, skill_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if skill_id not in self.definitions:
            raise KeyError(skill_id)
        skill = self.definitions[skill_id]
        state = self._state_for(skill_id)
        available, message = self._availability(skill, context)
        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "system_instructions": skill.system_instructions,
            "allowed_tools": skill.allowed_tools,
            "triggers": skill.triggers,
            "parameters": state["parameters"],
            "enabled": state["enabled"],
            "risk": skill.risk,
            "examples": skill.examples,
            "available": available,
            "availability_message": message,
        }

    def to_dict_list(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [self.to_dict(skill_id, context) for skill_id in self.definitions]

    def relevant_enabled(self, user_message: str, context: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
        message = (user_message or "").lower()
        matches: List[Dict[str, Any]] = []
        for skill_id, skill in self.definitions.items():
            payload = self.to_dict(skill_id, context)
            if not payload["enabled"] or not payload["available"]:
                continue
            triggered = any(trigger.lower() in message for trigger in skill.triggers)
            if triggered:
                matches.append(payload)
        return matches[:limit]

    def build_prompt_extension(self, user_message: str, context: Dict[str, Any], limit: int = 3) -> str:
        skills = self.relevant_enabled(user_message, context, limit=limit)
        if not skills:
            return ""
        lines = []
        for skill in skills:
            tools = ", ".join(skill["allowed_tools"])
            lines.append(
                f"- {skill['name']} (risk: {skill['risk']}, tools: {tools}): "
                f"{skill['system_instructions']}"
            )
        return "\n".join(lines)

    def test(self, skill_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if skill_id not in self.definitions:
            raise KeyError(skill_id)
        payload = self.to_dict(skill_id, context)
        if not payload["available"]:
            return {
                "id": skill_id,
                "status": "unavailable",
                "message": payload["availability_message"],
                "details": {"allowed_tools": payload["allowed_tools"]},
            }
        if skill_id == "local_models":
            models = context.get("ollama_models", [])
            return {
                "id": skill_id,
                "status": "ok" if models else "warning",
                "message": (
                    f"{len(models)} modèle(s) Ollama détecté(s)."
                    if models else
                    "Ollama répond, mais aucun modèle local n'est installé."
                ),
                "details": {"models": models[:8]},
            }
        if skill_id == "provider_doctor":
            return {
                "id": skill_id,
                "status": "ok",
                "message": "Diagnostics providers disponibles.",
                "details": {"provider": context.get("provider", "")},
            }
        return {
            "id": skill_id,
            "status": "ok",
            "message": f"{payload['name']} est disponible.",
            "details": {"allowed_tools": payload["allowed_tools"], "risk": payload["risk"]},
        }


_manager: Optional[SkillsManager] = None


def get_skills_manager() -> SkillsManager:
    global _manager
    if _manager is None:
        _manager = SkillsManager()
    return _manager
