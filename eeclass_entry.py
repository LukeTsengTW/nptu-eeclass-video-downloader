"""Shared source / frozen launcher. Worker modes keep their redirected pipes."""
import sys
import tempfile
import traceback
from pathlib import Path


def launch():
    try:
        from eeclass_video_downloader import entry_point
        entry_point()
    except Exception:
        detail = traceback.format_exc()
        if sys.argv[1:]:
            # Background workers / build checks must fail without a modal GUI.
            if sys.stderr is not None:
                sys.stderr.write(detail)
            raise SystemExit(1)
        message = "無法啟動 eeClass 影片下載器。"
        message += ("\n請重新下載完整的 EXE。" if getattr(sys, "frozen", False)
                    else "\n請確認 Python 已包含 Tcl/Tk。")
        try:
            log = Path(tempfile.gettempdir()) / "eeclass_startup_error.log"
            log.write_text(detail, encoding="utf-8")
            message += "\n\n錯誤紀錄：" + str(log)
        except OSError:
            message += "\n" + detail[-1500:]
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "eeClass 啟動失敗", 16)
        elif sys.stderr is not None:
            sys.stderr.write(detail)
        raise SystemExit(1)


if __name__ == "__main__":
    launch()
