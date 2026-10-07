# -*- coding: utf-8 -*-
"""Windowed launcher: report startup failures even when no console exists."""
import sys
import traceback
from pathlib import Path

try:
    from eeclass_video_downloader import main
    main()
except Exception:
    detail = traceback.format_exc()
    try:
        import tempfile
        log_path = Path(tempfile.gettempdir()) / "eeclass_startup_error.log"
        log_path.write_text(detail, encoding="utf-8")
        message = "無法啟動 eeClass 影片下載器。\n請確認使用 Python 3.10 以上，且已安裝 Tcl/Tk。\n\n錯誤紀錄：" + str(log_path)
    except OSError:
        message = "無法啟動 eeClass 影片下載器。\n" + detail[-1500:]
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "eeClass 啟動失敗", 16)
    else:
        sys.stderr.write(detail)
    sys.exit(1)
