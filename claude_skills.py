"""Read local, user-invoked Claude skill packages as text."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml


NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
MAX_BYTES = 128 * 1024
LINK = re.compile(r"\[[^\]\n]*\]\((?:<([^>\n]+)>|([^()\s]+))\)")
PLACEHOLDER = re.compile(r"(?P<escape>\\)?\$(?:ARGUMENTS\[(?P<index>\d+)\]|(?P<short>\d+)|(?P<word>[A-Za-z_][A-Za-z0-9_]*))")


def _read(path: Path, limit: int) -> str:
    if not path.is_file():
        raise ValueError("Fichier de skill absent.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Le skill et ses références dépassent la taille prise en charge (128 Kio).")
    return data.decode("utf-8-sig")


def read_markdown(path: Path) -> tuple[dict, str]:
    text = _read(path, MAX_BYTES).replace("\r\n", "\n")
    fields = {}
    if text.split("\n", 1)[0].strip() == "---":
        header = re.match(r"^---[^\S\n]*\n(.*?)^---[^\S\n]*(?:\n|$)", text, re.M | re.S)
        if not header:
            raise ValueError("En-tête YAML incomplet.")
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(header[1])):
            raise ValueError("Les alias YAML ne sont pas pris en charge.")
        fields = yaml.safe_load(header[1]) or {}
        if not isinstance(fields, dict):
            raise ValueError("L’en-tête YAML doit être un dictionnaire.")
        text = text[header.end():]
    body = text.strip()
    if not body:
        raise ValueError("Le fichier Markdown est vide.")
    return fields, body


@dataclass(frozen=True)
class MarkdownSkill:
    path: Path
    command: str
    description: str
    body: str
    arguments: tuple[str, ...]
    blocked: str

    @classmethod
    def load(cls, path: Path) -> MarkdownSkill:
        fields, body = read_markdown(path)
        command = fields.get("name", path.parent.name)
        if not isinstance(command, str) or not NAME.fullmatch(command):
            command = path.parent.name
        desc = fields.get("description") or body.splitlines()[0]
        if not isinstance(desc, str):
            raise ValueError("Description invalide.")
        arguments = fields.get("arguments", [])
        if isinstance(arguments, str):
            arguments = arguments.split()
        if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
            raise ValueError("La liste des arguments du skill est invalide.")
        blocked = ""
        if fields.get("user-invocable") in (False, "false"):
            blocked = "Ce skill désactive l’invocation manuelle."
        elif any(fields.get(k) for k in ("hooks", "agent", "context", "shell", "mcpServers")) or re.search(r"!`|^\s*```!", body, re.M):
            blocked = "Ce skill demande une extension d’exécution de Claude Code (shell, hooks ou agent)."
        elif fields.get("model") not in (None, "inherit") or fields.get("effort"):
            blocked = "Ce skill demande une configuration de modèle spécifique à Claude Code."
        return cls(path, command, str(desc)[:1536], body, tuple(arguments), blocked)

    def render(self, argument_text: str) -> str:
        if self.blocked:
            raise ValueError(self.blocked)
        try:
            words = shlex.split(argument_text)
        except ValueError:
            words = argument_text.split()
        named = dict(zip(self.arguments, words))
        used = False

        def substitute(match: re.Match) -> str:
            nonlocal used
            if match["escape"]:
                return match[0][1:]
            name = match["word"]
            if name == "ARGUMENTS":
                value = argument_text
            elif name in self.arguments:
                value = named.get(name, "")
            elif name:
                return match[0]
            else:
                index_text = match["index"] or match["short"]
                if len(index_text) > 6 or int(index_text) >= len(words):
                    return match[0]
                value = words[int(index_text)]
            used = True
            return value

        rendered = PLACEHOLDER.sub(substitute, self.body)
        if argument_text and not used:
            rendered += "\n\nARGUMENTS: " + argument_text

        # Only links from the installed skill itself may select companion text.
        directory = self.path.parent.resolve()
        remaining = MAX_BYTES - len(self.body.encode())
        seen = {self.path.resolve()}
        for match in LINK.finditer(self.body):
            url = urlsplit(match[1] or match[2])
            target = Path(unquote(url.path))
            if url.scheme or url.netloc or target.is_absolute() or target.suffix.lower() not in {".md", ".txt"}:
                continue
            path = (directory / target).resolve()
            if not path.is_relative_to(directory):
                raise ValueError("Une référence du skill sort de son dossier.")
            if path in seen:
                continue
            seen.add(path)
            try:
                reference = _read(path, remaining)
            except (OSError, UnicodeError) as exc:
                raise ValueError("Une référence du skill est illisible.") from exc
            remaining -= len(reference.encode())
            rendered += f"\n\n[Référence : {target}]\n{reference}"
        return rendered


def discover(personal_root: Path, project: str = "") -> dict[str, MarkdownSkill]:
    # A personal command takes precedence over a command from the active project.
    roots = [personal_root]
    if project:
        roots.append(Path(project).expanduser().resolve())
    found = {}
    for scope in roots:
        root = scope / ".claude" / "skills"
        if not root.resolve().is_relative_to(scope.resolve()) or not root.is_dir():
            continue
        try:
            entries = sorted(root.glob("*/SKILL.md"))
        except OSError:
            continue
        for path in entries:
            name = path.parent.name
            if not NAME.fullmatch(name) or name in found:
                continue
            try:
                if not path.resolve().is_relative_to(root.resolve()) or path.parent.is_symlink() or path.is_symlink():
                    continue
                skill = MarkdownSkill.load(path)
            except (OSError, UnicodeError, ValueError, RuntimeError, yaml.YAMLError):
                skill = MarkdownSkill(path, name, "SKILL.md illisible.", "", (), "Le fichier SKILL.md est invalide ou illisible.")
            found[name] = skill
    return found
