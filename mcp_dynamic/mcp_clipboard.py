#!/usr/bin/env python3
"""
MCP Server for Clipboard
Auto-généré par MCP Factory
Gestion du presse-papiers
"""

import subprocess
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Clipboard")

def run_as(script: str) -> str:
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.stdout else result.stderr.strip()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_clipboard() -> str:
    """Récupère le contenu du presse-papiers"""
    return run_as('the clipboard')

@mcp.tool()
def set_clipboard(text: str) -> str:
    """Copie du texte dans le presse-papiers"""
    return run_as(f'set the clipboard to "{text}"')

@mcp.tool()
def clear_clipboard(**kwargs) -> str:
    """Tool: clear_clipboard"""
    return "Tool en cours de développement"


# Clipboard - pas de AppleScript nécessaire, utilisation directe


if __name__ == "__main__":
    print("🚀 MCP Clipboard starting...")
    mcp.run()
