# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MacAgentOS backend (server.py)
#
# Build command:
#   cd "$PROJECT_ROOT"
#   .venv312/bin/pip install pyinstaller
#   .venv312/bin/pyinstaller server.spec
#
# Output: dist/MacAgentServer  (single binary)

import sys
from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH)

a = Analysis(
    ['server.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # Runtime config profiles (read by app_runtime.py via get_project_root())
        ('config', 'config'),
        # MCP server list (read by server.py via get_project_root())
        ('mcp_servers.json', '.'),
        # Web UI served at /ui
        ('server_ui.html', '.'),
        # Local MCP stdio scripts used by bundled MacAgentServer dispatch mode
        ('mac_server.py', '.'),
        ('mcp_safari.py', '.'),
    ],
    hiddenimports=[
        # ── uvicorn internals (not auto-detected) ──────────────────────────
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.middleware',
        'uvicorn.middleware.proxy_headers',
        # ── anyio backends ─────────────────────────────────────────────────
        'anyio._backends._asyncio',
        'anyio._backends._trio',
        # ── starlette ──────────────────────────────────────────────────────
        'starlette.routing',
        'starlette.applications',
        'starlette.requests',
        'starlette.responses',
        'starlette.middleware',
        'starlette.middleware.base',
        'starlette.middleware.cors',
        # ── fastapi ────────────────────────────────────────────────────────
        'fastapi.routing',
        'fastapi.applications',
        'fastapi.responses',
        'fastapi.middleware',
        # ── pydantic v2 internals ──────────────────────────────────────────
        'pydantic.deprecated.class_validators',
        'pydantic_core',
        # ── mcp client / server ────────────────────────────────────────────
        'mcp',
        'mcp.client',
        'mcp.client.stdio',
        'mcp.server',
        'mcp.server.fastmcp',
        # ── sse-starlette ──────────────────────────────────────────────────
        'sse_starlette',
        'sse_starlette.sse',
        # ── multipart (FastAPI file uploads) ──────────────────────────────
        'multipart',
        'python_multipart',
        # ── email (standard lib, often missed) ────────────────────────────
        'email.mime.text',
        'email.mime.multipart',
        'email.mime.base',
        # ── project modules (imported dynamically in places) ──────────────
        'core',
        'sessions',
        'templates',
        'permissions',
        'skills',
        'commands',
        'agents',
        'plugins',
        'worker',
        'debug',
        'opencode_auth',
        'opencode_bridge',
        'token_optimization',
        'app_runtime',
        'provider_connections',
        'paths',
        'llm_universal',
        'mcp_hub',
        'mcp_factory',
        'mac_server',
        'mcp_safari',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not needed at runtime — reduces binary size
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'pytest',
        'IPython',
        'notebook',
        'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MacAgentServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,        # UPX can break macOS signed binaries
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,     # Keep console=True; the Swift app redirects stdout/stderr
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',    # Change to 'x86_64' or None (universal) as needed
    codesign_identity=None,
    entitlements_file=None,
)
