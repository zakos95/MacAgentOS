"""
Permissions - Système d'approbation pour les actions sensibles
Inspired by OpenWork permissions concept
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from enum import Enum
from dataclasses import dataclass, field


class PermissionType(Enum):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    COMMAND_RUN = "command_run"
    NETWORK = "network"
    MAC_CONTROL = "mac_control"
    FOLDER_ACCESS = "folder_access"


class PermissionReply(Enum):
    ONCE = "once"           # Allow this time only
    ALWAYS = "always"      # Always allow for this session
    DENY = "deny"         # Deny this request
    REJECT = "reject"      # Permanently deny


@dataclass
class PermissionRequest:
    request_id: str
    permission_type: str
    description: str
    details: Dict[str, Any]
    timestamp: str
    status: str = "pending"  # pending, approved, denied, rejected
    reply: Optional[str] = None
    reply_timestamp: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "permission_type": self.permission_type,
            "description": self.description,
            "details": self.details,
            "timestamp": self.timestamp,
            "status": self.status,
            "reply": self.reply,
            "reply_timestamp": self.reply_timestamp
        }


class ApprovalRule:
    def __init__(self, rule_id: str, permission_type: str, pattern: str = "*", allow: bool = False):
        self.rule_id = rule_id
        self.permission_type = permission_type
        self.pattern = pattern  # glob pattern like "*.py" or path prefix
        self.allow = allow
    
    def matches(self, path: str) -> bool:
        import fnmatch
        return fnmatch.fnmatch(path, self.pattern) or path.startswith(self.pattern)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "permission_type": self.permission_type,
            "pattern": self.pattern,
            "allow": self.allow
        }


class PermissionManager:
    def __init__(self, storage_dir: str = None):
        if storage_dir is None:
            storage_dir = Path(__file__).parent / "data" / "permissions"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory state
        self.pending_requests: Dict[str, PermissionRequest] = {}
        self.rules: Dict[str, ApprovalRule] = {}
        self.session_approvals: Dict[str, set] = {}  # session_id -> set of approved types
        
        self._load_rules()
    
    def _rules_file(self) -> Path:
        return self.storage_dir / "rules.json"
    
    def _load_rules(self):
        if self._rules_file().exists():
            with open(self._rules_file(), "r") as f:
                data = json.load(f)
                for rule_data in data.get("rules", []):
                    rule = ApprovalRule(
                        rule_id=rule_data["rule_id"],
                        permission_type=rule_data["permission_type"],
                        pattern=rule_data["pattern"],
                        allow=rule_data["allow"]
                    )
                    self.rules[rule.rule_id] = rule
    
    def _save_rules(self):
        with open(self._rules_file(), "w") as f:
            json.dump({
                "rules": [r.to_dict() for r in self.rules.values()]
            }, f, indent=2)
    
    def create_request(self, permission_type: str, description: str, details: Dict[str, Any]) -> PermissionRequest:
        """Create a new permission request (would be shown to user)"""
        request = PermissionRequest(
            request_id=str(uuid.uuid4())[:8],
            permission_type=permission_type,
            description=description,
            details=details,
            timestamp=datetime.now().isoformat()
        )
        self.pending_requests[request.request_id] = request
        return request
    
    def check_auto_approve(self, permission_type: str, path: str) -> Optional[bool]:
        """Check if an action should be auto-approved based on rules"""
        for rule in self.rules.values():
            if rule.permission_type == permission_type or rule.permission_type == "*":
                if rule.matches(path):
                    return rule.allow
        return None  # No rule matched, need user approval
    
    def check_session_approval(self, session_id: str, permission_type: str) -> bool:
        """Check if user already approved this type for the session"""
        if session_id in self.session_approvals:
            return permission_type in self.session_approvals[session_id]
        return False
    
    def approve(self, request_id: str, reply: PermissionReply, session_id: Optional[str] = None) -> bool:
        """Process a permission reply"""
        request = self.pending_requests.get(request_id)
        if not request:
            return False
        
        request.status = "approved" if reply != PermissionReply.DENY and reply != PermissionReply.REJECT else "denied"
        request.reply = reply.value
        request.reply_timestamp = datetime.now().isoformat()
        
        # Store session approval if "always"
        if reply == PermissionReply.ALWAYS and session_id:
            if session_id not in self.session_approvals:
                self.session_approvals[session_id] = set()
            self.session_approvals[session_id].add(request.permission_type)
        
        # Add rule if "always" or "reject"
        if reply == PermissionReply.ALWAYS:
            rule = ApprovalRule(
                rule_id=str(uuid.uuid4())[:8],
                permission_type=request.permission_type,
                pattern=request.details.get("path", "*"),
                allow=True
            )
            self.rules[rule.rule_id] = rule
            self._save_rules()
        elif reply == PermissionReply.REJECT:
            rule = ApprovalRule(
                rule_id=str(uuid.uuid4())[:8],
                permission_type=request.permission_type,
                pattern=request.details.get("path", "*"),
                allow=False
            )
            self.rules[rule.rule_id] = rule
            self._save_rules()
        
        return True
    
    def get_pending(self) -> List[PermissionRequest]:
        return [r for r in self.pending_requests.values() if r.status == "pending"]
    
    def clear_session(self, session_id: str):
        """Clear approvals for a session"""
        if session_id in self.session_approvals:
            del self.session_approvals[session_id]
    
    def add_rule(self, permission_type: str, pattern: str, allow: bool) -> ApprovalRule:
        """Add a new rule"""
        rule = ApprovalRule(
            rule_id=str(uuid.uuid4())[:8],
            permission_type=permission_type,
            pattern=pattern,
            allow=allow
        )
        self.rules[rule.rule_id] = rule
        self._save_rules()
        return rule
    
    def list_rules(self) -> List[ApprovalRule]:
        return list(self.rules.values())


# Singleton instance
_manager: Optional[PermissionManager] = None

def get_permission_manager() -> PermissionManager:
    global _manager
    if _manager is None:
        _manager = PermissionManager()
    return _manager


# --- CLI TEST ---
if __name__ == "__main__":
    print("🔐 Test Permission Manager")
    
    manager = get_permission_manager()
    
    # Test create request
    request = manager.create_request(
        permission_type="file_read",
        description="Read file $HOME/Desktop/test.txt",
        details={"path": "$HOME/Desktop/test.txt"}
    )
    print(f"✅ Created request: {request.request_id}")
    print(f"   Type: {request.permission_type}")
    print(f"   Description: {request.description}")
    
    # Test auto-approve
    auto = manager.check_auto_approve("file_read", "$HOME/Desktop/test.txt")
    print(f"\n🔍 Auto-approve check: {auto}")
    
    # Test approve
    manager.approve(request.request_id, PermissionReply.ALWAYS, session_id="test-session")
    print(f"\n✅ Approved with ALWAYS for session test-session")
    
    # List rules
    print("\n📋 Rules:")
    for rule in manager.list_rules():
        print(f"  - {rule.permission_type}: {rule.pattern} -> {'allow' if rule.allow else 'deny'}")