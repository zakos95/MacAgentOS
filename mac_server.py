from mcp.server.fastmcp import FastMCP
import subprocess
import shlex
import os
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Création du serveur MCP
mcp = FastMCP("Mac Control Server")

# --- OUTILS DU SERVEUR MCP ---

@mcp.tool()
def execute_bash(command: str, timeout: int = 30) -> str:
    """
    Exécute une commande Bash sur le Mac.
    ATTENTION : Cette commande sera exécutée avec les privilèges de l'utilisateur actuel.
    
    Args:
        command: La commande Bash à exécuter (ex: 'ls -la', 'pwd', 'whoami').
        timeout: Temps maximum d'exécution en secondes (défaut: 30).
    """
    logging.info(f"Exécution Bash demandée (longueur: {len(command)} chars)")

    try:
        args = shlex.split(command)
    except ValueError as e:
        return f"Erreur : Commande invalide — {e}"

    # Reject shell built-ins and dangerous executables
    _BLOCKED = {"rm", "mkfs", "dd", "shred", "fdisk", "format", "shutdown", "reboot"}
    if args and args[0].rstrip("/").split("/")[-1] in _BLOCKED:
        return f"Erreur de sécurité : Commande '{args[0]}' interdite par le serveur."

    try:
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        output = result.stdout + result.stderr
        return output if output else "Commande exécutée avec succès (aucun retour)."
        
    except subprocess.TimeoutExpired:
        return f"Erreur : La commande a dépassé le délai de {timeout} secondes."
    except Exception as e:
        return f"Erreur d'exécution : {str(e)}"

@mcp.tool()
def open_application(app_name: str) -> str:
    """
    Ouvre ou met au premier plan une application macOS.
    
    Args:
        app_name: Le nom exact de l'application (ex: 'Safari', 'Notes', 'Terminal').
    """
    logging.info(f"Ouverture d'application demandée : {app_name}")

    safe_name = app_name.replace("\\", "\\\\").replace('"', '\\"')
    apple_script = f'tell application "{safe_name}" to activate'
    try:
        subprocess.run(["osascript", "-e", apple_script], check=True, capture_output=True, text=True)
        return f"Succès : L'application '{app_name}' a été ouverte ou mise au premier plan."
    except subprocess.CalledProcessError as e:
        return f"Erreur : Impossible d'ouvrir '{app_name}'. Vérifiez le nom. Détails: {e.stderr}"
    except Exception as e:
        return f"Erreur AppleScript : {str(e)}"

@mcp.tool()
def execute_applescript(script: str) -> str:
    """
    Exécute un script AppleScript personnalisé pour contrôler macOS.
    Permet d'interagir avec l'interface graphique, de cliquer sur des boutons, etc.
    
    Args:
        script: Le code AppleScript complet à exécuter.
    """
    logging.info(f"Exécution AppleScript demandée (longueur: {len(script)} chars)")
    
    try:
        result = subprocess.run(
            ["osascript", "-e", script], 
            check=True, 
            capture_output=True, 
            text=True
        )
        output = result.stdout.strip()
        return output if output else "Script exécuté avec succès."
    except subprocess.CalledProcessError as e:
        return f"Erreur d'exécution AppleScript : {e.stderr}"
    except Exception as e:
        return f"Erreur système : {str(e)}"

@mcp.tool()
def get_system_info() -> str:
    """
    Récupère des informations de base sur le système Mac (Version OS, CPU, Mémoire).
    """
    try:
        os_ver = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip()
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        return f"macOS Version: {os_ver}\nProcesseur: {cpu}"
    except Exception as e:
        return f"Erreur lors de la récupération des infos système : {e}"

@mcp.tool()
def get_running_apps() -> str:
    """
    Liste les applications actuellement en cours d'exécution sur le Mac.
    """
    try:
        script = '''
        tell application "System Events"
            set appList to name of every process whose background only is false
            return appList
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        apps = [a.strip() for a in result.stdout.strip().split(", ") if a.strip()]
        return " Applications actives:\n" + "\n".join(f"  • {a}" for a in sorted(apps))
    except Exception as e:
        return f"Erreur : {e}"

@mcp.tool()
def kill_app(app_name: str) -> str:
    """
    Force la fermeture d'une application.
    
    Args:
        app_name: Nom de l'application à fermer.
    """
    try:
        safe_name = app_name.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "{safe_name}" to quit'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        return f"✅ L'application '{app_name}' a été fermée."
    except Exception as e:
        return f"Erreur : Impossible de fermer '{app_name}'. {e}"

@mcp.tool()
def take_screenshot(save_path: str = "", display: bool = True) -> str:
    """
    Prend une capture d'écran.
    
    Args:
        save_path: Chemin où sauvegarder (défaut: Bureau).
        display: Si true, capture todo l'écran; sinon zone de capture.
    """
    import datetime
    import shutil
    
    home = os.path.expanduser("~")
    if not save_path:
        save_path = os.path.join(home, "Desktop")

    # Resolve and restrict to within the user's home directory
    resolved = os.path.realpath(os.path.abspath(save_path))
    if not resolved.startswith(home + os.sep) and resolved != home:
        return f"Erreur de sécurité : Chemin '{save_path}' non autorisé."

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"screenshot_{timestamp}.png"
    filepath = os.path.join(resolved, filename)
    
    try:
        if display:
            subprocess.run(["screencapture", filepath], check=True)
        else:
            subprocess.run(["screencapture", "-i", filepath], check=True)
        return f"✅ Capture d'écran enregistrée: {filepath}"
    except Exception as e:
        return f"Erreur : {e}"

@mcp.tool()
def volume_control(action: str, level: int = 50) -> str:
    """
    Contrôle le volume du système.
    
    Args:
        action: "get", "set", "up", "down", "mute", "unmute"
        level: Niveau de volume 0-100 (pour action "set")
    """
    try:
        if action == "get":
            script = 'output volume of (get volume settings)'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return f"Volume actuel: {result.stdout.strip()}%"
        
        elif action == "set":
            subprocess.run(["osascript", "-e", f"set volume {level}"], check=True)
            return f"✅ Volume mis à {level}%"
        
        elif action == "up":
            subprocess.run(["osascript", "-e", "set volume output volume of (get volume settings) + 10"], check=True)
            return "🔊 Volume augmenté"
        
        elif action == "down":
            subprocess.run(["osascript", "-e", "set volume output volume of (get volume settings) - 10"], check=True)
            return "🔉 Volume diminué"
        
        elif action == "mute":
            subprocess.run(["osascript", "-e", "set volume with muted"], check=True)
            return "🔇 Son coupé"
        
        elif action == "unmute":
            subprocess.run(["osascript", "-e", "set volume without muted"], check=True)
            return "🔊 Son réactivé"
        
        return "Action non reconnue"
    except Exception as e:
        return f"Erreur : {e}"

@mcp.tool()
def get_battery_status() -> str:
    """
    Retourne le statut de la batterie (pourcentage, état de charge, temps restant).
    """
    try:
        script = '''
        tell application "System Events"
            set batteryPercent to (do shell script "pmset -g btp | head -1 | sed 's/.*\\([0-9]\\)%.*/\\1/'")
            set charging to (do shell script "pmset -g btp | grep -c 'charging'")
            return batteryPercent & "%" & return & if charging is "1" then "En charge" else "Sur batterie"
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return f"🔋 Batterie: {result.stdout.strip()}"
    except Exception as e:
        return f"Erreur : {e}"

# --- POINT D'ENTRÉE ---
if __name__ == "__main__":
    # Lancement du serveur en mode stdio (standard pour MCP)
    logging.info("Démarrage du serveur MCP Mac Control sur stdio...")
    mcp.run()
