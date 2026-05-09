"""
Commands Manager - Like OpenWork commands (/)

Commands are slash-prefixed triggers that run predefined actions
"""
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional


class Command:
    def __init__(self, name: str, pattern: str, action: str, description: str = ""):
        self.name = name
        self.pattern = pattern  # e.g., "/help", "/new-session"
        self.action = action  # The action to perform
        self.description = description
    
    def matches(self, text: str) -> bool:
        """Check if text matches this command"""
        return text.strip().startswith(self.pattern)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "pattern": self.pattern,
            "action": self.action,
            "description": self.description
        }


class CommandsManager:
    """Manages commands"""
    
    def __init__(self, commands_dir: str = None):
        if commands_dir is None:
            commands_dir = Path(__file__).parent / ".opencode" / "commands"
        self.commands_dir = Path(commands_dir)
        self.commands: Dict[str, Command] = {}
        self._load_commands()
        self._register_defaults()
    
    def _load_commands(self):
        """Load commands from directory"""
        if not self.commands_dir.exists():
            return
        
        for cmd_file in self.commands_dir.glob("*.md"):
            self._load_command_file(cmd_file)
    
    def _load_command_file(self, path: Path):
        """Load command from markdown file"""
        content = path.read_text()
        
        # Parse command from file
        name = path.stem
        pattern = f"/{name}"
        description = ""
        action = content
        
        # Extract description from first line
        lines = content.split("\n")
        if lines:
            # Look for description
            for line in lines:
                if line.strip() and not line.startswith("#"):
                    description = line.strip()
                    break
        
        command = Command(name, pattern, action, description)
        self.commands[name] = command
    
    def _register_defaults(self):
        """Register default commands"""
        defaults = [
            Command("help", "/help", "list_all", "Afficher l'aide"),
            Command("new-session", "/new", "create_session", "Créer une nouvelle session"),
            Command("sessions", "/sessions", "list_sessions", "Lister les sessions"),
            Command("skills", "/skills", "list_skills", "Lister les skills"),
            Command("settings", "/settings", "open_settings", "Ouvrir les paramètres"),
            Command("clear", "/clear", "clear_session", "Effacer la session actuelle"),
        ]
        
        for cmd in defaults:
            if cmd.name not in self.commands:
                self.commands[cmd.name] = cmd
    
    def find_command(self, text: str) -> Optional[Command]:
        """Find a command that matches the text"""
        for cmd in self.commands.values():
            if cmd.matches(text):
                return cmd
        return None
    
    def list(self) -> List[Command]:
        return list(self.commands.values())
    
    def get(self, name: str) -> Optional[Command]:
        return self.commands.get(name)
    
    def to_dict_list(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self.commands.values()]


# Singleton
_manager: Optional[CommandsManager] = None

def get_commands_manager() -> CommandsManager:
    global _manager
    if _manager is None:
        _manager = CommandsManager()
    return _manager


if __name__ == "__main__":
    print("Commands Manager Test")
    manager = get_commands_manager()
    print(f"Commands: {len(manager.list())}")
    for cmd in manager.list():
        print(f"  {cmd.pattern} - {cmd.description}")