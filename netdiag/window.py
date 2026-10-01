"""
无边框窗口的交互：拖动、八向缩放、最大化。

FramelessWindowHint 之后系统不再做命中测试，所有行为都得自己实现。
数据全部来自 Qt 的事件（Qt 直接给 event.position()
和 event.globalPosition()，不用自己做坐标换算）。

边界情况
--------
· 最大化状态下拖动标题栏：先还原再跟随，否则窗口会「粘」在屏幕外。
· 双击标题栏：最大化 / 还原。
· 缩放有最小尺寸约束，避免把窗口拖成不可用大小。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEvent, QObject, QPoint, QRect, Qt, Slot,
)
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget

#: 缩放热区宽度（像素）
GRIP = 6
#: 最小窗口尺寸
MIN_W, MIN_H = 940, 620


class WindowDragger(QObject):
    """把「无边框窗口的鼠标行为」混进主窗口。

    必须继承 QObject——installEventFilter 的参数类型是 QObject，
    传普通 Python 类会直接 TypeError。

    用组合而不是继承：主窗口是 QMainWindow，它的子控件先收事件，
    事件过滤器是最不容易出错的接法。
    """

    def __init__(self, window: QWidget, title_bar: QWidget):
        super().__init__(window)          # 挂在窗口下，随窗口一起销毁
        self.win = window
        self.bar = title_bar
        self._press: QPoint | None = None
        self._zone = ""
        self._rect0 = None
        # 记录普通尺寸，还原时要用。
        #
        # 这一行漏了会导致：任何**非拖拽器发起的最大化**（任务栏拖到顶部、
        # Win+↑、任务栏按钮双击）之后再点还原或双击标题栏，就走到读这个
        # 属性的地方抛 AttributeError。异常发生在 Qt 事件处理器里 → 弹
        # 「程序出错」对话框，窗口卡在最大化再也还原不了。
        # 只有拖拽器发起的最大化会先设它，所以自己点标题栏最大化/还原
        # 一切正常——这个 bug 极难靠手动点出来。
        self._rect_normal: QRect | None = None

        window.installEventFilter(self)
        title_bar.installEventFilter(self)

    # ---------------------------------------------------------------- #
    @Slot(QObject, QEvent)
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.MouseButtonPress:
            if self._handle_press(ev):
                return True
        elif ev.type() == QEvent.Type.MouseMove:
            if self._handle_move(ev):
                return True
        elif ev.type() == QEvent.Type.MouseButtonRelease:
            if self._zone or self._press is not None:
                self._zone = ""
                self._press = None
                return True
        elif ev.type() == QEvent.Type.MouseButtonDblClick:
            # 只认 titlebar 自身收到的事件。窗口上也装了过滤器，
            # 但 DblClick 实际派发给的是命中的子控件。
            if obj is self.bar and self._in_bar(ev):
                self._toggle_maximize()
                return True
        return False

    # ---------------------------------------------------------------- #
    def _toggle_maximize(self) -> None:
        """双击标题栏：最大化 <-> 还原。

        必须用 `windowState()` 的实际标志，不能用 isMaximized()：
        在 frameless 窗口上 setGeometry() 只改几何、不清
        WindowState 的最大化位，此时 isMaximized() 与可见几何
        互相矛盾，二选一都会让第二次双击走进错误的分支。

        还原时先 showNormal() 再显式 setGeometry —— showNormal()
        恢复的是 normalGeometry()，若它已被拖动/缩放改过，
        还原结果会和用户预期不符。
        """
        win = self.win
        if win.windowState() & Qt.WindowState.WindowMaximized:
            win.showNormal()
            r = self._rect_normal or win.normalGeometry()
            if r.isValid():
                win.setGeometry(r)
        else:
            self._rect_normal = QRect(win.geometry())
            win.showMaximized()

    # ---------------------------------------------------------------- #
    def _global_pos(self, ev: QMouseEvent) -> QPoint:
        return ev.globalPosition().toPoint()

    def _in_bar(self, ev: QMouseEvent) -> bool:
        return self._in_bar_at(self.win.mapFromGlobal(
            ev.globalPosition().toPoint()))

    def _hit_zone(self, ev: QMouseEvent) -> str:
        """从鼠标事件判断落在哪个缩放热区。"""
        return self._zone_at_global(ev.globalPosition().toPoint())

    def _in_bar_at(self, local: QPoint) -> bool:
        """local 坐标是否落在标题栏内。"""
        bar = self.bar
        return (bar.x() <= local.x() <= bar.x() + bar.width()
                and bar.y() <= local.y() <= bar.y() + bar.height())

    def _zone_at_global(self, gpos: QPoint) -> str:
        """全局坐标 -> 交互区。

        拆出独立方法是为了能脱离 QMouseEvent 直接测：PySide6 的
        QMouseEvent 构造函数重载在各版本上对 globalPosition 的
        支持并不一致，用它做测试会得到与真实行为不符的结果。
        """
        local = self.win.mapFromGlobal(gpos)
        w, h = self.win.width(), self.win.height()
        g = GRIP
        x, y = local.x(), local.y()
        left = x <= g
        right = x >= w - g
        top = y <= g
        bottom = y >= h - g

        # 角落必须**先于**标题栏判定。
        #
        # 标题栏贯通到窗口顶端（高 BAR_H），而缩放热区只有 GRIP 宽，
        # 若标题栏优先，顶部 4 个角就会变成「拖动窗口」——恰好
        # 和真实 Windows 相反（系统窗口的角落是 resize grip 优先）。
        if top and left:
            return "top-left"
        if top and right:
            return "top-right"
        if bottom and left:
            return "bottom-left"
        if bottom and right:
            return "bottom-right"

        # 标题栏内部：拖动窗口
        if self._in_bar_at(local):
            return "caption"

        if top:
            return "top"
        if bottom:
            return "bottom"
        if left:
            return "left"
        if right:
            return "right"
        return ""

    # ---------------------------------------------------------------- #
    def _handle_press(self, ev: QMouseEvent) -> bool:
        if ev.button() != Qt.MouseButton.LeftButton:
            return False
        self._zone = self._hit_zone(ev)
        if not self._zone:
            return False
        self._press = self._global_pos(ev)
        if self._zone == "caption":
            # 最大化时先把几何记下来，还原后按比例恢复鼠标位置
            self._rect0 = self.win.geometry()
        else:
            self._rect0 = self.win.geometry()
        return True

    def _handle_move(self, ev: QMouseEvent) -> bool:
        if not self._zone or self._press is None:
            return False
        pos = self._global_pos(ev)
        dx = pos.x() - self._press.x()
        dy = pos.y() - self._press.y()

        if self._zone == "caption":
            self._do_move(dx, dy)
        else:
            self._do_resize(dx, dy)
        return True

    def _do_move(self, dx: int, dy: int) -> None:
        win = self.win
        if win.windowState() & Qt.WindowState.WindowMaximized:
            # 最大化时拖动：先还原，并把鼠标锚定在标题栏上的相对位置
            r = self._rect0 or win.normalGeometry()
            ratio = (self._press.x() - r.left()) / max(1, r.width())
            win.showNormal()
            nw = win.width()
            win.move(self._press.x() - int(nw * ratio), self._press.y() - 16)
            # 重设锚点，否则还原后第一次 move 会跳一大段
            self._press = QPoint(self._press.x(), self._press.y())
            return
        win.move(win.x() + dx, win.y() + dy)
        self._press = pos_after(self._press, dx, dy)

    def _do_resize(self, dx: int, dy: int) -> None:
        r = self._rect0
        if r is None:
            return
        x, y, w, h = r.x(), r.y(), r.width(), r.height()
        z = self._zone
        if "left" in z:
            w = max(MIN_W, w - dx)
            x = r.x() + (r.width() - w)
        if "right" in z:
            w = max(MIN_W, w + dx)
        if "top" in z:
            h = max(MIN_H, h - dy)
            y = r.y() + (r.height() - h)
        if "bottom" in z:
            h = max(MIN_H, h + dy)
        self.win.setGeometry(x, y, w, h)


def pos_after(p: QPoint, dx: int, dy: int) -> QPoint:
    return QPoint(p.x() + dx, p.y() + dy)
