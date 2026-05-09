"""
Templates - Sauvegarde et rejoue des workflows
Inspired by OpenWork templates concept
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
import sessions


class TemplateStep:
    def __init__(self, step_type: str, content: str, tool_calls: Optional[List[Dict]] = None):
        self.step_type = step_type  # "user_message" | "assistant_response" | "tool_call"
        self.content = content
        self.tool_calls = tool_calls or []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_type": self.step_type,
            "content": self.content,
            "tool_calls": self.tool_calls
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TemplateStep":
        return cls(
            step_type=data["step_type"],
            content=data["content"],
            tool_calls=data.get("tool_calls", [])
        )


class Template:
    def __init__(self, template_id: Optional[str] = None, name: str = "", description: str = ""):
        self.id = template_id or str(uuid.uuid4())[:8]
        self.name = name
        self.description = description
        self.steps: List[TemplateStep] = []
        self.created_at = datetime.now().isoformat()
        self.usage_count = 0
    
    def add_step(self, step_type: str, content: str, tool_calls: Optional[List[Dict]] = None):
        step = TemplateStep(step_type, content, tool_calls)
        self.steps.append(step)
        return step
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "usage_count": self.usage_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Template":
        template = cls(template_id=data["id"], name=data.get("name", ""), description=data.get("description", ""))
        template.steps = [TemplateStep.from_dict(s) for s in data.get("steps", [])]
        template.created_at = data.get("created_at", template.created_at)
        template.usage_count = data.get("usage_count", 0)
        return template
    
    def increment_usage(self):
        self.usage_count += 1


class TemplatesManager:
    def __init__(self, storage_dir: str = None):
        if storage_dir is None:
            storage_dir = Path(__file__).parent / "data" / "templates"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.templates: Dict[str, Template] = {}
        self._load_templates()
    
    def _template_file(self, template_id: str) -> Path:
        return self.storage_dir / f"{template_id}.json"
    
    def _load_templates(self):
        if not self.storage_dir.exists():
            return
        
        for template_file in self.storage_dir.glob("*.json"):
            with open(template_file, "r") as f:
                data = json.load(f)
                template = Template.from_dict(data)
                self.templates[template.id] = template
    
    def _save_template(self, template: Template):
        with open(self._template_file(template.id), "w") as f:
            json.dump(template.to_dict(), f, indent=2)
    
    def create(self, name: str, description: str = "") -> Template:
        template = Template(name=name, description=description)
        self.templates[template.id] = template
        self._save_template(template)
        return template
    
    def create_from_session(self, session_id: str, name: str, description: str = "") -> Template:
        """Create a template from an existing session"""
        from sessions import get_session_manager
        manager = get_session_manager()
        session = manager.get(session_id)
        
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        template = Template(name=name, description=description or f"From session {session_id}")
        
        # Add all messages as steps
        for msg in session.messages:
            step_type = "user_message" if msg.role == "user" else "assistant_response"
            template.add_step(step_type, msg.content, msg.tool_calls)
        
        self.templates[template.id] = template
        self._save_template(template)
        return template
    
    def get(self, template_id: str) -> Optional[Template]:
        return self.templates.get(template_id)
    
    def list(self) -> List[Template]:
        return sorted(self.templates.values(), key=lambda t: t.usage_count, reverse=True)
    
    def update(self, template_id: str, name: Optional[str] = None, description: Optional[str] = None) -> bool:
        template = self.templates.get(template_id)
        if not template:
            return False
        
        if name:
            template.name = name
        if description:
            template.description = description
        
        self._save_template(template)
        return True
    
    def delete(self, template_id: str) -> bool:
        if template_id in self.templates:
            template_file = self._template_file(template_id)
            if template_file.exists():
                template_file.unlink()
            del self.templates[template_id]
            return True
        return False
    
    def increment_usage(self, template_id: str) -> bool:
        template = self.templates.get(template_id)
        if template:
            template.increment_usage()
            self._save_template(template)
            return True
        return False


# Singleton instance
_manager: Optional[TemplatesManager] = None

def get_templates_manager() -> TemplatesManager:
    global _manager
    if _manager is None:
        _manager = TemplatesManager()
    return _manager


# --- CLI TEST ---
if __name__ == "__main__":
    print("📋 Test Templates Manager")
    
    manager = get_templates_manager()
    
    # Create a template manually
    template = manager.create(
        name="List Files Workflow",
        description="Lists files in a directory"
    )
    template.add_step("user_message", "Liste les fichiers du Bureau")
    template.add_step("assistant_response", "Voici les fichiers...", [{"name": "list_directory", "arguments": {"path": "$HOME/Desktop"}}])
    manager._save_template(template)
    print(f"✅ Created template: {template.name} ({template.id})")
    
    # List templates
    print("\n📦 Available templates:")
    for t in manager.list():
        print(f"  - {t.name} ({t.id}): {len(t.steps)} steps, used {t.usage_count}x")