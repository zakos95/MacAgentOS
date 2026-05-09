#!/usr/bin/env python3
"""
Safari MCP Server - Contrôle complet du navigateur Safari
Utilise FastMCP pour simplifier le code
"""

import subprocess
import json
import logging
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Safari-MCP")

mcp = FastMCP("Safari Control")


def run_as(script: str) -> str:
    """Exécute un script AppleScript"""
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.stdout else result.stderr.strip()
    except Exception as e:
        return f"Error: {str(e)}"


def js_escape(s: str) -> str:
    return s.replace("'", "\\'")


# === OUTILS MCP ===

@mcp.tool()
def safari_get_windows() -> str:
    """Liste toutes les fenêtres Safari ouvertes"""
    return run_as('tell application "Safari"\n set w to {}\n repeat with win in windows\n set end of w to {name:name of win, id:id of win}\n end repeat\n return w\nend tell')


@mcp.tool()
def safari_get_tabs() -> str:
    """Liste tous les onglets de la fenêtre active"""
    return run_as('tell application "Safari"\n set t to {}\n repeat with tab in tabs of window 1\n set end of t to {name:name of tab, url:URL of tab}\n end repeat\n return t\nend tell')


@mcp.tool()
def safari_get_url() -> str:
    """Récupère l'URL de l'onglet actuel"""
    return run_as('tell application "Safari"\n return URL of current tab of window 1\nend tell')


@mcp.tool()
def safari_get_content() -> str:
    """Récupère le contenu texte de la page actuelle"""
    return run_as('tell application "Safari"\n return do JavaScript "document.body.innerText" in current tab of window 1\nend tell')[:5000]


@mcp.tool()
def safari_navigate(url: str) -> str:
    """Navigue vers une URL"""
    run_as(f'tell application "Safari"\n tell window 1\n  set current tab to (make new tab with properties {{URL:"{url}"}})\n end tell\nend tell')
    return f"Navigué vers: {url}"


@mcp.tool()
def safari_back() -> str:
    """Reviens à la page précédente"""
    run_as('tell application "Safari"\n tell window 1\n  go back\n end tell\nend tell')
    return "Page précédente"


@mcp.tool()
def safari_forward() -> str:
    """Va à la page suivante"""
    run_as('tell application "Safari"\n tell window 1\n  go forward\n end tell\nend tell')
    return "Page suivante"


@mcp.tool()
def safari_reload() -> str:
    """Recharge la page actuelle"""
    run_as('tell application "Safari"\n tell window 1\n  reload\n end tell\nend tell')
    return "Page rechargée"


@mcp.tool()
def safari_new_tab(url: str = "") -> str:
    """Ouvre un nouvel onglet (optionnel: avec URL)"""
    if url:
        run_as(f'tell application "Safari"\n tell window 1\n  set current tab to (make new tab with properties {{URL:"{url}"}})\n end tell\nend tell')
        return f"Nouvel onglet ouvert: {url}"
    else:
        run_as('tell application "Safari"\n tell window 1\n  set current tab to (make new tab)\n end tell\nend tell')
        return "Nouvel onglet vide ouvert"


@mcp.tool()
def safari_close_tab() -> str:
    """Ferme l'onglet actuel"""
    run_as('tell application "Safari"\n tell window 1\n  close current tab\n end tell\nend tell')
    return "Onglet fermé"


@mcp.tool()
def safari_click(selector: str) -> str:
    """Clique sur un élément via sélecteur CSS"""
    run_as(f'tell application "Safari"\n tell window 1\n  do JavaScript "document.querySelector(\'{js_escape(selector)}\').click();" in current tab\n end tell\nend tell')
    return f"Cliqué sur: {selector}"


@mcp.tool()
def safari_fill(selector: str, value: str) -> str:
    """Remplit un champ de formulaire"""
    run_as(f'tell application "Safari"\n tell window 1\n  do JavaScript "document.querySelector(\'{js_escape(selector)}\').value = \'{js_escape(value)}\';" in current tab\n end tell\nend tell')
    return f"Rempli: {selector} = {value}"


@mcp.tool()
def safari_get_links() -> str:
    """Récupère tous les liens de la page actuelle"""
    r = run_as('tell application "Safari"\n tell window 1\n  return do JavaScript "JSON.stringify(Array.from(document.querySelectorAll(\"a\")).map(a=>({text:a.innerText,href:a.href})))" in current tab\n end tell\nend tell')
    try:
        links = json.loads(r)
        return json.dumps(links[:20], indent=2)
    except:
        return r


@mcp.tool()
def safari_get_forms() -> str:
    """Récupère tous les formulaires de la page"""
    return run_as('tell application "Safari"\n tell window 1\n  return do JavaScript "JSON.stringify(Array.from(document.querySelectorAll(\"form\")).map(f=>({action:f.action,inputs:Array.from(f.querySelectorAll(\"input,textarea,select\")).map(i=>({name:i.name,type:i.type,id:i.id}))})))" in current tab\n end tell\nend tell')[:3000]


@mcp.tool()
def safari_search(query: str) -> str:
    """Recherche un texte dans la page"""
    run_as(f'tell application "Safari"\n tell window 1\n  do JavaScript "window.find(\'{js_escape(query)}\');" in current tab\n end tell\nend tell')
    return f"Recherche: {query}"


@mcp.tool()
def safari_execute_js(code: str) -> str:
    """Exécute du code JavaScript arbitraire"""
    return run_as(f'tell application "Safari"\n tell window 1\n  return do JavaScript \'{js_escape(code)}\' in current tab\n end tell\nend tell')[:3000]


if __name__ == "__main__":
    logger.info("🦁 Safari MCP Server starting...")
    mcp.run()