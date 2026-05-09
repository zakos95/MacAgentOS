"""
MacAgent-OS Debug - Debug exports like OpenWork
Export runtime state for troubleshooting
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger("MacAgent-Debug")


class DebugExporter:
    """Export debug information"""
    
    def __init__(self, storage_dir: str = None):
        if storage_dir is None:
            storage_dir = Path(__file__).parent / "data" / "debug"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    
    def export_runtime(self, state: Dict) -> str:
        """Export runtime state"""
        timestamp = datetime.now().isoformat()
        filename = f"runtime_{timestamp.replace(':', '-')}.json"
        filepath = self.storage_dir / filename
        
        data = {
            "exported_at": timestamp,
            "runtime": state
        }
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"Exported runtime to {filepath}")
        return str(filepath)
    
    def export_logs(self, log_file: str = "/tmp/macagent.log") -> str:
        """Export logs"""
        timestamp = datetime.now().isoformat()
        filename = f"logs_{timestamp.replace(':', '-')}.log"
        filepath = self.storage_dir / filename
        
        try:
            with open(log_file, "r") as src:
                content = src.read()
            
            with open(filepath, "w") as f:
                f.write(content)
            
            logger.info(f"Exported logs to {filepath}")
            return str(filepath)
        except FileNotFoundError:
            return ""
    
    def export_full(self, runtime_state: Dict) -> Dict[str, str]:
        """Export everything"""
        files = {}
        
        # Runtime
        files["runtime"] = self.export_runtime(runtime_state)
        
        # Logs
        log_file = self.export_logs()
        if log_file:
            files["logs"] = log_file
        
        timestamp = datetime.now().isoformat()
        summary = {
            "exported_at": timestamp,
            "files": files
        }
        
        # Export summary
        summary_file = self.storage_dir / f"debug_summary_{timestamp.replace(':', '-')}.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)
        
        files["summary"] = str(summary_file)
        
        return files
    
    def list_exports(self) -> list:
        """List all exports"""
        exports = list(self.storage_dir.glob("*.json"))
        exports.extend(self.storage_dir.glob("*.log"))
        return sorted(exports, key=lambda p: p.stat().st_mtime, reverse=True)


# Singleton
_exporter: DebugExporter = None

def get_debug_exporter() -> DebugExporter:
    global _exporter
    if _exporter is None:
        _exporter = DebugExporter()
    return _exporter


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    exporter = get_debug_exporter()
    print("Debug Exports:")
    for f in exporter.list_exports():
        print(f"  {f.name}")