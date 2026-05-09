#!/usr/bin/env python3
"""
MCP Server for Messages
Auto-généré par Dynamic MCP Factory
App: Messages
Path: /System/Applications/Messages.app
Bundle ID: com.apple.MobileSMS
"""

import subprocess
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Messages")

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
    """Active Messages"""
    return run_as('tell app "Messages" to activate')
        
@mcp.tool()
def quit() -> str:
    """Ferme Messages"""
    return run_as('tell app "Messages" to quit')

if __name__ == "__main__":
    print("🚀 MCP for Messages starting...")
    mcp.run()
