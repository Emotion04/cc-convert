# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a portable *onedir* build.

Build (Windows):

    uv sync --group build
    uv run pyinstaller ccconvert.spec --noconfirm --clean

Output: ``dist/ccconvert/ccconvert.exe`` — ship the whole folder.

onedir rather than onefile: every proxy instance is a child process, and a
onefile build would re-extract the whole bundle (1–3 s) for each instance start.
"""

from PyInstaller.utils.hooks import collect_all

# Jinja templates are read from the bundle, not from a .py file on disk.
datas = [
    ("src/cc_convert/web/templates", "cc_convert/web/templates"),
]
binaries = []
hiddenimports = []

# Packages whose data files / dynamic imports PyInstaller cannot infer.
for package in (
    "uvicorn",
    "tiktoken",
    "jinja2",
    "websockets",
    "httptools",
    "watchfiles",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        # Optional runtime extras may be absent; the core still works.
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["build_tools/entry.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Test tooling and GUI stubs are not needed at runtime.
    excludes=["tkinter", "pytest", "pytest_asyncio", "ruff", "mypy", "black"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ccconvert",
    debug=False,
    strip=False,
    upx=False,  # UPX trips antivirus heuristics far more often than it helps.
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ccconvert",
)
