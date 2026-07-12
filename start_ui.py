"""Direct launcher for the Search4Paper desktop UI."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def main() -> None:
    try:
        from code.paper_ui import main as run_ui
        run_ui()
    except Exception:
        base = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        log_path = base / "search4paper_startup_error.log"
        details = traceback.format_exc()
        try:
            log_path.write_text(details, encoding="utf-8")
        except OSError:
            pass
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Search4Paper 启动失败",
                f"程序启动失败，详细错误已保存到：\n{log_path}",
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()