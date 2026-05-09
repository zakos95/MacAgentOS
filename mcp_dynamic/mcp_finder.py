#!/usr/bin/env python3
"""
MCP Server for Finder
Auto-généré par MCP Factory
Contrôle du Finder Mac - fichiers, dossiers, navigation
"""

import subprocess
import json
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

_HOME = str(Path.home())

def _safe_path(path: str) -> str:
    """Resolve and validate that a path stays within the user's home directory."""
    resolved = str(Path(path).resolve())
    if not (resolved == _HOME or resolved.startswith(_HOME + os.sep)):
        raise ValueError(f"Path '{path}' is outside the allowed directory.")
    return resolved

mcp = FastMCP("Finder")

def run_as(script: str) -> str:
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.stdout else result.stderr.strip()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def list_files(path: str = "") -> str:
    """Liste les fichiers d'un répertoire"""
    if not path:
        path = os.path.join(_HOME, "Desktop")
    try:
        safe = _safe_path(path)
        files = os.listdir(safe)
        return json.dumps(files)
    except ValueError as e:
        return f"Erreur de sécurité: {e}"
    except Exception as e:
        return f"Erreur: {e}"

@mcp.tool()
def create_folder(name: str, path: str = "") -> str:
    """Crée un nouveau dossier"""
    if not path:
        path = os.path.join(_HOME, "Desktop")
    # Reject traversal in folder name
    if "/" in name or "\\" in name or name in (".", ".."):
        return "Erreur de sécurité: nom de dossier invalide."
    try:
        safe = _safe_path(path)
        full_path = os.path.join(safe, name)
        os.makedirs(full_path, exist_ok=True)
        return f"Dossier créé: {full_path}"
    except ValueError as e:
        return f"Erreur de sécurité: {e}"
    except Exception as e:
        return f"Erreur: {e}"

@mcp.tool()
def move_file(**kwargs) -> str:
    """Tool: move_file"""
    return "Tool en cours de développement"

@mcp.tool()
def copy_file(**kwargs) -> str:
    """Tool: copy_file"""
    return "Tool en cours de développement"

@mcp.tool()
def delete_file(**kwargs) -> str:
    """Tool: delete_file"""
    return "Tool en cours de développement"

@mcp.tool()
def get_file_info(**kwargs) -> str:
    """Tool: get_file_info"""
    return "Tool en cours de développement"

@mcp.tool()
def open_file(**kwargs) -> str:
    """Tool: open_file"""
    return "Tool en cours de développement"

@mcp.tool()
def reveal_in_finder(**kwargs) -> str:
    """Tool: reveal_in_finder"""
    return "Tool en cours de développement"


# Finder AppleScript
finder_templates = {
    "list_windows": '''tell application "Finder"
 set w to {}
 repeat with win in windows
 set end of w to {name:name of win}
 end repeat
 return w
end tell''',
    "reveal_file": '''tell application "Finder"
 reveal POSIX file "{path}"
end tell''',
    "open_file": '''tell application "Finder"
 open POSIX file "{path}"
end tell''',
}

if __name__ == "__main__":
    print("🚀 MCP Finder starting...")
    mcp.run()
