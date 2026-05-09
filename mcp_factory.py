#!/usr/bin/env python3
"""
Dynamic MCP Factory v2 - Analyse le Mac et crée des MCPs pour TOUTES les applications
"""

import os
import json
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MCP-Factory-v2")

# Répertoire des MCPs dynamiques
from paths import get_project_root, get_app_support_dir
PROJECT_ROOT = get_project_root()
MCP_DYNAMIC_DIR = get_app_support_dir() / "mcp_dynamic"
MCP_DYNAMIC_DIR.mkdir(parents=True, exist_ok=True)
MCP_DYNAMIC_DIR.mkdir(exist_ok=True)

# Registry des MCPs
REGISTRY_FILE = MCP_DYNAMIC_DIR / "registry_v2.json"

# Applications système connues pour aides AppleScript
SYSTEM_APPS = {
    "Finder": "Finder",
    "Safari": "Safari", 
    "Chrome": "Google Chrome",
    "Terminal": "Terminal",
    "Notes": "Notes",
    "Messages": "Messages",
    "Mail": "Mail",
    "Calendar": "Calendar",
    "Contacts": "Contacts",
    "Reminders": "Reminders",
    "Music": "Music",
    "Spotify": "Spotify",
    "Slack": "Slack",
    "Discord": "Discord",
    "Telegram": "Telegram",
    "Zoom": "Zoom",
    "FaceTime": "FaceTime",
    "Photos": "Photos",
    "Preview": "Preview",
    "TextEdit": "TextEdit",
    "System Preferences": "System Preferences",
    "System Settings": "System Settings",
    "App Store": "App Store",
    "Calculator": "Calculator",
    "Dictionary": "Dictionary",
    "Font Book": "Font Book",
    "Keychain Access": "Keychain Access",
    "Activity Monitor": "Activity Monitor",
    "Disk Utility": "Disk Utility",
    "Terminal": "Terminal",
    "Console": "Console",
    "Grapher": "Grapher",
    "Home": "Home",
    "Stocks": "Stocks",
    "Weather": "Weather",
    "Maps": "Maps",
}


class DynamicMCPFactory:
    """Fabrique dynamique qui analyse le Mac et crée des MCPs à la demande"""
    
    def __init__(self):
        self.registry = self._load_registry()
        self.installed_apps = {}  # Cache des apps installées
        logger.info(f"🔧 Dynamic MCP Factory v2初始isée")
    
    def _load_registry(self) -> Dict:
        if REGISTRY_FILE.exists():
            with open(REGISTRY_FILE, 'r') as f:
                return json.load(f)
        return {"mcp_servers": {}, "last_updated": None}
    
    def _save_registry(self):
        self.registry["last_updated"] = datetime.now().isoformat()
        with open(REGISTRY_FILE, 'w') as f:
            json.dump(self.registry, f, indent=2)
    
    def scan_installed_apps(self) -> Dict[str, Dict]:
        """
        Scan le Mac pour trouver toutes les applications installées
        """
        if self.installed_apps:
            return self.installed_apps
        
        app_locations = [
            "/Applications",
            "/System/Applications", 
            "/System/Applications/Utilities",
            str(Path.home() / "Applications"),
            "/System/Applications/Accessibility",
        ]
        
        installed = {}
        
        for location in app_locations:
            if not os.path.exists(location):
                continue
                
            try:
                for item in os.listdir(location):
                    if item.endswith(".app"):
                        app_name = item.replace(".app", "")
                        app_path = os.path.join(location, item)
                        
                        # Tente de trouver le bundle identifier
                        bundle_id = self._get_bundle_id(app_path)
                        
                        installed[app_name.lower()] = {
                            "name": app_name,
                            "path": app_path,
                            "bundle_id": bundle_id,
                            "location": location
                        }
            except PermissionError:
                continue
        
        self.installed_apps = installed
        logger.info(f"📱 {len(installed)} applications trouvées sur le Mac")
        return installed
    
    def _get_bundle_id(self, app_path: str) -> Optional[str]:
        """Récupère le bundle identifier d'une app"""
        try:
            result = subprocess.run(
                ["osascript", "-e", f'tell app "Finder" to get id of app "{app_path}"'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip()
        except:
            return None
    
    def get_app_from_request(self, user_request: str) -> Optional[Dict]:
        """
        Détecte l'application cible depuis la requête utilisateur
        en comparant avec les apps installées sur le Mac
        """
        request_lower = user_request.lower()
        apps = self.scan_installed_apps()
        
        # Analyse plus intelligente - extrait les mots clés de la requête
        words = user_request.lower().split()
        
        # Recherche par nom d'application
        best_match = None
        best_score = 0
        
        for app_key, app_info in apps.items():
            app_name_lower = app_info["name"].lower()
            
            # Score basé sur la correspondance
            score = 0
            
            # Mot exact dans la requête (ex: "spotify" dans "ouvre spotify")
            if app_name_lower in request_lower:
                score = 100
            # Le nom de l'app contient un mot de la requête (ex: "note" dans "crée une note")
            else:
                for word in words:
                    if len(word) > 2 and (word in app_name_lower or app_name_lower in word):
                        # Bonus si c'est un mot significatif
                        score = max(score, 60)
            
            # Bonus pour les applications système courantes
            common_apps = ["finder", "safari", "chrome", "terminal", "notes", "messages", 
                          "spotify", "music", "slack", "discord", "mail", "calendar"]
            if any(c in app_name_lower for c in common_apps) and score > 0:
                score += 20
            
            if score > best_score:
                best_score = score
                best_match = app_info
        
        # Seuil plus bas pour permettre plus de correspondances
        if best_match and best_score > 20:
            logger.info(f"   → Détecté: {best_match['name']} (score: {best_score})")
            return best_match
        
        return None
    
    def is_app_supported(self, app_name: str) -> bool:
        """Vérifie si l'app supporte AppleScript"""
        app_lower = app_name.lower()
        
        # Apps avec AppleScript connu
        supported_apps = [
            "finder", "safari", "chrome", "terminal", "notes", "messages",
            "mail", "calendar", "contacts", "reminders", "music", "spotify",
            "slack", "discord", "telegram", "zoom", "facetime", "photos",
            "preview", "textedit", "system preferences", "system settings",
            "calculator", "dictionary", "font book", "activity monitor",
            "disk utility", "console", "home", "stocks", "weather", "maps"
        ]
        
        return any(supported in app_lower for supported in supported_apps)
    
    def create_mcp_for_app(self, app_info: Dict, force: bool = False) -> str:
        """
        Crée un MCP server pour une application spécifique
        """
        app_name = app_info["name"]
        app_key = app_name.lower()
        
        # Vérifie si déjà existant
        if not force and app_key in self.registry.get("mcp_servers", {}):
            logger.info(f"📦 MCP pour {app_name} existe déjà")
            return self.registry["mcp_servers"][app_key]["file"]
        
        # Génère le code MCP
        mcp_code = self._generate_dynamic_mcp(app_info)
        
        # Sauvegarde le fichier
        safe_name = app_name.replace(" ", "_").replace("/", "_")
        mcp_file = MCP_DYNAMIC_DIR / f"mcp_{safe_name}.py"
        
        try:
            with open(mcp_file, 'w') as f:
                f.write(mcp_code)
        except Exception as e:
            logger.error(f"Failed to write MCP file: {e}")
            return ""
        
        # Enregistre dans le registry
        self.registry["mcp_servers"][app_key] = {
            "name": app_name,
            "bundle_id": app_info.get("bundle_id"),
            "path": app_info["path"],
            "file": str(mcp_file),
            "created": datetime.now().isoformat(),
            "location": app_info.get("location", ""),
            "tools": self._get_app_tools(app_name)
        }
        self._save_registry()
        
        logger.info(f"✅ MCP créé pour: {app_name}")
        return str(mcp_file)
    
    def _generate_dynamic_mcp(self, app_info: Dict) -> str:
        """Génère un MCP server pour une application"""
        app_name = app_info["name"]
        bundle_id = app_info.get("bundle_id", "")
        
        tools = self._get_app_tools(app_name)
        tools_code = self._generate_tools_code(app_name, tools)
        
        # Utilise des doubles accolades pour les f-strings qui seront évalués après
        return f'''#!/usr/bin/env python3
"""
MCP Server for {app_name}
Auto-généré par Dynamic MCP Factory
App: {app_name}
Path: {app_info['path']}
Bundle ID: {bundle_id}
"""

import subprocess
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("{app_name}")

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
        return f"Error: {{str(e)}}"

def run_js(app_name: str, js_code: str) -> str:
    """Exécute du JavaScript dans une application"""
    escaped_js = js_code.replace('"', '\\\\"')
    script = f'tell app "{{app_name}}" to do javascript "{{escaped_js}}" in front window'
    return run_as(script)

{tools_code}

if __name__ == "__main__":
    print("🚀 MCP for {app_name} starting...")
    mcp.run()
'''
    
    def _get_app_tools(self, app_name: str) -> List[str]:
        """Retourne les outils disponibles pour une application"""
        
        # Outils génériques pour toutes les apps
        generic_tools = [
            "activate",
            "quit",
            "get_version",
        ]
        
        # Outils spécifiques par app
        app_tools = {
            "finder": [
                "list_files", "create_folder", "delete_file", "move_file",
                "copy_file", "open_file", "reveal_in_finder", "get_selection",
                "make_alias", "empty_trash"
            ],
            "safari": [
                "navigate", "get_url", "get_title", "get_content", "get_links",
                "get_forms", "click_element", "fill_input", "search",
                "new_tab", "close_tab", "reload", "back", "forward"
            ],
            "chrome": [
                "navigate", "get_url", "get_title", "get_tabs", "new_tab",
                "close_tab", "reload", "execute_js"
            ],
            "terminal": [
                "execute_command", "new_tab", "new_window", "list_processes"
            ],
            "notes": [
                "list_notes", "get_note", "create_note", "update_note", 
                "delete_note", "search_notes"
            ],
            "messages": [
                "list_conversations", "send_message", "get_messages",
                "search_messages"
            ],
            "mail": [
                "send_email", "list_emails", "get_email", "search_emails"
            ],
            "calendar": [
                "list_calendars", "list_events", "create_event", 
                "update_event", "delete_event"
            ],
            "contacts": [
                "list_contacts", "get_contact", "create_contact",
                "update_contact", "delete_contact", "search_contacts"
            ],
            "reminders": [
                "list_reminders", "create_reminder", "complete_reminder",
                "delete_reminder", "list_lists"
            ],
            "spotify": [
                "play", "pause", "next_track", "previous_track", 
                "get_current_track", "set_volume", "search"
            ],
            "music": [
                "play", "pause", "next_track", "previous_track",
                "get_current_track", "set_volume"
            ],
            "slack": [
                "send_message", "list_channels", "search_messages"
            ],
            "discord": [
                "send_message", "list_servers", "list_channels"
            ],
            "zoom": [
                "start_meeting", "join_meeting", "list_meetings"
            ],
            "calendar": [
                "list_events", "create_event", "update_event", "delete_event"
            ]
        }
        
        app_lower = app_name.lower()
        
        # Combine outils génériques + spécifiques
        tools = generic_tools.copy()
        
        for key, tool_list in app_tools.items():
            if key in app_lower:
                tools.extend(tool_list)
                break
        
        return tools
    
    def _generate_tools_code(self, app_name: str, tools: List[str]) -> str:
        """Génère le code des outils MCP"""
        
        code = []
        
        # Outils génériques
        code.append(f'''
@mcp.tool()
def activate() -> str:
    """Active {app_name}"""
    return run_as('tell app "{app_name}" to activate')
        
@mcp.tool()
def quit() -> str:
    """Ferme {app_name}"""
    return run_as('tell app "{app_name}" to quit')''')
        
        # Outils spécifiques selon l'app
        if any(x in app_name.lower() for x in ["finder", "finder"]):
            code.extend([
                '''
@mcp.tool()
def list_files(path: str = "") -> str:
    """Liste les fichiers d'un répertoire"""
    if not path:
        path = os.path.expanduser("~/Desktop")
    try:
        import os
        files = os.listdir(path)
        return json.dumps(files[:50])
    except Exception as e:
        return f"Erreur: {str(e)}"''',
                '''
@mcp.tool()
def open_file(path: str) -> str:
    """Ouvre un fichier avec {app_name}"""
    return run_as(f'tell app "Finder" to open POSIX file "{path}"')'''
            ])
        
        if "safari" in app_name.lower():
            code.extend([
                '''
@mcp.tool()
def navigate(url: str) -> str:
    """Navigue vers une URL"""
    return run_as(f'tell app "Safari" to tell window 1 to set current tab\'s URL to "{url}"')''',
                '''
@mcp.tool()
def get_url() -> str:
    """Récupère l'URL actuelle"""
    return run_as('tell app "Safari" to return URL of front document')''',
                '''
@mcp.tool()
def get_links() -> str:
    """Récupère tous les liens de la page"""
    js = 'JSON.stringify(Array.from(document.querySelectorAll("a")).map(a=>{{href:a.href, text:a.innerText}}))'
    return run_as(f'tell app "Safari" to do javascript "{js}" in front document')'''
            ])
        
        if "terminal" in app_name.lower():
            code.extend([
                '''
@mcp.tool()
def execute_command(command: str, timeout: int = 30) -> str:
    """Exécute une commande dans Terminal"""
    return run_as(f'tell app "Terminal" to do script "{command}"')''',
                '''
@mcp.tool()
def new_tab(command: str = "") -> str:
    """Ouvre un nouvel onglet Terminal"""
    if command:
        return run_as(f'tell app "Terminal" to do script "{command}" in front window')
    return run_as('tell app "Terminal" to tell front window to do script ""')'''
            ])
        
        if "notes" in app_name.lower():
            code.extend([
                '''
@mcp.tool()
def list_notes() -> str:
    """Liste toutes les notes"""
    return run_as('tell app "Notes" to set n to name of every note; return n')''',
                '''
@mcp.tool()
def create_note(title: str, body: str = "") -> str:
    """Crée une nouvelle note"""
    return run_as(f'tell app "Notes" to tell account "iCloud" to make new note at folder "Notes" with properties {{name:"{title}", body:"{body}"}}')'''
            ])
        
        if "spotify" in app_name.lower():
            code.extend([
                '''
@mcp.tool()
def play() -> str:
    """Lecture"""
    return run_as('tell app "Spotify" to play')''',
                '''
@mcp.tool()
def pause() -> str:
    """Pause"""
    return run_as('tell app "Spotify" to pause')''',
                '''
@mcp.tool()
def next_track() -> str:
    """Piste suivante"""
    return run_as('tell app "Spotify" to next track')''',
                '''
@mcp.tool()
def get_current_track() -> str:
    """Piste actuelle"""
    return run_as('tell app "Spotify" to return name of current track & " - " & artist of current track')'''
            ])
        
        return "\n".join(code)
    
    def process_request(self, user_request: str) -> Dict[str, Any]:
        """
        Protocol complet:
        1. Analyse le Mac pour détecter l'application demandée
        2. Vérifie si un MCP existe déjà
        3. Si non, crée un nouveau MCP dynamique
        """
        logger.info(f"🔍 Analyse requête: {user_request}")
        
        # 1. Scan le Mac si pas encore fait
        if not self.installed_apps:
            self.scan_installed_apps()
        
        # 2. Détecte l'application cible
        app_info = self.get_app_from_request(user_request)
        
        if not app_info:
            return {
                "action": "app_not_found",
                "message": f"Aucune application détectée dans: {user_request}",
                "suggestion": "Essayez de spécifier le nom de l'application (ex: 'Spotify', 'Finder', 'Safari')",
                "mcp_created": False
            }
        
        app_name = app_info["name"]
        app_key = app_name.lower()
        
        logger.info(f"📱 Application détectée: {app_name}")
        
        # 3. Vérifie si le MCP existe déjà
        if app_key in self.registry.get("mcp_servers", {}):
            mcp_info = self.registry["mcp_servers"][app_key]
            logger.info(f"📦 MCP existe: {mcp_info['file']}")
            
            return {
                "action": "use_existing",
                "app": app_name,
                "bundle_id": app_info.get("bundle_id"),
                "path": app_info["path"],
                "mcp_created": False,
                "mcp_file": mcp_info["file"],
                "tools": mcp_info.get("tools", [])
            }
        
        # 4. Crée le MCP
        logger.info(f"🆕 Création MCP pour: {app_name}")
        mcp_file = self.create_mcp_for_app(app_info)
        
        return {
            "action": "created",
            "app": app_name,
            "bundle_id": app_info.get("bundle_id"),
            "path": app_info["path"],
            "mcp_created": True,
            "mcp_file": mcp_file,
            "tools": self._get_app_tools(app_name)
        }
    
    def list_installed_apps(self) -> Dict:
        """Liste toutes les applications installées"""
        if not self.installed_apps:
            self.scan_installed_apps()
        return self.installed_apps
    
    def list_created_mcps(self) -> Dict:
        """Liste les MCPs créés"""
        return self.registry.get("mcp_servers", {})
    
    def get_mcp_for_app(self, app_name: str) -> Optional[Dict]:
        """Récupère les infos MCP pour une app"""
        app_key = app_name.lower()
        return self.registry.get("mcp_servers", {}).get(app_key)


# === CLI pour test ===
if __name__ == "__main__":
    factory = DynamicMCPFactory()
    
    print("\n🔧 Dynamic MCP Factory v2 - Test")
    print("=" * 60)
    
    # Scan les apps
    apps = factory.scan_installed_apps()
    print(f"\n📱 Applications installées: {len(apps)}")
    
    # Test détection
    test_requests = [
        "Ouvre Spotify",
        "Navigue sur Safari vers google.com",
        "Crée une note dans Notes",
        "Ouvre le Finder",
        "Lance Terminal et exécute ls",
        "Envoie un message sur Slack",
        "Ouvre Photos",
    ]
    
    for req in test_requests:
        print(f"\n🔍 {req}")
        result = factory.process_request(req)
        print(f"   → Action: {result['action']}")
        print(f"   → App: {result.get('app', 'N/A')}")
        print(f"   → Créé: {result['mcp_created']}")
        print(f"   → Fichier: {result.get('mcp_file', 'N/A')[:50]}...")
