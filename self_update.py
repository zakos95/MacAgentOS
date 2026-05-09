"""
Self-update workspace management for Mac Agent OS.

This module never overwrites the running app or the safe copy. It prepares an
editable working tree, validates it, and can build a candidate app beside it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


EXCLUDED_NAMES = {
    ".git",
    ".venv",
    ".venv312",
    "__pycache__",
    ".pytest_cache",
    "build",
    "dist",
    ".build",
    "node_modules",
}


@dataclass
class SelfUpdateWorkspace:
    root: Path
    safe_path: Path
    working_path: Path
    manifest_path: Path


def _default_root() -> Path:
    return Path.home() / "Desktop" / "MacAgentOS-SelfUpdate"


def _safe_source_path(raw_path: str) -> Optional[Path]:
    raw = (raw_path or "").strip()
    source = Path(raw).expanduser() if raw else Path.cwd()
    try:
        source = source.resolve()
    except Exception:
        return None
    if not source.exists() or not source.is_dir():
        return None
    if any(part in EXCLUDED_NAMES for part in source.parts):
        return None
    return source


def _ignore_names(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES}


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(str(target))
    shutil.copytree(source, target, ignore=_ignore_names)


def workspace_from_root(root: str = "") -> SelfUpdateWorkspace:
    base = Path(root).expanduser() if root else _default_root()
    base = base.resolve()
    return SelfUpdateWorkspace(
        root=base,
        safe_path=base / "MacAgentOS-SAFE",
        working_path=base / "MacAgentOS-WORKING",
        manifest_path=base / "self_update_manifest.json",
    )


def _manifest_for_working(working: Path) -> Optional[Path]:
    candidates = [
        working.parent / "self_update_manifest.json",
        _default_root() / "self_update_manifest.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _validation_python(working: Path) -> str:
    env_python = os.environ.get("MACAGENT_VALIDATION_PYTHON", "").strip()
    if env_python and Path(env_python).exists():
        return env_python

    local_python = working / ".venv312" / "bin" / "python"
    if local_python.exists():
        return str(local_python)

    manifest_path = _manifest_for_working(working)
    if manifest_path:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_python = str(manifest.get("validation_python", "")).strip()
            if manifest_python and Path(manifest_python).exists():
                return manifest_python
        except Exception:
            pass

    if not getattr(sys, "frozen", False):
        return str(Path(sys.executable))

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    return str(Path(sys.executable))


def status(root: str = "") -> dict[str, Any]:
    workspace = workspace_from_root(root)
    manifest: dict[str, Any] = {}
    if workspace.manifest_path.exists():
        try:
            loaded = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except Exception:
            manifest = {"error": "Manifest illisible."}
    return {
        "root": str(workspace.root),
        "safe_path": str(workspace.safe_path),
        "working_path": str(workspace.working_path),
        "safe_exists": workspace.safe_path.exists(),
        "working_exists": workspace.working_path.exists(),
        "manifest": manifest,
    }


def prepare(source_path: str = "", destination_root: str = "", force_working_refresh: bool = False) -> dict[str, Any]:
    source = _safe_source_path(source_path)
    if source is None:
        return {"status": "error", "message": "Source projet introuvable ou non autorisée."}

    workspace = workspace_from_root(destination_root)
    workspace.root.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    if not workspace.safe_path.exists():
        _copy_tree(source, workspace.safe_path)
        created.append("safe")

    if workspace.working_path.exists() and force_working_refresh:
        archived = workspace.root / f"MacAgentOS-WORKING-archive-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        workspace.working_path.rename(archived)
        created.append(f"archive:{archived.name}")

    if not workspace.working_path.exists():
        _copy_tree(source, workspace.working_path)
        created.append("working")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(source),
        "root": str(workspace.root),
        "safe_path": str(workspace.safe_path),
        "working_path": str(workspace.working_path),
        "policy": (
            "Modifier uniquement MacAgentOS-WORKING. Garder MacAgentOS-SAFE intact. "
            "Promouvoir uniquement une candidate validée."
        ),
        "validation_python": str(Path(sys.executable)) if not getattr(sys, "frozen", False) else os.environ.get("MACAGENT_VALIDATION_PYTHON", ""),
        "created": created,
    }
    workspace.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "message": "Workspace self-update prêt.", **status(str(workspace.root))}


def _run(command: list[str], cwd: Path, timeout: int = 240) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/macagent-pycache")
    env.setdefault("CLANG_MODULE_CACHE_PATH", "/private/tmp/macagent-clang-cache")
    try:
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        return {
            "command": " ".join(command),
            "returncode": completed.returncode,
            "ok": completed.returncode == 0,
            "output": output[-20000:],
        }
    except Exception as exc:
        return {"command": " ".join(command), "returncode": -1, "ok": False, "output": str(exc)}


def _backend_needs_rebuild(working: Path, backend_binary: Path) -> bool:
    if not backend_binary.exists():
        return True
    try:
        backend_mtime = backend_binary.stat().st_mtime
    except OSError:
        return True
    sources = [
        working / "server.py",
        working / "self_update.py",
        working / "skills.py",
        working / "core.py",
        working / "llm_universal.py",
        working / "provider_connections.py",
        working / "opencode_auth.py",
        working / "opencode_bridge.py",
    ]
    for source in sources:
        try:
            if source.exists() and source.stat().st_mtime > backend_mtime:
                return True
        except OSError:
            return True
    return False


def validate(working_path: str = "") -> dict[str, Any]:
    working = _safe_source_path(working_path)
    if working is None:
        return {"status": "error", "message": "Working copy introuvable ou non autorisée."}

    python_bin = _validation_python(working)
    checks = [
        _run([python_bin, "-m", "py_compile", "server.py", "mcp_hub.py", "llm_universal.py", "provider_connections.py", "skills.py", "self_update.py"], working),
        _run([python_bin, "-m", "unittest", "discover", "-s", "tests"], working),
    ]
    native_path = working / "NativeMacApp"
    if native_path.exists():
        checks.append(_run(["swift", "build", "--package-path", "NativeMacApp"], working, timeout=300))

    ok = all(check.get("ok") for check in checks)
    return {
        "status": "ok" if ok else "error",
        "message": "Validation réussie." if ok else "Validation échouée.",
        "working_path": str(working),
        "checks": checks,
    }


def build_candidate(working_path: str = "", output_root: str = "") -> dict[str, Any]:
    working = _safe_source_path(working_path)
    if working is None:
        return {"status": "error", "message": "Working copy introuvable ou non autorisée."}

    validation = validate(str(working))
    if validation.get("status") != "ok":
        return {"status": "error", "message": "Build refusé: validation échouée.", "validation": validation}

    native_path = working / "NativeMacApp"
    build_script = native_path / "script" / "build_and_bundle.sh"
    backend_binary = working / "dist" / "MacAgentServer"
    pyinstaller = working / ".venv312" / "bin" / "pyinstaller"

    needs_backend_build = _backend_needs_rebuild(working, backend_binary)

    if needs_backend_build and pyinstaller.exists():
        backend_build = _run([str(pyinstaller), "--clean", "server.spec"], working, timeout=600)
        if not backend_build.get("ok"):
            return {"status": "error", "message": "Build backend échoué.", "backend_build": backend_build}
    elif needs_backend_build and (working / "server.spec").exists():
        backend_build = _run([_validation_python(working), "-m", "PyInstaller", "--clean", "server.spec"], working, timeout=600)
        if not backend_build.get("ok"):
            return {"status": "error", "message": "Build backend échoué.", "backend_build": backend_build}

    if not backend_binary.exists():
        return {
            "status": "error",
            "message": "Backend binaire absent. Installe/prépare .venv312 dans la working copy ou copie dist/MacAgentServer.",
            "validation": validation,
        }
    if not build_script.exists():
        return {"status": "error", "message": "Script build_and_bundle.sh introuvable.", "validation": validation}

    env = os.environ.copy()
    env["ENVIRONMENT"] = "prod"
    env["SIGN_IDENTITY"] = ""
    env["BACKEND_BINARY"] = str(backend_binary)
    if not env.get("CHATGPT_BRIDGE_BINARY"):
        bundled_codex = Path("/Applications/Codex.app/Contents/Resources/codex")
        if bundled_codex.exists():
            env["CHATGPT_BRIDGE_BINARY"] = str(bundled_codex)
    env.setdefault("CLANG_MODULE_CACHE_PATH", "/private/tmp/macagent-clang-cache")
    try:
        completed = subprocess.run(
            ["zsh", str(build_script)],
            cwd=native_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    except Exception as exc:
        return {"status": "error", "message": f"Build app impossible: {exc}", "validation": validation}

    if completed.returncode != 0:
        return {"status": "error", "message": "Build app échoué.", "output": output[-20000:], "validation": validation}

    produced_app = Path("/tmp/MacAgentOS-build/prod/Mac Agent OS.app")
    target_root = Path(output_root).expanduser() if output_root else workspace_from_root().root / "candidate-app"
    target_root.mkdir(parents=True, exist_ok=True)
    target_app = target_root / "Mac Agent OS.app"
    if target_app.exists():
        shutil.rmtree(target_app)
    if produced_app.exists():
        shutil.copytree(produced_app, target_app)

    return {
        "status": "ok",
        "message": "Candidate buildée. La version safe n'a pas été modifiée.",
        "candidate_app": str(target_app),
        "output": output[-12000:],
        "validation": validation,
    }


def diagnose(working_path: str = "") -> dict[str, Any]:
    """Run validation and turn failures into actionable self-repair hints."""
    validation = validate(working_path)
    suggestions: list[str] = []
    failed_checks = [check for check in validation.get("checks", []) if not check.get("ok")]

    for check in failed_checks:
        command = check.get("command", "")
        output = check.get("output", "")
        lowered = f"{command}\n{output}".lower()
        if "py_compile" in lowered or "syntaxerror" in lowered:
            suggestions.append("Corriger d'abord les erreurs de syntaxe Python indiquées par py_compile.")
        elif "unittest" in lowered or "failed" in lowered or "assertionerror" in lowered:
            suggestions.append("Lire le traceback du test, corriger le plus petit fichier concerné, puis relancer les tests.")
        elif "swift build" in lowered or "compile" in lowered:
            suggestions.append("Corriger les erreurs Swift signalées par swift build avant de rebuilder l'app.")
        else:
            suggestions.append("Inspecter la sortie de validation et corriger la cause la plus proche de la première erreur.")

    if validation.get("status") == "ok":
        suggestions.append("La working copy est saine. Tu peux builder une candidate.")

    return {
        "status": validation.get("status"),
        "message": "Diagnostic terminé.",
        "validation": validation,
        "suggestions": suggestions,
    }


def promote_candidate(
    candidate_app: str,
    target_app: str,
    backup_root: str = "",
    confirmation: str = "",
) -> dict[str, Any]:
    """Promote a candidate app to a target path, with backup and explicit confirmation."""
    if confirmation != "PROMOTE_MAC_AGENT_OS_CANDIDATE":
        return {
            "status": "error",
            "message": "Promotion refusée: confirmation explicite requise.",
            "required_confirmation": "PROMOTE_MAC_AGENT_OS_CANDIDATE",
        }

    candidate = Path(candidate_app).expanduser().resolve()
    target = Path(target_app).expanduser().resolve()
    if not candidate.exists() or not candidate.is_dir() or candidate.suffix != ".app":
        return {"status": "error", "message": "Candidate .app introuvable ou invalide."}
    if target.suffix != ".app":
        return {"status": "error", "message": "La cible doit être un bundle .app."}

    workspace = workspace_from_root()
    try:
        safe_path = workspace.safe_path.resolve()
        if str(target).startswith(str(safe_path)):
            return {"status": "error", "message": "Promotion refusée: la copie SAFE ne doit jamais être modifiée."}
    except Exception:
        pass

    backup_base = Path(backup_root).expanduser().resolve() if backup_root else target.parent / "Mac Agent OS backups"
    backup_base.mkdir(parents=True, exist_ok=True)
    backup_app = backup_base / f"{target.stem}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.app"

    if target.exists():
        shutil.copytree(target, backup_app)
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, target)

    return {
        "status": "ok",
        "message": "Candidate promue avec backup. Redémarre l'application cible pour utiliser cette version.",
        "target_app": str(target),
        "backup_app": str(backup_app) if backup_app.exists() else "",
    }


def rollback_candidate(backup_app: str, target_app: str, confirmation: str = "") -> dict[str, Any]:
    if confirmation != "ROLLBACK_MAC_AGENT_OS_BACKUP":
        return {
            "status": "error",
            "message": "Rollback refusé: confirmation explicite requise.",
            "required_confirmation": "ROLLBACK_MAC_AGENT_OS_BACKUP",
        }
    backup = Path(backup_app).expanduser().resolve()
    target = Path(target_app).expanduser().resolve()
    if not backup.exists() or backup.suffix != ".app":
        return {"status": "error", "message": "Backup .app introuvable."}
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(backup, target)
    return {"status": "ok", "message": "Rollback terminé.", "target_app": str(target)}


def auto_update(
    working_path: str = "",
    output_root: str = "",
    promote: bool = False,
    target_app: str = "",
    confirmation: str = "",
) -> dict[str, Any]:
    """Validate, build a candidate, and optionally promote it with backup."""
    diagnosis = diagnose(working_path)
    if diagnosis.get("status") != "ok":
        return {
            "status": "error",
            "message": "Auto-update arrêté: la working copy doit être corrigée avant le build.",
            "diagnosis": diagnosis,
        }

    build = build_candidate(working_path, output_root)
    if build.get("status") != "ok":
        return {"status": "error", "message": "Auto-update arrêté: build candidate échoué.", "build": build}

    result: dict[str, Any] = {
        "status": "ok",
        "message": "Auto-update terminé: candidate buildée.",
        "diagnosis": diagnosis,
        "build": build,
    }
    if promote:
        if not target_app:
            result["status"] = "error"
            result["message"] = "Candidate buildée, mais promotion impossible: target_app manquant."
            return result
        promotion = promote_candidate(
            str(build.get("candidate_app", "")),
            target_app,
            confirmation=confirmation,
        )
        result["promotion"] = promotion
        if promotion.get("status") != "ok":
            result["status"] = "error"
            result["message"] = "Candidate buildée, mais promotion refusée ou échouée."
        else:
            result["message"] = "Auto-update terminé: candidate buildée et promue avec backup."
    return result
