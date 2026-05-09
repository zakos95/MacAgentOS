"""
Agents Manager - Like OpenWork agents

Agents are reusable AI configurations with extra context and tools
"""
import json
from pathlib import Path
from typing import Dict, List, Any, Optional


class Agent:
    def __init__(
        self,
        name: str,
        model: str,
        provider: str = "ollama",
        system_prompt: str = "",
        tools: List[str] = None,
        enabled: bool = True
    ):
        self.name = name
        self.model = model
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.enabled = enabled
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "provider": self.provider,
            "system_prompt": self.system_prompt,
            "tools": self.tools,
            "enabled": self.enabled
        }


class AgentsManager:
    """Manages agents"""
    
    def __init__(self, agents_dir: str = None):
        if agents_dir is None:
            agents_dir = Path(__file__).parent / ".opencode" / "agents"
        self.agents_dir = Path(agents_dir)
        self.agents: Dict[str, Agent] = {}
        self._load_agents()
        self._register_defaults()
    
    def _load_agents(self):
        """Load agents from directory"""
        if not self.agents_dir.exists():
            return
        
        for agent_file in self.agents_dir.glob("*.md"):
            self._load_agent_file(agent_file)
    
    def _load_agent_file(self, path: Path):
        """Load agent from markdown file"""
        content = path.read_text()
        name = path.stem
        
        # Parse metadata
        model = "dolphin3:latest"
        provider = "ollama"
        system_prompt = ""
        tools = []
        enabled = True
        
        lines = content.split("\n")
        for line in lines:
            if line.startswith("model:"):
                model = line.split(":", 1)[1].strip()
            elif line.startswith("provider:"):
                provider = line.split(":", 1)[1].strip()
            elif line.startswith("tools:"):
                tools = [t.strip() for t in line.split(":", 1)[1].split(",")]
            elif line.startswith("enabled:"):
                enabled = line.split(":", 1)[1].strip().lower() == "true"
            elif line.startswith("## System Prompt"):
                # Find next heading
                idx = lines.index(line) + 1
                while idx < len(lines):
                    if lines[idx].startswith("## "):
                        break
                    system_prompt += lines[idx] + "\n"
                    idx += 1
        
        agent = Agent(name, model, provider, system_prompt.strip(), tools, enabled)
        self.agents[name] = agent
    
    def _register_defaults(self):
        """Register default agents"""
        defaults = [
            Agent(
                "default",
                "dolphin3:latest",
                "ollama",
                "You are MacAgent-OS, a helpful AI assistant.",
                [],
                True
            ),
            Agent(
                "coder",
                "llama3:70b",
                "ollama",
                "You are an expert programmer. Focus on code quality and best practices.",
                ["read_file", "write_file", "execute_bash"],
                True
            ),
            Agent(
                "researcher",
                "qwen2.5:14b",
                "ollama",
                "You are a research assistant. Be thorough and cite sources.",
                ["web_search"],
                True
            ),
        ]
        
        for agent in defaults:
            if agent.name not in self.agents:
                self.agents[agent.name] = agent
    
    def list(self) -> List[Agent]:
        return [a for a in self.agents.values() if a.enabled]
    
    def get(self, name: str) -> Optional[Agent]:
        return self.agents.get(name)
    
    def to_dict_list(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self.agents.values()]


# Singleton
_manager: Optional[AgentsManager] = None

def get_agents_manager() -> AgentsManager:
    global _manager
    if _manager is None:
        _manager = AgentsManager()
    return _manager


if __name__ == "__main__":
    print("Agents Manager Test")
    manager = get_agents_manager()
    print(f"Agents: {len(manager.list())}")
    for agent in manager.list():
        print(f"  {agent.name}: {agent.model} ({agent.provider})")