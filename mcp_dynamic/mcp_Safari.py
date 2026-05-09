#!/usr/bin/env python3
"""
MCP Server for Safari
Auto-généré par Dynamic MCP Factory
App: Safari
Path: /Applications/Safari.app
Bundle ID: com.apple.Safari
"""

import subprocess
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Safari")

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
    """Active Safari"""
    return run_as('tell app "Safari" to activate')
        
@mcp.tool()
def quit() -> str:
    """Ferme Safari"""
    return run_as('tell app "Safari" to quit')

@mcp.tool()
def navigate(url: str) -> str:
    """Navigue vers une URL"""
    return run_as(f'tell app "Safari" to tell window 1 to set current tab\'s URL to "{_esc(url)}"')

@mcp.tool()
def get_url() -> str:
    """Récupère l'URL actuelle"""
    return run_as('tell app "Safari" to return URL of front document')

@mcp.tool()
def get_links() -> str:
    """Récupère tous les liens de la page"""
    js = 'JSON.stringify(Array.from(document.querySelectorAll("a")).map(a=>{{href:a.href, text:a.innerText}}))'
    return run_as(f'tell app "Safari" to do javascript "{js}" in front document')

if __name__ == "__main__":
    print("🚀 MCP for Safari starting...")
    mcp.run()
