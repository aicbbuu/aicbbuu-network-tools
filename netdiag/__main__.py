"""
程序入口。

支持两种运行方式：
  python -m netdiag          从源码运行
  netdiag.exe                PyInstaller 打包后
"""
from __future__ import annotations

import sys


def _excepthook(exc_type, exc, tb) -> None:
    """GUI 程序没有可见的控制台，未捕获异常会让程序「静默消失」。

    这里把异常写到同目录的 error.log，用户下次出问题能直接给我们看。
    """
    import os
    import traceback
    log_path = os.path.join(os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                            else os.getcwd(), "error.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {__import__('datetime').datetime.now().isoformat()} =====\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        traceback.print_exception(exc_type, exc, tb)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None, "程序出错",
            f"发生了未预期的错误：\n\n{exc}\n\n详细信息已写入：\n{log_path}")
    except Exception:
        pass


def main() -> int:
    sys.excepthook = _excepthook

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # 高 DPI 缩放取整策略。Qt6 默认就是 PassThrough，显式设置是为了
    # 表达意图——125%/150% 缩放的屏幕上取整会导致布局尺寸对不上。
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("aicbbuu network tools")
    app.setApplicationDisplayName("aicbbuu network tools")
    app.setOrganizationName("aicbbuu")

    from .app import App
    win = App()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
