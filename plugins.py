"""
MacAgent-OS Plugins - Extensible plugin system like OpenWork
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass

logger = logging.getLogger("MacAgent-Plugins")


@dataclass
class PluginInfo:
    """Plugin metadata"""
    name: str
    version: str
    description: str
    author: str
    tools: List[str]
    commands: List[str]
    enabled: bool = True


class Plugin:
    """Base plugin class"""
    name: str = "base"
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    
    def __init__(self):
        self.tools = []
        self.commands = []
        self.config = {}
    
    def get_tools(self) -> List[Dict]:
        """Return list of tools this plugin provides"""
        return self.tools
    
    def get_commands(self) -> List[str]:
        """Return list of commands"""
        return self.commands
    
    def on_load(self):
        """Called when plugin is loaded"""
        logger.info(f"Plugin {self.name} loaded")
    
    def on_unload(self):
        """Called when plugin is unloaded"""
        logger.info(f"Plugin {self.name} unloaded")


class PluginManager:
    """Manages plugins"""
    
    def __init__(self, plugins_dir: str = None):
        if plugins_dir is None:
            plugins_dir = Path(__file__).parent / "plugins"
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        
        self.plugins: Dict[str, Plugin] = {}
        self.tools: Dict[str, Callable] = {}
        self.commands: Dict[str, Callable] = {}
        self._load_builtin_plugins()
    
    def _load_builtin_plugins(self):
        """Load built-in plugins"""
        # File system plugin
        self.register_plugin(FileSystemPlugin())
        
        # Mac control plugin  
        self.register_plugin(MacControlPlugin())
        
        # Web search plugin
        self.register_plugin(WebSearchPlugin())
        
        logger.info(f"Loaded {len(self.plugins)} builtin plugins")
    
    def register_plugin(self, plugin: Plugin):
        """Register a plugin"""
        self.plugins[plugin.name] = plugin
        plugin.on_load()
        
        # Register tools
        for tool in plugin.get_tools():
            self.tools[tool["name"]] = tool
        
        # Register commands
        for cmd in plugin.get_commands():
            self.commands[cmd] = cmd
        
        logger.info(f"Registered plugin: {plugin.name}")
    
    def unregister_plugin(self, name: str) -> bool:
        """Unregister a plugin"""
        if name in self.plugins:
            plugin = self.plugins[name]
            plugin.on_unload()
            del self.plugins[name]
            
            # Remove tools
            for tool in plugin.get_tools():
                if tool["name"] in self.tools:
                    del self.tools[tool["name"]]
            
            return True
        return False
    
    def get_tool(self, name: str) -> Optional[Dict]:
        """Get a tool by name"""
        return self.tools.get(name)
    
    def get_command(self, name: str) -> Optional[str]:
        """Get a command"""
        return self.commands.get(name)
    
    def list_plugins(self) -> List[PluginInfo]:
        """List all plugins"""
        return [
            PluginInfo(
                name=p.name,
                version=p.version,
                description=p.description,
                author=p.author,
                tools=[t["name"] for t in p.get_tools()],
                commands=p.get_commands()
            )
            for p in self.plugins.values()
        ]
    
    def get_all_tools(self) -> List[Dict]:
        """Get all tools from all plugins"""
        return list(self.tools.values())
    
    def to_dict(self) -> Dict[str, Any]:
        """Get state as dict"""
        return {
            "plugins": [p.name for p in self.plugins.values()],
            "tools": len(self.tools),
            "commands": len(self.commands)
        }


# === BUILT-IN PLUGINS ===

class FileSystemPlugin(Plugin):
    """File system operations"""
    name = "filesystem"
    version = "0.1.0"
    description = "File system operations"
    author = "MacAgent-OS"
    
    def __init__(self):
        super().__init__()
        self.tools = [
            {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"path": "string"}
            },
            {
                "name": "write_file", 
                "description": "Write to a file",
                "parameters": {"path": "string", "content": "string"}
            },
            {
                "name": "list_directory",
                "description": "List directory contents",
                "parameters": {"path": "string"}
            },
            {
                "name": "create_directory",
                "description": "Create a directory",
                "parameters": {"path": "string"}
            },
            {
                "name": "delete_file",
                "description": "Delete a file",
                "parameters": {"path": "string"}
            }
        ]
        self.commands = ["/ls", "/cat", "/write", "/mkdir", "/rm"]


class MacControlPlugin(Plugin):
    """Mac control operations"""
    name = "mac-control"
    version = "0.1.0"
    description = "Control macOS applications and system"
    author = "MacAgent-OS"
    
    def __init__(self):
        super().__init__()
        self.tools = [
            {
                "name": "open_application",
                "description": "Open an application",
                "parameters": {"app_name": "string"}
            },
            {
                "name": "get_running_apps",
                "description": "Get running applications"
            },
            {
                "name": "kill_app",
                "description": "Force quit an application",
                "parameters": {"app_name": "string"}
            },
            {
                "name": "execute_applescript",
                "description": "Execute AppleScript",
                "parameters": {"script": "string"}
            },
            {
                "name": "get_system_info",
                "description": "Get system information"
            },
            {
                "name": "get_battery_status",
                "description": "Get battery status"
            },
            {
                "name": "volume_control",
                "description": "Control volume",
                "parameters": {"action": "string", "level": "number"}
            },
            {
                "name": "take_screenshot",
                "description": "Take a screenshot",
                "parameters": {"save_path": "string", "display": "boolean"}
            }
        ]
        self.commands = ["/open", "/apps", "/quit", "/screenshot", "/volume"]


class WebSearchPlugin(Plugin):
    """Web search plugin"""
    name = "web-search"
    version = "0.1.0"
    description = "Search the web"
    author = "MacAgent-OS"
    
    def __init__(self):
        super().__init__()
        self.tools = [
            {
                "name": "web_search",
                "description": "Search the web",
                "parameters": {"query": "string", "num_results": "number"}
            },
            {
                "name": "fetch_url",
                "description": "Fetch a URL",
                "parameters": {"url": "string"}
            }
        ]
        self.commands = ["/search", "/fetch"]


# Singleton
_manager: Optional[PluginManager] = None

def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    pm = get_plugin_manager()
    print("Plugins:")
    for p in pm.list_plugins():
        print(f"  {p.name} v{p.version}")
        print(f"    Tools: {len(p.tools)}")
        print(f"    Commands: {p.commands}")