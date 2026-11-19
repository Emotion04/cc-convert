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
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ONEDIR = REPO / "dist" / "ccconvert"
SHELL_EXE = REPO / "desktop-shell" / "src-tauri" / "target" / "release" / "ccconvert-desktop.exe"
OUT_DIR = REPO / "dist-release"

README_ZH = """CC Convert {version} (Windows x64) — 绿色版
=============================================

启动
  双击 ccconvert-desktop.exe：自动启动后台服务并打开桌面窗口。
  （关闭窗口 = 缩到托盘；托盘菜单可退出）
  也可只用命令行：ccconvert.exe start，然后浏览器打开
  http://127.0.0.1:18406 （/desktop 桌面界面，/ 原 Web 界面）。

数据位置（便携模式）
  本目录下的 data\\ 文件夹存放全部配置与运行数据：
    data\\config\\config.toml   实例与全局配置
    data\\runtime\\             日志、状态、用量统计
  整个文件夹可以整体移动或拷到 U 盘使用。
  若想改用用户目录（%USERPROFILE%\\.ccconvert），删除 data\\ 文件夹即可。

注意
  ccconvert-desktop.exe 与 ccconvert.exe 必须保持在同一目录。
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

    # Build in a throwaway directory: a previous staging folder may still be
    # open (the running shell lives in it), and failing to delete that would
    # otherwise block every repack.
    with tempfile.TemporaryDirectory(prefix="ccconvert-pack-") as tmp:
        staging = Path(tmp)
        for item in ONEDIR.iterdir():
            dest = staging / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        shutil.copy2(SHELL_EXE, staging / SHELL_EXE.name)
        (staging / "使用说明.txt").write_text(
            README_ZH.format(version=version), encoding="utf-8"
        )
        # Portable mode marker: runtime_paths switches to <exe>/data/{config,runtime}
        # whenever this directory exists next to a frozen executable. Keep a
        # placeholder file inside — an empty directory would be dropped when
        # only files are written to the zip.
        portable = staging / "data"
        portable.mkdir()
        (portable / "README.txt").write_text(
            "此目录保存 CC Convert 的全部配置与运行数据（便携模式）。\n"
            "config\\config.toml   实例与全局配置\n"
            "runtime\\             日志、状态与用量统计\n"
            "删除本目录即可改用 %USERPROFILE%\\.ccconvert。\n",
            encoding="utf-8",
        )

        if zip_path.exists():
            zip_path.unlink()
        files = [p for p in sorted(staging.rglob("*")) if p.is_file()]
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in files:
                zf.write(path, path.relative_to(staging))

    size_mb = zip_path.stat().st_size / 1e6
    print(f"packed {zip_path.name}: {size_mb:.1f} MB, {len(files)} files")
    return zip_path


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(__doc__)
        sys.exit(0)
    pack()
