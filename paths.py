import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def get_app_support_dir() -> Path:
    override = os.environ.get("MACAGENT_APP_SUPPORT_DIR")
    path = Path(override) if override else Path.home() / "Library/Application Support/MacAgentOS"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    path = get_app_support_dir() / "data"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def storage_dir(name: str) -> Path:
    """Keep user data outside the temporary PyInstaller extraction directory."""
    path = get_data_dir() / name
    legacy = get_project_root() / "data" / name
    if not path.exists() and legacy.is_dir() and not os.environ.get("MACAGENT_APP_SUPPORT_DIR"):
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            copy = Path(tmp) / name
            shutil.copytree(legacy, copy)
            if not path.exists():
                copy.rename(path)
    return path


def write_private_json(path: Path, contents) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(contents, handle, indent=2, ensure_ascii=False)
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def get_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent
