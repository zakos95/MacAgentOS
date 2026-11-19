"""One bounded, text-only team run on the user's selected model."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from claude_skills import NAME, read_markdown


ROLES = {
    "analyste": "Propose une solution concrète à la demande avec les informations fournies.",
    "relecteur": "Examine la demande indépendamment. Identifie les points à vérifier et les alternatives pertinentes.",
}
ProviderFactory = Callable[[], Any]


def team_goal(text: str) -> tuple[str, list[str]]:
    roles = []
    text = text.strip()
    while match := re.match(r"@([a-zA-Z0-9_-]+)(?:\s+|$)", text):
        if len(roles) >= 4:
            raise ValueError("Sélectionne au maximum quatre agents.")
        roles.append(match[1])
        text = text[match.end():]
    return text, roles


def role_prompts(names: list[str], personal_root: Path, project_path: str) -> dict[str, str]:
    if not names:
        return ROLES
    if len(names) > 4 or len(set(names)) != len(names) or any(not NAME.fullmatch(n) for n in names):
        raise ValueError("Sélectionne de un à quatre agents distincts par leur nom de fichier.")
    scopes = [personal_root]
    if project_path:
        scopes.insert(0, Path(project_path).expanduser().resolve())
    roles = {}
    for name in names:
        for scope in scopes:
            path = scope / ".claude" / "agents" / f"{name}.md"
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(scope.resolve()):
                continue
            try:
                fields, body = read_markdown(path)
            except (ValueError, OSError, yaml.YAMLError, RuntimeError) as exc:
                raise ValueError(f"Le profil @{name} est illisible.") from exc
            if (any(fields.get(k) for k in ("hooks", "mcpServers", "skills", "isolation", "memory", "tools", "permissionMode"))
                    or fields.get("model") not in (None, "inherit")):
                raise ValueError(f"Le profil @{name} demande des capacités spécifiques à Claude Code. Utilise un profil texte.")
            roles[name] = body
            break
        if name not in roles:
            if name not in ROLES:
                raise ValueError(f"Profil @{name} introuvable dans .claude/agents.")
            roles[name] = ROLES[name]
    return roles


class TeamRun:
    def __init__(self, goal: str, provider: str, model: str, make_provider: ProviderFactory, roles: dict[str, str]):
        self.goal = goal.strip()
        if not self.goal or len(self.goal) > 16000:
            raise ValueError("Donne un objectif compris entre 1 et 16 000 caractères.")
        self.provider = provider
        self.model = model
        self.make_provider = make_provider
        self.roles = roles
        self.status = "running"
        self.results = {role: {"role": role, "status": "queued", "content": ""} for role in self.roles}
        self.summary = ""
        self.error = ""
        self.job: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self.status in {"running", "synthesizing", "stopping"}

    def snapshot(self) -> dict:
        done = sum(r["status"] in {"completed", "failed"} for r in self.results.values())
        return {
            "active": self.active, "goal": self.goal, "status": self.status,
            "provider": self.provider, "model": self.model, "tasks": list(self.roles),
            "results": [dict(r) for r in self.results.values()],
            "progress": 100 if self.status == "completed" else done * 70 // len(self.roles),
            "summary": self.summary, "error": self.error,
            "logs": "\n".join(f"{role} — {r['status']}" for role, r in self.results.items()),
        }

    async def _ask(self, instruction: str, text: str) -> str:
        # Keep the role in the request because some local model templates drop system messages.
        messages = [
            {"role": "system", "content": "Tu travailles uniquement sur le texte fourni. Réponds dans la langue de la demande."},
            {"role": "user", "content": f"{instruction}\n\n{text}"},
        ]
        response = await asyncio.to_thread(lambda: self.make_provider().chat(messages))
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, str) or not content.strip() or response.get("type") != "text":
            raise ValueError("Le modèle n’a pas fourni de réponse texte. Vérifie sa configuration.")
        return content.strip()

    async def _worker(self, role: str, instruction: str) -> None:
        result = self.results[role]
        result["status"] = "running"
        try:
            answer = await self._ask(instruction, self.goal)
            result.update(status="completed", content=answer)
        except Exception:
            result.update(status="failed", content="Cet agent n’a pas obtenu de réponse du modèle.")

    def stop(self) -> None:
        if self.active:
            self.status = "stopping"

    async def run(self) -> None:
        try:
            if self.status == "stopping":
                return
            await asyncio.gather(*(self._worker(role, prompt) for role, prompt in self.roles.items()))
            if self.status == "stopping":
                return
            reports = {role: r["content"][:12000] for role, r in self.results.items() if r["status"] == "completed"}
            if not reports:
                self.status = "failed"
                self.error = "Aucun agent n’a obtenu de réponse. Vérifie le fournisseur et le modèle."
                return
            self.status = "synthesizing"
            summary = await self._ask(
                "Réponds à l’objectif initial en synthétisant ces contributions indépendantes. Signale leurs désaccords pertinents.",
                json.dumps({"objectif": self.goal, "contributions": reports}, ensure_ascii=False),
            )
            if self.status != "stopping":
                self.summary = summary
                if len(reports) < len(self.roles):
                    self.summary += "\n\nUne contribution manque car un agent n’a pas obtenu de réponse."
                self.status = "completed"
        except Exception:
            if self.status != "stopping":
                self.status = "failed"
                self.error = "La synthèse de l’équipe a échoué. Vérifie le fournisseur et le modèle."
        finally:
            if self.status == "stopping":
                self.status = "stopped"

    async def follow_up(self, message: str) -> str:
        if self.status != "completed":
            raise ValueError("Termine d’abord un travail en équipe.")
        if not message.strip() or len(message) > 16000:
            raise ValueError("Donne une question comprise entre 1 et 16 000 caractères.")
        return await self._ask(
            "Réponds à la question au sujet du travail de cette équipe.",
            json.dumps({"objectif": self.goal, "synthèse": self.summary[:16000], "question": message}, ensure_ascii=False),
        )


class TeamService:
    def __init__(self):
        self.current: TeamRun | None = None

    def start(self, goal: str, provider: str, model: str, make_provider: ProviderFactory, roles: dict[str, str] | None = None) -> TeamRun:
        if self.current and self.current.active:
            raise RuntimeError("Une équipe travaille déjà. Attends la fin du travail en cours.")
        run = TeamRun(goal, provider, model, make_provider, roles or ROLES)
        self.current = run
        run.job = asyncio.create_task(run.run())
        return run

    def snapshot(self) -> dict:
        if self.current:
            return self.current.snapshot()
        return {"active": False, "status": "idle", "goal": "", "provider": "", "model": "",
                "progress": 0, "tasks": [], "results": [], "logs": "", "summary": "", "error": ""}
