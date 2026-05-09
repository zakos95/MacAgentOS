#!/usr/bin/env python3
"""
MCP Server for Notes
Auto-généré par Dynamic MCP Factory
App: Notes
Path: /System/Applications/Notes.app
Bundle ID: com.apple.Notes
"""

import subprocess
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Notes")

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
    """Active Notes"""
    return run_as('tell app "Notes" to activate')
        
@mcp.tool()
def quit() -> str:
    """Ferme Notes"""
    return run_as('tell app "Notes" to quit')

@mcp.tool()
def list_notes() -> str:
    """Liste toutes les notes"""
    return run_as('tell app "Notes" to set n to name of every note; return n')

@mcp.tool()
def create_note(title: str, body: str = "") -> str:
    """Crée une nouvelle note"""
    return run_as(f'tell app "Notes" to tell account "iCloud" to make new note at folder "Notes" with properties {{name:"{title}", body:"{body}"}}')

if __name__ == "__main__":
    print("🚀 MCP for Notes starting...")
    mcp.run()
