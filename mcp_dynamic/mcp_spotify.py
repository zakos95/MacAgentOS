#!/usr/bin/env python3
"""
MCP Server for Spotify
Auto-généré par MCP Factory
Lecteur Spotify - lecture, pause, recherche
"""

import subprocess
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Spotify")

def run_as(script: str) -> str:
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.stdout else result.stderr.strip()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def play() -> str:
    """Lecture Spotify"""
    return run_as('tell application "Spotify" to play')

@mcp.tool()
def pause() -> str:
    """Pause Spotify"""
    return run_as('tell application "Spotify" to pause')

@mcp.tool()
def next(**kwargs) -> str:
    """Tool: next"""
    return "Tool en cours de développement"

@mcp.tool()
def previous(**kwargs) -> str:
    """Tool: previous"""
    return "Tool en cours de développement"

@mcp.tool()
def search(**kwargs) -> str:
    """Tool: search"""
    return "Tool en cours de développement"

@mcp.tool()
def get_current_track(**kwargs) -> str:
    """Tool: get_current_track"""
    return "Tool en cours de développement"

# AppleScript pour cette app

if __name__ == "__main__":
    print("🚀 MCP Spotify starting...")
    mcp.run()
