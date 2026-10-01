"""在真实桌面上逐页截图，供人工比对配色。

**为什么必须用真实渲染。** offscreen 后端下 Qt 不给
QMessageBox / QCheckBox 应用 QSS，而且控件的 palette 与
`grab()` 的结果都可能与真机不同——本轮排查「配色和 v1.0.0
不一致」时，正是被 offscreen 的假象带偏了两次。所以配色验收
必须在 windows 插件 + 真实显示环境下做。

用法：
    python tools/shoot_pages.py <输出目录>

Windows runner 上由 ci.yml 调用；本机跑会因缺交互桌面而失败
（Start-Process 起的 GUI 在本会话没有可见桌面），这属于预期。
"""
from __future__ import annotations

import os
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:  # pragma: no cover - 环境相关
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from netdiag.app import App  # noqa: E402
from netdiag.ui.pages import GROUPS, SIDEBAR  # noqa: E402


def _pump(app: QApplication, n: int = 25) -> None:
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "screenshots"
    os.makedirs(out, exist_ok=True)

    app = QApplication(sys.argv[:1])
    win = App("light")
    win.show()
    _pump(app, 40)
    win.resize(1440, 900)
    _pump(app, 30)

    # 一级页面（展开二级菜单，让组内页面也进得来）
    for title, head in win.sidebar._group_heads.items():
        head.setChecked(True)
    _pump(app, 12)

    names = []
    for item in SIDEBAR:
        if isinstance(item, tuple):
            for cls in GROUPS[item[1]]:
                names.append(cls.NAME)
        else:
            names.append(item.NAME)

    for name in names:
        win.switch_to(name)
        _pump(app, 16)
        win.grab().save(os.path.join(out, f"light_{name}.png"))

    # 深色主题：v1.0.0 时这套对比度问题全靠肉眼，现在要留证据
    win.toggle_theme()
    _pump(app, 30)
    for name in ("ping", "diagnose", "flushdns", "multiprobe"):
        win.switch_to(name)
        _pump(app, 16)
        win.grab().save(os.path.join(out, f"dark_{name}.png"))

    print(f"已截图 {len(names) + 4} 张 -> {out}")
    win.close()


if __name__ == "__main__":
    main()