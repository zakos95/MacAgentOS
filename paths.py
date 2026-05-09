import os
import sys
from pathlib import Path

def get_app_support_dir() -> Path:
    d = Path.home() / "Library" / "Application Support" / "MacAgentOS"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_data_dir() -> Path:
    d = get_app_support_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_project_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent
