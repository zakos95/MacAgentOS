"""
Sessions - Gère l'historique des conversations
Inspired by OpenWork sessions concept
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any


class Message:
    def __init__(self, role: str, content: str, tool_calls: Optional[List[Dict]] = None, timestamp: Optional[str] = None):
        self.role = role  # "user" | "assistant" | "system"
        self.content = content
        self.tool_calls = tool_calls or []
        self.timestamp = timestamp or datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        return cls(
            role=data["role"],
            content=data["content"],
            tool_calls=data.get("tool_calls", []),
            timestamp=data.get("timestamp")
        )


class Session:
    def __init__(self, session_id: Optional[str] = None, name: Optional[str] = None):
        self.id = session_id or str(uuid.uuid4())[:8]
        self.name = name or f"Session {self.id}"
        self.messages: List[Message] = []
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
    
    def add_message(self, role: str, content: str, tool_calls: Optional[List[Dict]] = None):
        msg = Message(role, content, tool_calls)
        self.messages.append(msg)
        self.updated_at = datetime.now().isoformat()
        return msg
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        session = cls(session_id=data["id"], name=data.get("name"))
        session.messages = [Message.from_dict(m) for m in data["messages"]]
        session.created_at = data.get("created_at", session.created_at)
        session.updated_at = data.get("updated_at", session.updated_at)
        return session
    
    def get_context(self, max_messages: int = 10) -> str:
        """Get conversation context for LLM"""
        context_parts = []
        for msg in self.messages[-max_messages:]:
            role_emoji = {"user": "👤", "assistant": "🤖", "system": "⚙️"}.get(msg.role, "📝")
            context_parts.append(f"{role_emoji} {msg.role}: {msg.content}")
        return "\n".join(context_parts)


class SessionManager:
    def __init__(self, storage_dir: str = None):
        if storage_dir is None:
            storage_dir = Path(__file__).parent / "data" / "sessions"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: Dict[str, Session] = {}
        self.current_session_id: Optional[str] = None
        self._load_index()
    
    def _session_file(self, session_id: str) -> Path:
        return self.storage_dir / f"{session_id}.json"
    
    def _load_index(self):
        index_file = self.storage_dir / "index.json"
        if index_file.exists():
            with open(index_file, "r") as f:
                data = json.load(f)
                self.current_session_id = data.get("current")
                for session_data in data.get("sessions", []):
                    session = Session.from_dict(session_data)
                    self.sessions[session.id] = session
    
    def _save_index(self):
        index_file = self.storage_dir / "index.json"
        data = {
            "current": self.current_session_id,
            "sessions": [s.to_dict() for s in self.sessions.values()]
        }
        with open(index_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def create(self, name: Optional[str] = None) -> Session:
        session = Session(name=name)
        self.sessions[session.id] = session
        self.current_session_id = session.id
        self._save_index()
        self._save_session(session)
        return session
    
    def _save_session(self, session: Session):
        with open(self._session_file(session.id), "w") as f:
            json.dump(session.to_dict(), f, indent=2)
    
    def load(self, session_id: str) -> Optional[Session]:
        session_file = self._session_file(session_id)
        if session_file.exists():
            with open(session_file, "r") as f:
                data = json.load(f)
                session = Session.from_dict(data)
                self.sessions[session.id] = session
                return session
        return None
    
    def get(self, session_id: Optional[str] = None) -> Optional[Session]:
        sid = session_id or self.current_session_id
        if sid and sid in self.sessions:
            return self.sessions[sid]
        return None
    
    def list(self) -> List[Session]:
        return sorted(self.sessions.values(), key=lambda s: s.updated_at, reverse=True)
    
    def set_current(self, session_id: str) -> bool:
        if session_id in self.sessions:
            self.current_session_id = session_id
            self._save_index()
            return True
        return False
    
    def add_message(self, role: str, content: str, tool_calls: Optional[List[Dict]] = None, session_id: Optional[str] = None) -> Message:
        sid = session_id or self.current_session_id
        if sid not in self.sessions:
            self.create()
            sid = self.current_session_id
        
        session = self.sessions[sid]
        msg = session.add_message(role, content, tool_calls)
        self._save_session(session)
        self._save_index()
        return msg
    
    def delete(self, session_id: str) -> bool:
        if session_id in self.sessions:
            session_file = self._session_file(session_id)
            if session_file.exists():
                session_file.unlink()
            del self.sessions[session_id]
            if self.current_session_id == session_id:
                self.current_session_id = None
            self._save_index()
            return True
        return False
    
    def summarize(self, session_id: Optional[str] = None) -> str:
        """Get a summary of the session"""
        session = self.get(session_id)
        if not session:
            return "No session found"
        
        msg_count = len(session.messages)
        user_msgs = sum(1 for m in session.messages if m.role == "user")
        assistant_msgs = sum(1 for m in session.messages if m.role == "assistant")
        
        return f"Session: {session.name}\nMessages: {msg_count} (user: {user_msgs}, assistant: {assistant_msgs})\nCreated: {session.created_at}"


# Singleton instance
_manager: Optional[SessionManager] = None

def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager


# --- CLI TEST ---
if __name__ == "__main__":
    print("🗂 Test Session Manager")
    
    manager = get_session_manager()
    
    # Create a new session
    session = manager.create(name="Test Session")
    print(f"✅ Created session: {session.id}")
    
    # Add some messages
    manager.add_message("user", "Salut MacAgent!")
    manager.add_message("assistant", "Bonjour! Je suis MacAgent-OS.")
    manager.add_message("user", "Liste mes fichiers")
    manager.add_message("assistant", "Voici vos fichiers...")
    
    # List sessions
    print("\n📋 Sessions:")
    for s in manager.list():
        print(f"  - {s.id}: {s.name} ({len(s.messages)} messages)")
    
    # Get current session context
    print("\n💬 Current context:")
    current = manager.get()
    if current:
        print(current.get_context())
    
    # Summarize
    print(f"\n📊 Summary: {manager.summarize()}")