"""
MacAgent-OS Worker - Cloud worker support like OpenWork
Connects to remote workers (Daytona-style)
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger("MacAgent-Worker")


@dataclass
class Worker:
    """A remote worker"""
    id: str
    url: str
    name: str
    status: str  # "connecting", "running", "stopped"
    workspace: str
    created_at: str


class WorkerManager:
    """Manages workers"""
    
    def __init__(self, config_file: str = None):
        if config_file is None:
            config_file = os.path.expanduser("~/.macagent/workers.json")
        self.config_file = Path(config_file)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.workers: Dict[str, Worker] = {}
        self.local_worker: Optional[Worker] = None
        self._load_config()
    
    def _load_config(self):
        """Load config"""
        if self.config_file.exists():
            with open(self.config_file) as f:
                data = json.load(f)
                for w in data.get("workers", []):
                    worker = Worker(**w)
                    self.workers[worker.id] = worker
    
    def _save_config(self):
        """Save config"""
        with open(self.config_file, "w") as f:
            json.dump({
                "workers": [w.__dict__ for w in self.workers.values()]
            }, f, indent=2)
    
    def create_local_worker(self, workspace: str) -> Worker:
        """Create a local worker"""
        worker = Worker(
            id="local",
            url="http://localhost:8000",
            name="Local",
            status="running",
            workspace=workspace
        )
        self.local_worker = worker
        self.workers["local"] = worker
        self._save_config()
        return worker
    
    def connect_remote(self, url: str, name: str = "Remote") -> Worker:
        """Connect to remote worker"""
        import uuid
        worker = Worker(
            id=str(uuid.uuid4())[:8],
            url=url,
            name=name,
            status="connecting",
            workspace=""
        )
        self.workers[worker.id] = worker
        self._save_config()
        return worker
    
    def disconnect(self, worker_id: str) -> bool:
        """Disconnect a worker"""
        if worker_id in self.workers:
            self.workers[worker_id].status = "stopped"
            self._save_config()
            return True
        return False
    
    def get(self, worker_id: str) -> Optional[Worker]:
        """Get worker"""
        return self.workers.get(worker_id)
    
    def list(self) -> List[Worker]:
        """List workers"""
        return list(self.workers.values())
    
    def get_active(self) -> List[Worker]:
        """Get active workers"""
        return [w for w in self.workers.values() if w.status == "running"]
    
    def create_worker_url(self, worker_id: str) -> str:
        """Create worker connect URL"""
        worker = self.get(worker_id)
        if worker:
            return f"/w/{worker.id}/connect"
        return ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Get state"""
        return {
            "workers": [
                {
                    "id": w.id,
                    "name": w.name,
                    "status": w.status,
                    "url": w.url
                }
                for w in self.workers.values()
            ],
            "active": len(self.get_active())
        }


# Singleton
_manager: Optional[WorkerManager] = None

def get_worker_manager() -> WorkerManager:
    global _manager
    if _manager is None:
        _manager = WorkerManager()
    return _manager


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    wm = get_worker_manager()
    
    # Create local
    local = wm.create_local_worker("$HOME/Desktop")
    print(f"Created local worker: {local.url}")
    
    # List
    print("\nWorkers:")
    for w in wm.list():
        print(f"  {w.name}: {w.status}")