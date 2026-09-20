#!/usr/bin/env python3
"""Build a Windows executable for the UI launcher.

Usage examples:
    python code/build_ui_exe.py
    python code/build_ui_exe.py --name Search4PaperUI
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = PROJECT_ROOT / "start_ui.py"
DEFAULT_EXE_NAME = "search4paper-ui"


def _ensure_pyinstaller_available() -> None:
    if importlib.util.find_spec("PyInstaller") is not None:
        return
    raise RuntimeError(
        "未检测到 PyInstaller。\n"
        "请先安装：pip install pyinstaller\n"
    )


def _run_pyinstaller(name: str, dist_dir: Path, build_temp: Path) -> None:
    separator = ";" if os.name == "nt" else ":"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--clean",
        "--noconfirm",
        "--name",
        name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_temp),
        "--specpath",
        str(PROJECT_ROOT),
        "--paths",
        str(PROJECT_ROOT),
        "--hidden-import",
        "code.fetch_openreview_accepted",
        "--hidden-import",
        "code.paper_enrichment",
        "--hidden-import",
        "code.paper_search",
        "--hidden-import",
        "code.query_target_papers",
        "--hidden-import",
        "code.query_research_topic",
        f"--add-data={PROJECT_ROOT / 'code' / 'ccf_a_conferences.json'}{separator}code",
        "--hidden-import=code.fetch_venue_metadata",
        f"--add-data={PROJECT_ROOT / 'code' / 'ccf_venues.json'}{separator}code",
        str(ENTRYPOINT),
    ]

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise RuntimeError(
            "PyInstaller 打包失败。\n"
            f"错误码: {result.returncode}\n"
            f"stderr: {stderr}\n"
            f"stdout: {stdout}"
        )


def _find_built_exe(name: str, dist_dir: Path, output_root: Path) -> Path | None:
    candidates = [
        dist_dir / f"{name}.exe",
        dist_dir / name / f"{name}.exe",
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    matches = [
        path
        for path in dist_dir.rglob(f"{name}.exe")
        if path.is_file()
    ]
    return matches[0] if matches else None


def build_ui_exe(
    name: str = DEFAULT_EXE_NAME,
    output_root: Path = PROJECT_ROOT,
    skip_copy: bool = False,
) -> Path:
    if os.name != "nt":
        raise RuntimeError("Windows EXE 必须在 Windows Python 环境中构建。")
    output_root = output_root.resolve()
    dist_dir = output_root / "dist"
    build_temp = output_root / ".pyinstaller"
    build_temp.mkdir(parents=True, exist_ok=True)

    print(f"正在调用 PyInstaller 打包 UI：{ENTRYPOINT} -> {output_root}")
    _run_pyinstaller(name, dist_dir, build_temp)

    built = _find_built_exe(name, dist_dir, output_root)
    if built is None:
        raise RuntimeError("未找到打包产物，请检查 PyInstaller 输出。")

    target = output_root / f"{name}.exe"
    if skip_copy or built == target:
        return built

    shutil.copy2(built, target)
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build search4paper UI executable")
    parser.add_argument(
        "--name",
        default=DEFAULT_EXE_NAME,
        help="可执行文件名（不带 .exe）",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT),
        help="可执行文件输出目录（默认仓库根目录）",
    )
    parser.add_argument(
        "--skip-copy",
        action="store_true",
        help="不将可执行文件复制到 output-dir 根目录",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_dir)

    try:
        _ensure_pyinstaller_available()
        exe = build_ui_exe(
            name=args.name,
            output_root=output_root,
            skip_copy=args.skip_copy,
        )
    except RuntimeError as err:
        print(f"失败：{err}", file=sys.stderr)
        return 1

    print(f"已生成: {exe}")
    print("在 Windows 机器上双击该 exe 即可启动 UI。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
