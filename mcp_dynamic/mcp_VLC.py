#!/usr/bin/env python3
"""
MCP Server for VLC
Auto-généré par Dynamic MCP Factory
App: VLC
Path: /Applications/VLC.app
Bundle ID: org.videolan.vlc
"""

import subprocess
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("VLC")

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
    """Active VLC"""
    return run_as('tell app "VLC" to activate')
        
@mcp.tool()
def quit() -> str:
    """Ferme VLC"""
    return run_as('tell app "VLC" to quit')

if __name__ == "__main__":
    print("🚀 MCP for VLC starting...")
    mcp.run()
