#!/usr/bin/env python3
"""
MCP Server for Terminal
Auto-généré par Dynamic MCP Factory
App: Terminal
Path: /System/Applications/Utilities/Terminal.app
Bundle ID: com.apple.Terminal
"""

import subprocess
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Terminal")

def _esc(value: str) -> str:
    """Escapes a string for safe embedding inside an AppleScript double-quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')

def run_as(script: str) -> str:
    """Exécute un script AppleScript"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout.strip() if result.stdout else result.stderr.strip()
    except Exception as e:
        return f"Error: {str(e)}"

def run_js(app_name: str, js_code: str) -> str:
    """Exécute du JavaScript dans une application"""
    escaped_js = js_code.replace('"', '\\"')
    script = f'tell app "{app_name}" to do javascript "{escaped_js}" in front window'
    return run_as(script)


@mcp.tool()
def activate() -> str:
    """Active Terminal"""
    return run_as('tell app "Terminal" to activate')
        
@mcp.tool()
def quit() -> str:
    """Ferme Terminal"""
    return run_as('tell app "Terminal" to quit')

@mcp.tool()
def execute_command(command: str, timeout: int = 30) -> str:
    """Exécute une commande dans Terminal"""
    return run_as(f'tell app "Terminal" to do script "{_esc(command)}"')

@mcp.tool()
def new_tab(command: str = "") -> str:
    """Ouvre un nouvel onglet Terminal"""
    if command:
        return run_as(f'tell app "Terminal" to do script "{_esc(command)}" in front window')
    return run_as('tell app "Terminal" to tell front window to do script ""')

if __name__ == "__main__":
    print("🚀 MCP for Terminal starting...")
    mcp.run()
