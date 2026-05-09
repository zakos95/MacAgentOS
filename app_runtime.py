import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppRuntime:
    environment: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"


def load_runtime() -> AppRuntime:
    environment = os.getenv("MAC_AGENT_ENV", "dev").lower()
    from paths import get_project_root
    config_path = get_project_root() / "config" / f"{environment}.json"
    if not config_path.exists():
        return AppRuntime(environment=environment)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return AppRuntime(
            environment=data.get("environment", environment),
            host=data.get("host", "127.0.0.1"),
            port=int(data.get("port", 8000)),
            log_level=data.get("log_level", "info"),
        )
    except Exception:
        return AppRuntime(environment=environment)
