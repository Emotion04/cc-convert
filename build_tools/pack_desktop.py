# SPDX-License-Identifier: Apache-2.0
"""Assemble the Windows portable desktop package.

Layout (zip root, flat):

    ccconvert.exe / _internal/…   # PyInstaller onedir of the Manager+CLI
    ccconvert-desktop.exe         # Tauri shell — finds ccconvert.exe beside it
    使用说明.txt

Run after both builds::

    uv run pyinstaller ccconvert.spec --noconfirm --clean
    (cd desktop-shell && npx tauri build)
    uv run python build_tools/pack_desktop.py
"""

import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ONEDIR = REPO / "dist" / "ccconvert"
SHELL_EXE = REPO / "desktop-shell" / "src-tauri" / "target" / "release" / "ccconvert-desktop.exe"
OUT_DIR = REPO / "dist-release"
STAGING = OUT_DIR / "stage"

README_ZH = """CC Convert {version} (Windows x64)
=====================================

1. 双击 ccconvert-desktop.exe：自动启动 Manager 并打开桌面界面。
   （关闭窗口 = 缩到托盘；托盘菜单可退出）
2. 浏览器方式：运行 ccconvert.exe start 后访问 http://127.0.0.1:18406
   （/desktop 为新桌面界面，/ 为原 Web 界面）。
3. 配置文件在 %USERPROFILE%\\.ccconvert\\config.toml（面板内可图形化编辑）。
4. ccconvert-desktop.exe 与 ccconvert.exe 必须保持在同一目录。
"""


def _version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version"):
            return line.split('"')[1]
    raise SystemExit("version not found in pyproject.toml")


def pack() -> Path:
    version = _version()
    if not ONEDIR.is_dir():
        raise SystemExit(f"missing {ONEDIR} — run pyinstaller first")
    if not SHELL_EXE.is_file():
        raise SystemExit(f"missing {SHELL_EXE} — run tauri build first")

    OUT_DIR.mkdir(exist_ok=True)
    zip_path = OUT_DIR / f"ccconvert-desktop-{version}-windows-x64.zip"
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)

    for item in ONEDIR.iterdir():
        dest = STAGING / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    shutil.copy2(SHELL_EXE, STAGING / SHELL_EXE.name)
    (STAGING / "使用说明.txt").write_text(
        README_ZH.format(version=version), encoding="utf-8"
    )

    if zip_path.exists():
        zip_path.unlink()
    files = [p for p in sorted(STAGING.rglob("*")) if p.is_file()]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            zf.write(path, path.relative_to(STAGING))

    size_mb = zip_path.stat().st_size / 1e6
    print(f"packed {zip_path.name}: {size_mb:.1f} MB, {len(files)} files")
    return zip_path


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(__doc__)
        sys.exit(0)
    pack()
