"""无边框窗口的交互测试（拖动 / 缩放 / 最大化 / 窗口按钮）。

设计要点
--------
1. **必须用 QMouseEvent 的完整签名**：
   (type, localPos, globalPos, button, buttons, modifiers)
   全局坐标是**第二个参数**。传错位置会得到「看起来没反应」
   的假象——这正是拖动功能一度测不出来的原因。
2. **双击要点在 titlebar 自身**（eventFilter 判定 obj is self.bar），
   坐标必须用 titlebar.mapToGlobal(rect().center()) 现算：最大化后
   标题栏尺寸会变，固定坐标必然测错。
3. **状态判定用 isMaximized()**，不要读 windowState()——已实测
   两者在 frameless 窗口上始终一致，读哪个都行，但要统一。
4. 全程 pump 事件；任何一次 pump 不足都会让上一个操作看起来
   「没生效」，进而写出错误的期望值。

跑法：python tests/test_qt_window.py
退出码 0 = 全过，1 = 有失败。用法与 test_qt_ui.py 一致。
"""

from __future__ import annotations

import os
import sys

# CI 的 windows-latest 控制台代码页是 cp1252，print 中文直接
# UnicodeEncodeError，测试连标题都打不出来就 exit 1。本地终端多半是
# UTF-8 所以复现不了——这个坑只有真正推上 GitHub 才会暴露。
# reconfigure 幂等，且对已经是 UTF-8 的流无副作用。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # 被重定向到 StringIO 等无 reconfigure 的对象
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt          # noqa: E402
from PySide6.QtGui import QMouseEvent                            # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

from netdiag import window as W                                 # noqa: E402
from netdiag.app import App                                     # noqa: E402

fails: list[str] = []


def main() -> int:
    app = QApplication(sys.argv[:1])
    w = App("light")
    w.show()
    d = w._dragger

    def pump(n: int = 12) -> None:
        for _ in range(n):
            app.processEvents()
            time.sleep(0.02)

    def send(kind: str, gx: int, gy: int, target=None) -> None:
        tgt = target or w
        gl = QPointF(gx, gy)
        types = {"p": QEvent.Type.MouseButtonPress,
                 "m": QEvent.Type.MouseMove,
                 "r": QEvent.Type.MouseButtonRelease,
                 "d": QEvent.Type.MouseButtonDblClick}
        btn = (Qt.MouseButton.LeftButton if kind != "m"
               else Qt.MouseButton.NoButton)
        bts = (Qt.MouseButton.LeftButton if kind != "r"
               else Qt.MouseButton.NoButton)
        app.sendEvent(tgt, QMouseEvent(types[kind], tgt.mapFromGlobal(gl),
                                       gl, btn, bts,
                                       Qt.KeyboardModifier.NoModifier))
        pump()

    def drag(frm: tuple[int, int], to: tuple[int, int]) -> None:
        send("p", *frm)
        send("m", *to)
        send("r", *to)

    def reset(x=200, y=200, ww=1000, hh=700) -> None:
        if w.isMaximized():
            w.showNormal()
        pump()
        w.setGeometry(x, y, ww, hh)
        pump()

    def bar_center() -> QPoint:
        tb = w.titlebar
        c = tb.mapToGlobal(tb.rect().center())
        return QPoint(c.x(), c.y())

    def dbl_bar() -> None:
        tb = w.titlebar
        c = bar_center()
        gl = QPointF(c.x(), c.y())
        app.sendEvent(tb, QMouseEvent(QEvent.Type.MouseButtonDblClick,
                                      tb.mapFromGlobal(gl), gl,
                                      Qt.MouseButton.LeftButton,
                                      Qt.MouseButton.LeftButton,
                                      Qt.KeyboardModifier.NoModifier))
        pump()

    pump(15)

    # ---- 1. 标题栏拖动 ----
    reset()
    drag((600, 206), (700, 306))
    got = (w.x(), w.y())
    print(f"1. 标题栏拖动      {got}  期望 (300,300)  "
          f"{'OK' if got == (300, 300) else 'FAIL'}")
    if got != (300, 300):
        fails.append("标题栏拖动")

    # ---- 2. 四向缩放 ----
    reset()
    drag((1198, 898), (1298, 998))                # 右下 -> 放大
    got = (w.x(), w.y(), w.width(), w.height())
    exp = (200, 200, 1100, 800)
    print(f"2. 右下角放大      {got}  期望 {exp}  "
          f"{'OK' if got == exp else 'FAIL'}")
    if got != exp:
        fails.append("右下角放大")

    reset()
    # 左下角往左下拖 = 宽和高都变大。dx = -102 -> 宽 1102，
    # dy = +302 -> 高 1002（注意高度增量是 +302 而不是拖到 1200）
    drag((2, 898), (-100, 1200))
    got = (w.width(), w.height())
    exp = (1102, 1002)
    print(f"3. 左下角放大      {got}  期望 {exp}  "
          f"{'OK' if got == exp else 'FAIL'}")
    if got != exp:
        fails.append("左下角放大")

    reset()
    drag((1198, 898), (800, 600))                 # 右下 -> 缩小到最小
    got = (w.width(), w.height())
    exp = (W.MIN_W, W.MIN_H)
    print(f"4. 右下缩到最小    {got}  期望 {exp}  "
          f"{'OK' if got == exp else 'FAIL'}")
    if got != exp:
        fails.append("缩小下限")

    # ---- 3. 角落不得被标题栏抢走 ----
    reset()
    drag((2, 206), (302, 506))                    # 左上角应缩放，不是拖动
    got = (w.x(), w.y())
    moved = got != (200, 200)
    # 缩放会把左上角往右下推是正常的；关键是宽度必须变化
    resized = w.width() != 1000
    print(f"5. 左上角=缩放     位置={got} 宽={w.width()}  "
          f"{'OK' if resized else 'FAIL'}")
    if not resized:
        fails.append("左上角未缩放")
    del moved

    # ---- 4. 双击标题栏：连续 4 次必须交替 ----
    print("6. 双击标题栏 4 次")
    for i in range(1, 5):
        dbl_bar()
        want = (i % 2 == 1)
        got = w.isMaximized()
        flag = "OK" if got == want else "FAIL"
        print(f"     第{i}次  isMaximized={got} 期望={want}  {flag}")
        if got != want:
            fails.append(f"双击第{i}次")

    # ---- 5. 最大化按钮：连续 4 次 + 图标切换 ----
    print("7. 最大化按钮 4 次")
    for i in range(1, 5):
        w.titlebar.btn_max.click()
        pump()
        want = (i % 2 == 1)
        got = w.isMaximized()
        k = getattr(w.titlebar.btn_max, "kind", "?")
        kind = k() if callable(k) else k
        want_kind = "restore" if want else "max"
        flag = "OK" if (got == want and kind == want_kind) else "FAIL"
        print(f"     第{i}次  isMaximized={got} 图标={kind} "
              f"期望={want}/{want_kind}  {flag}")
        if not (got == want and kind == want_kind):
            fails.append(f"最大化按钮第{i}次")

    # ---- 6. 三个窗口按钮存在且有尺寸 ----
    print("8. 窗口按钮")
    for name, b in (("最小化", w.titlebar.btn_min),
                    ("最大化", w.titlebar.btn_max),
                    ("关闭", w.titlebar.btn_close)):
        geo = (b.width(), b.height()) if b is not None else (0, 0)
        good = b is not None and geo[0] > 0 and geo[1] > 0
        print(f"     {name}  {geo[0]}x{geo[1]}  "
              f"{'OK' if good else 'FAIL'}")
        if not good:
            fails.append(f"{name}按钮")

    # ---- 7. 最小化 ----
    if w.titlebar.btn_min is not None:
        w.titlebar.btn_min.click()
        pump()
        good = w.isMinimized()
        print(f"9. 最小化          isMinimized={good}  "
              f"{'OK' if good else 'FAIL'}")
        if not good:
            fails.append("最小化")
        w.showNormal()
        pump()

    w._on_close()

    print()
    print("FAILS:", fails if fails else "无")
    print("=" * 60)
    print("  结果:", "有失败或超时" if fails else "全部通过")
    print("=" * 60)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
