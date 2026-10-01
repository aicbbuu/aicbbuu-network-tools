"""
主窗口（PySide6）。

布局：
┌────────────────────────────────────────────┐
��  标题栏：品牌标记 + 标题 + 最小化/最大化/关闭  │  44px
├──────────┬─────────────────────────────────┤
│  侧边栏   │  页面内容（QStackedWidget）      │
│  品牌区   │                                 │
│  导航项×6 │                                 │
│  ───────  │                                 │
│  主题切换 │                                 │
└──────────┴─────────────────────────────────┘

圆角与阴影
----------
窗口用 frameless + WA_TranslucentBackground，配合 QSS 的
border-radius 和 QGraphicsDropShadowEffect。这样圆角边缘是 Qt 自己
抗锯齿画出来的，阴影也是真实的高斯模糊——不需要挖窗口区域。

代价是无边框窗口要自己做拖动/缩放/最大化，见 window.py。
"""
from __future__ import annotations

import os
import sys
from typing import Any

from PySide6.QtCore import QEvent, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from . import APP_TITLE, APP_VERSION   # 窗口标题仍带版本号（系统 UI 的一部分）
from .core.encoding import request_shutdown
from .core.runner import Dispatcher
from .ui import theme as T
from .ui.pages import ALL_PAGES, GROUPS, PAGES, SIDEBAR
from .ui.widgets import add_shadow, hline
from .window import WindowDragger

def resource_dir() -> str:
    """资源目录绝对路径。源码运行与 PyInstaller 打包后都能用。

    刻意做成一个函数而不是到处 os.path.join：早期版本在两处各写了一遍
    路径拼接，其中一处少拼一层就静默找不到文件（QPixmap 返回 null），
    表现为「软件内图标变成一个纯色圆点」，而另一处却正常——极难定位。

    两种运行方式的实际布局不同，必须都试：
      源码：netdiag/app.py 与 netdiag/assets/ 平级
      打包：build.py 的 --add-data 把 assets 放到 sys._MEIPASS/assets
    """
    if hasattr(sys, "_MEIPASS"):                 # PyInstaller
        return os.path.join(sys._MEIPASS, "assets")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets")


#: 事件泵间隔（毫秒）。太大则界面迟钝，太小则空转。
_PUMP_MS = 60


# ---------------------------------------------------------------- #
#  品牌标记
# ---------------------------------------------------------------- #
class BrandMark(QLabel):
    """标题栏与侧边栏的品牌标记。

    **直接显示 icon_*.png，不手绘。**

    图标必须与 exe 保持一致。若此处另行手绘而 exe 图标由
    tools/make_icon.py 生成，两套代码各画各的：改了生成器，
    软件内的标记不变（四角像素比对：
    手绘版 64/64 不透明=切角，生成版 0/0=圆形）。

    手绘的初衷是「矢量在任何 DPI 下都清晰、不引入图片资源」，
    但代价是两处真相必然漂移。图标本身已经是 4x 超采样的
    多尺寸 PNG，256px 那张足够任何 DPI 缩放；用 QPixmap 加载
    也没有生命周期问题（QLabel 持有引用）。

    找不到资源时回落到一个纯色圆点，保证标题栏不会空一块。
    """

    def __init__(self, size: int = 24, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._pix: QPixmap | None = None
        self._load()

    def _load(self) -> None:
        """挑一张不小于目标尺寸的 PNG，按需缩小。"""
        assets = resource_dir()
        # 优先选 >= 目标尺寸的最小那张：既够清晰又不浪费内存
        for want in (self._size * 2, self._size, 32, 48, 64, 128, 256):
            path = os.path.join(assets, f"icon_{want}.png")
            if os.path.isfile(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    self._pix = pm
                    break
        if self._pix is None:
            ico = os.path.join(assets, "icon.ico")
            if os.path.isfile(ico):
                pm = QPixmap(ico)
                if not pm.isNull():
                    self._pix = pm
        if self._pix is None:
            # 资源缺失：画一个圆点占位，别让标题栏空着
            self.setStyleSheet(
                f"background: {T.LIGHT['accent']};"
                f" border-radius: {self._size // 2}px;")
            return
        self.setPixmap(
            self._pix.scaled(self._size, self._size,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation))
        self.setScaledContents(False)


# ---------------------------------------------------------------- #
#  窗口按钮
# ---------------------------------------------------------------- #
class WinButton(QPushButton):
    """最小化 / 最大化 / 关闭。图标用 QPainter 自绘，不用 Unicode 字形。

    **为什么不用「—」「☐」「❐」「✕」这类字符。** 它们在不同字体下
    渲染得完全不可控——**基线高低和笔画粗细都不一样**，同一行里三个
    按钮会明显不在一个水平线上：

    - `—`（U+2014）是长破折号，实际宽度随字体变，在 40px 按钮里
      显得又细又长
    - `☐`（U+2610）是「空方框」，**字面尺寸只有em 的 60% 左右**，
      且视觉重心偏上，所以看起来明显比旁边的 ✕ 小一圈
    - `❐`（U+2750）在部分字体里缺失，会掉回系统字体，形状完全不可控
    - `✕`（U+2715）相对居中，但笔画比 `—` 粗

    结果就是三个图标大小不一、粗细不一、上下不齐。要「统一」只能靠
    逐个微调字符，而字形随系统字体变化，微调量根本不可控。

    **自绘能精确控制**：线宽统一 1.4px（2x 屏上自动加粗）、图形居中、
    四角圆角一致。而且最大化/还原是两个明确的状态，不需要「❐ 这个
    字符是否被字体支持」这种赌博。

    图形参照 Windows 11 的窗口控制（细线条、小尺寸、居中偏上）。
    """

    # 1x 下的线宽。2x 屏上按 devicePixelRatio 加粗，否则视觉上会
    # 细成一条几乎看不见的灰线——这正是「有点太小」的另一半原因。
    STROKE = 1.4

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        # 三个按钮用同一个 objectName。给 close 单独一个 "WinClose"
        # 并配红色 hover（模仿 Windows 惯例）会让它在视觉上重于另外两个，
        # 而这一组是平级关系，所以统一处理。
        self.setObjectName("WinBtn")
        self.setFixedSize(40, 32)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ---- 图形 ----
    # ---- 四个图标的共同基准 ----
    #
    # **所有图形都必须落在同一个视觉中心上。** 按钮 40x32，中心
    # 是 (20, 16)。第一版把方框画在 y=7..15、减号画在 y=16——
    # 于是一半图形浮在上半、一半沉在下半，用户一眼就看出「没对齐」。
    #
    # 规则很简单：**中心一律取 CY=16**，只有减号允许下移 2px
    #（Windows 的惯例，线在正中心会让人以为是分割线）。
    CX = 10.0          # 主体图形的中心 x
    CY = 16.0          # 主体图形的中心 y
    SIZE = 8.0         # 方框类图形的边长

    #: 每个 kind 的**主体图形中心**（restore 取前框，不取外接矩形）。
    #: 单独暴露出来是为了让测试能直接断言「四个图标在同一条线上」，
    #: 而不用在测试里重��遍同样的几何推导——那种重复实现一改
    #: 就和代码对不上，测试就成了摆设。
    SUBJECT_CENTER = {"min": (10.0, 18.0), "max": (10.0, 16.0),
                      "restore": (10.0, 16.0), "close": (10.0, 16.0)}

    @staticmethod
    def _min_lines() -> tuple[tuple[float, float, float, float], ...]:
        """最小化：一条水平线，比中心低 2px。"""
        return ((6.0, 18.0, 14.0, 18.0),)

    @staticmethod
    def _max_lines() -> tuple[tuple[float, float, float, float], ...]:
        """最大化：方框（四条边），中心 = (10, 16)。"""
        x0, y0 = 6.0, 12.0
        x1, y1 = 14.0, 20.0
        return ((x0, y0, x1, y0),
                (x1, y0, x1, y1),
                (x1, y1, x0, y1),
                (x0, y1, x0, y0))

    @staticmethod
    def _restore_lines() -> tuple[tuple[float, float, float, float], ...]:
        """还原：两个 8x8 的框错开 3px，外接中心仍取 (10, 16)。

        后框往**右上**错：这样视觉重心落回中心，不会因为多了个框
        就整体偏右上（第一版错开 4px 时看着就偏了）。
        """
        # **前框（完整那个）的中心严格取 (10, 16)**，和 max/close 同点。
        # 后框往右上错 3px。
        #
        # 不能按「两个框的外接矩形」来对：外接框比单个框大 3px，
        # 中心会算到 13.5 —— 视觉上整个图标浮在上半，看着没对齐。
        # 人的判断基准是**主体图形**（这里是完整的前框）。
        d = 3.0
        fx0, fy0 = 6.0, 12.0          # 前框 8x8，中心 (10, 16)
        bx0, by0 = fx0 + d, fy0 - d   # 后框（右上）
        return ((bx0, by0, bx0 + 8, by0),          # 后框：上边
                (bx0 + 8, by0, bx0 + 8, by0 + 8),  # 后框：右边
                (fx0, fy0, fx0 + 8, fy0),          # 前框：上边
                (fx0 + 8, fy0, fx0 + 8, fy0 + 8),  # 前框：右边
                (fx0 + 8, fy0 + 8, fx0, fy0 + 8),  # 前框：下边
                (fx0, fy0 + 8, fx0, fy0))          # 前框：左边

    @staticmethod
    def _close_lines() -> tuple[tuple[float, float, float, float], ...]:
        """关闭：叉，8x8，中心 = (10, 16)。

        **8x8，不是 6x6。** 第一版按 6px 画，放大后明显比旁边的方框
        小一圈。斜线的视觉重量天然比直角框轻，所以要**按外接框对齐
        而不是按感觉画**。
        """
        return ((6.0, 12.0, 14.0, 20.0),
                (14.0, 12.0, 6.0, 20.0))

    @staticmethod
    def _lines_of(kind: str) -> tuple[tuple[float, float, float, float], ...]:
        """按 kind 取线段表。抽成静态方法是为了让测试能在**不创建按钮**
        的前提下量图形尺寸——QWidget 在无显示环境下构造本身就很慢，
        而尺寸一致性是这个图标唯一需要守住的性质。
        """
        return {
            "min": WinButton._min_lines(),
            "max": WinButton._max_lines(),
            "restore": WinButton._restore_lines(),
            "close": WinButton._close_lines(),
        }[kind]

    def _lines(self) -> tuple[tuple[float, float, float, float], ...]:
        return self._lines_of(self.kind)

    def paintEvent(self, e) -> None:  # noqa: N802
        """先让 QSS 画出 hover / 按下的底色，再把图标描上去。

        **必须调 super()**：QSS 的 background / border 是样式绘制，
        不调父类的 paintEvent 就只画得出图标、底色永远不出来。
        """
        super().paintEvent(e)
        p = QPainter(self)
        try:
            dpr = self.devicePixelRatioF()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 颜色随主题走。hover / pressed 时用更深的文字色，
            # 视觉上「这个按钮正被按」——底色由 QSS 负责，这里只管线。
            base = getattr(self, "_icon_color", None) or "#5b6880"
            if self.underMouse():
                base = getattr(self, "_icon_color_hover", None) or "#16202f"
            p.setPen(QPen(QColor(base),
                          max(1.0, self.STROKE * dpr),
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            for x0, y0, x1, y1 in self._lines():
                p.drawLine(int(x0 * dpr), int(y0 * dpr),
                           int(x1 * dpr), int(y1 * dpr))
        finally:
            p.end()

    def set_theme(self, theme: dict[str, Any]) -> None:
        """切主题时更新图标颜色（QSS 管不到自绘内容）。"""
        self._icon_color = theme["text_dim"]
        self._icon_color_hover = theme["text"]
        self.update()

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()



class ThemeButton(QPushButton):
    """标题栏上的主题切换：太阳 / 月亮。同样**自绘**，不用 Unicode 字形。

    **为什么不用 ☀ / ☾。** 用户先说「用图标代替月亮和太阳」，改完又
    指出窗口控制「太难看，很复古，也有点太小」——后者其实也包括
    这个按钮：☾ 在 Segoe UI Symbol 里是**书法体**（书法笔触的弧线，
    还会甩出一个尾巴），和旁边三个规整的几何线条完全不是一套语言，
    视觉上又小又细。截图放大 4 倍一眼就看出来了。

    既然窗口控制已经自绘，这个一起改掉才成套：**四个图标同线宽、
    同视觉重量、同笔画逻辑**。

    **语义仍是「点下去会变成什么」**：浅色下显示月亮（点了变深色），
    深色下显示太阳（点了变浅色）。和 Windows 的行为一致。
    """

    STROKE = 1.4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WinBtn")     # 和窗口控制共用一套 hover
        self.setFixedSize(40, 32)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._dark = False
        self._icon_color = "#5b6880"
        self._icon_color_hover = "#16202f"
        self._sync_tip()

    def set_dark(self, dark: bool) -> None:
        if dark != self._dark:
            self._dark = dark
            self._sync_tip()
            self.update()

    def set_theme(self, theme: dict[str, Any]) -> None:
        self._icon_color = theme["text_dim"]
        self._icon_color_hover = theme["text"]
        self.update()

    def _sync_tip(self) -> None:
        """tooltip 说当前状态；图标说「点下去会变成什么」。

        两者不能反：早期侧边栏那版写「深色模式」，在深色主题下读起来
        像「已经切到深色了」，是歧义。
        """
        self.setToolTip("切换到浅色模式" if self._dark else "切换到深色模式")

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)        # QSS 的 hover 底色靠父类画
        import math
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QBrush, QLinearGradient, QPainterPath

        p = QPainter(self)
        try:
            dpr = self.devicePixelRatioF()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            color = (self._icon_color_hover if self.underMouse()
                     else self._icon_color)
            # 中心与三个窗口控制**同一个点** (10, 16)——差 1px 肉眼就
            # 看得出，所以两边都写死成同一个常量。
            cx, cy, r = 10.0, 16.0, 5.6

            if self._dark:
                # 太阳：实心圆心 + 8 根短射线。
                #
                # **圆心用实心而不是空心环。** 空心环的视觉重量只有
                # 实心的一半，放在这个尺寸下会比旁边的方框和叉明显
                # 轻——整个按钮看着像「没画完」。
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(self._fill_grad(p, cx - r, cy - r,
                                                   cx + r, cy + r, color)))
                p.drawEllipse(QPointF(cx, cy), r * 0.58, r * 0.58)
                # 射线保留细线：它必须比实心圆心轻，否则整个太阳糊成
                # 一坨。线宽比窗口控制的 STROKE 略细一点。
                p.setPen(QPen(QColor(color), max(1.0, 1.3 * dpr),
                              Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap))
                for k in range(8):
                    a = k * math.pi / 4.0
                    dx, dy = math.cos(a), math.sin(a)
                    # 起点贴近圆心但留缝：连成实心团就成了「画糊了」。
                    # 终点 4.0 而不是 3.5——用 RoundCap 画短线段时，
                    # 两个圆头会吃掉长度，3.5 画出来每根像一根小短线，
                    # 看着不完整。
                    p.drawLine(QPointF(cx + dx * (r + 1.4),
                                       cy + dy * (r + 1.4)),
                               QPointF(cx + dx * (r + 4.1),
                                       cy + dy * (r + 4.1)))
            else:
                # 月牙：大圆 **减去** 偏心的圆，剩实心的弯钩。
                #
                # **必须用 subtracted()。** 手工拼两段弧接不严——右上角
                # 缺一段，看起来像「C」而不是月亮，而且弧的端点角度稍
                # 有偏差就有可见缺口。subtracted() 是真正的布尔运算。
                #
                # **实心而不是描边。** 描边版的月牙被挖掉一大块之后，
                # 剩下的可见面积天然小，同样的线宽就显得比旁边的方框
                # 和叉弱一档。实心填充的重量才配得上那三个。
                path = QPainterPath()
                path.addEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
                r2 = r * 0.88
                # 咬掉的那个圆往右上偏：偏移量决定月牙多厚。
                bite = QPainterPath()
                bite.addEllipse(QRectF(cx + 2.6 - r2, cy - 1.5 - r2,
                                       2 * r2, 2 * r2))
                cres = path.subtracted(bite)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(self._fill_grad(p, cx - r, cy - r,
                                                   cx + r, cy + r, color)))
                p.drawPath(cres)
        finally:
            p.end()

    def _fill_grad(self, p: QPainter, x0: float, y0: float,
                   x1: float, y1: float, color: str) -> QBrush:
        """实心图形的填充：左上略亮 -> 右下略暗。

        **要渐变不要纯色。** 纯色块看着像贴纸；顺着光照方向来一道
        过渡，图标就有了体积，和按钮底色也融得进去。渐变幅度刻意
        很小（约 18%），只是让它「不是一个死色块」。
        """
        from PySide6.QtGui import QBrush, QColor, QLinearGradient

        base = QColor(color)
        top = QColor(base)
        top.lighter(118)          # 上缘亮一档
        bot = QColor(base)
        bot.darker(112)           # 下缘暗一档
        g = QLinearGradient(x0, y0, x1, y1)
        g.setColorAt(0.0, top)
        g.setColorAt(1.0, bot)
        return QBrush(g)



class Sidebar(QFrame):
    navigate = Signal(str)

    def __init__(self, theme: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(T.NAV_W)
        self.t = theme
        self._buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        #: 二级菜单组标题按钮：标题 -> 按钮
        self._group_heads: dict[str, QPushButton] = {}
        #: 二级菜单组内的页面名：标题 -> [页面名, ...]
        self._groups: dict[str, list[str]] = {}
        #: QButtonGroup 的自增 id
        self._i = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE_MD, T.SPACE_LG,
                               T.SPACE_MD, T.SPACE_MD)
        lay.setSpacing(T.SPACE_XS)

        # ---- 品牌区 ----
        brand = QWidget()
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(T.SPACE_XS, 0, 0, 0)
        bl.setSpacing(T.SPACE_MD)
        bl.addWidget(BrandMark(28))
        txt = QVBoxLayout()
        txt.setSpacing(0)
        n1 = QLabel("aicbbuu")
        n1.setObjectName("BrandName")
        n2 = QLabel("network tools")
        n2.setObjectName("BrandSub")
        txt.addWidget(n1)
        txt.addWidget(n2)
        bl.addLayout(txt)
        bl.addStretch(1)
        lay.addWidget(brand)
        lay.addSpacing(T.SPACE_MD)
        lay.addWidget(hline(theme))
        lay.addSpacing(T.SPACE_XS)

        # ---- 导航项：放进可滚动区 ----
        #
        # 20 个页面在 204px 宽的侧边栏里需要约 900px 高，而常见
        # 笔记本窗口只有 700~800px。之前所有导航项都直接塞进侧边栏
        # 的主 layout，装不下时 Qt 会**压缩每个按钮**——于是字挤在
        # 一起、垂直居中不成行，看着像「字体坏了」。
        #
        # 改成滚动区后：装得下就照常显示，装不下就出滚动条，
        # **按钮高度永远不会被压缩**。品牌区和主题按钮留在滚动区
        # 外面，始终可见可点。
        self._scroll = QScrollArea()
        self._scroll.setObjectName("NavScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._nav_holder = QWidget()
        self._nav_holder.setObjectName("NavHolder")
        nav_lay = QVBoxLayout(self._nav_holder)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(T.SPACE_XS)

        # ---- 导航项 ----
        #
        # 渲染 :data:`SIDEBAR`，元素要么是页面类（平铺一级项），
        # 要么是 ("group", 标题) —— 二级菜单。
        #
        # 二级菜单做成「可折叠」：点组标题展开/收起。这样侧边栏
        # 默认只显示 20 项（一级），不会因为多出 8 个修复项而立刻
        # 出现滚动条；用户主动点开「网络修复」才看到具体操作。
        #
        # 组内项**默认不建按钮**，展开时才建。全部建出来的话，
        # 即使折叠着也要占 8×31 = 248px 的高度——那就等于白折叠了。
        self._i = 0
        for item in SIDEBAR:
            if isinstance(item, tuple) and item and item[0] == "group":
                self._add_group(nav_lay, item[1])
            else:
                self._add_item(nav_lay, item)

        nav_lay.addStretch(1)
        self._scroll.setWidget(self._nav_holder)
        lay.addWidget(self._scroll, 1)
        # 底下什么都不挂。主题切换按钮挪到标题栏后，这里原本只剩一条
        # 分割线——一条线下面空着一大片，看起来像被截断的残骸，
        # 所以连分割线一起去掉。
        #
        # 侧边栏因此变成纯列表：品牌区在上，导航占满中间，
        # 底部留白由 stretch 自然产生。这比一条无意义的横线干净。

    def _add_item(self, nav_lay, cls, in_group: bool = False) -> None:
        """加一个导航按钮。组内项用 NavButtonSub（缩进 + 小一号）。"""
        btn = QPushButton(("    " + cls.TITLE) if in_group else cls.TITLE)
        btn.setObjectName("NavButtonSub" if in_group else "NavButton")
        btn.setCheckable(True)
        # **刻意不给导航项配图标。** 试过给 20 个页面各画一个
        # 几何图形（ui/navicons.py），结论是不要——20 个小图形挤在
        # 204px 宽的侧边栏里，缩小后
        # 全是噪点，反而不如纯文字好认。主题按钮的图标也一并去掉。
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # 导航项点击后不要焦点，否则会留一圈虚线框
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(cls.SUBTITLE)
        btn.clicked.connect(lambda _=False, n=cls.NAME: self.navigate.emit(n))
        self._group.addButton(btn, self._i)
        self._i += 1
        self._buttons[cls.NAME] = btn
        nav_lay.addWidget(btn)

    def _add_group(self, nav_lay, title: str) -> None:
        """加一个可折叠的二级菜单组。"""
        head = QPushButton(title)
        # 展开/收起用**两个 objectName** 区分，不用 `:not(:checked)`。
        # Qt 的 QSS 不支持 :not()，遇到它会静默丢弃这条规则之后的
        # 全部内容——曾因此把 QFrame#Card / QPushButton#Primary /
        # QLineEdit / QPlainTextEdit#Console 四条规则一起废掉，
        # 界面表现是「卡片看不见、按钮变灰、输出区变白」。
        # 详见 ui/theme.py 里 NavGroupOff 的注释。
        head.setObjectName("NavGroup")
        head.setCheckable(True)
        # **默认收起。** 展开后是 20 + 8 = 28 项，204px 宽的侧边栏
        # 装不下（每项 31px，总共要 868px），必然出滚动条。用户看到
        # 满屏的二级项也不觉得清爽——需要修网络的时候他会去点那个组。
        head.setChecked(False)
        head.setToolTip("展开网络修复的 8 项操作")
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        head.toggled.connect(lambda on, t=title: self._toggle_group(t, on))
        nav_lay.addWidget(head)
        self._group_mark(head, False)
        # **必须显式调一次 _toggle_group(False)。**
        # setChecked(False) 在值本就是 False 时**不发 toggled 信号**
        # （Qt 只在值真的改变时才发），于是子项保持默认的可见状态——
        # 表现是「明明标记为收起，8 个子项却全都露在外面」。
        # 这个坑很隐蔽：head.isChecked() 读出来是对的，肉眼一看
        # 标题也是 ▸，只有子项赖着不走。
        self._group_heads[title] = head
        self._groups[title] = []
        for cls in GROUPS[title]:
            self._add_item(nav_lay, cls, in_group=True)
            self._groups[title].append(cls.NAME)
        # 子项建完再收起——_toggle_group 遍历的是 self._groups[title]，
        # 顺序反了的话它拿到空列表，8 个子项一个都藏不住。
        self._toggle_group(title, False)

    @staticmethod
    def _group_mark(head: QPushButton, expanded: bool) -> None:
        """在组标题后面标出展开 / 收起。

        **用文字符号而不是图标**——导航区已经明确取消图标了，
        这里再加一个下拉三角就自相矛盾。`▸` 收起 / `▾` 展开，
        是纯文本系统里通用的树形标记。
        """
        base = head.text().split("  ")[0].rstrip()
        head.setText(f"{base}  {'▾' if expanded else '▸'}")

    def _toggle_group(self, title: str, on: bool) -> None:
        # 收起态换个 objectName，好让 QSS 能单独上色（Qt 没有 :not）
        head = self._group_heads.get(title)
        if head is not None:
            head.setObjectName("NavGroup" if on else "NavGroupOff")
            # objectName 变了要重新 polish，否则旧样式的缓存还在
            head.style().unpolish(head)
            head.style().polish(head)
        """展开 / 收起一个二级菜单组。

        **只设 visible，不重建控件。** 重建的话展开时会新建一批
        按钮 —— 那些按钮已经 addButton 进 self._group（QButtonGroup）
        了，重复 add 会打乱 checkable 的互斥关系，表现为「同时
        高亮两项」。而且页面对象在 App.pages 里是复用的，重建按钮
        不会丢状态，只是白白费劲。
        """
        head = self._group_heads.get(title)
        if head is not None:
            self._group_mark(head, on)
        for name in self._groups.get(title, ()):
            btn = self._buttons.get(name)
            if btn is not None:
                btn.setVisible(on)


    def select(self, name: str) -> None:
        """高亮某个页面。若它在收起的二级菜单里，先把组展开。

        不展开的话会出现「当前页面在某个二级项上，但那个二级项
        看不见」——用户完全不知道自己在哪一页。
        """
        btn = self._buttons.get(name)
        if btn is None:
            return
        if not btn.isVisible():
            for title, names in self._groups.items():
                if name in names:
                    head = self._group_heads.get(title)
                    if head is not None and not head.isChecked():
                        head.setChecked(True)   # 触发 _toggle_group
                    break
        btn.setChecked(True)


# ---------------------------------------------------------------- #
#  标题栏
# ---------------------------------------------------------------- #
class TitleBar(QWidget):
    minimize = Signal()
    maximize = Signal()
    close = Signal()
    toggle_theme = Signal()

    def __init__(self, theme: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(T.TITLE_H)
        self.t = theme

        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.SPACE_LG, 0, T.SPACE_XS, 0)
        lay.setSpacing(T.SPACE_MD)
        # 刻意**不放** BrandMark：侧边栏顶部已经有一个图标，标题栏
        # 再放一个就是重复。
        title = QLabel(APP_TITLE)
        title.setObjectName("TitleLabel")
        lay.addWidget(title)
        # 版本号紧跟标题，灰色小字。
        #
        # 之前版本号只在**系统窗口标题**里（setWindowTitle），而这个
        # 窗口是无边框的，系统标题根本不显示，用户看不到自己是哪一版。
        # 报 bug 时报不出版本号是最难排查的一环。
        #
        # 单独一个 QLabel 而不是拼进标题文字：这样版本号可以有自己的
        # objectName，QSS 能给它单独的灰色，不必用富文本。
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("TitleVersion")
        lay.addWidget(ver)
        lay.addSpacing(-T.SPACE_MD)   # 抵掉 layout 间距，让版本号贴着标题
        lay.addStretch(1)

        # 主题切换放在最小化左边。
        #
        # **为什么从侧边栏挪过来**：它是全局开关，不是某一个页面的
        # 导航项。放在侧边栏底部会让人以为它是列表的最后一页，而放在
        # 窗口控制旁边才是「设置」该在的位置——和 VS Code、Edge
        # 一样。侧边栏因此少一项，列表更纯粹。
        self.btn_theme = ThemeButton()
        self.btn_theme.clicked.connect(self.toggle_theme.emit)
        lay.addWidget(self.btn_theme)

        self.btn_min = WinButton("min")
        self.btn_max = WinButton("max")
        self.btn_close = WinButton("close")
        for b, sig in ((self.btn_min, self.minimize),
                       (self.btn_max, self.maximize),
                       (self.btn_close, self.close)):
            b.clicked.connect(sig.emit)
            lay.addWidget(b)
            # 图标是自绘的，必须**初始化时就**给一次主题字典。放到
            # toggle_theme 里设的话，首次打开图标会退回内置的浅灰，
            # 在浅色标题栏上勉强能看，但那是「碰巧不糟」而不是「正确」。
            b.set_theme(theme)

    def sync_win_states(self, maximized: bool | None = None) -> None:
        """同步最大化/还原图标。

        最大化后按钮要变成「还原」（两个错开的方框），而这两个图标的
        线条位置不同——所以换图标等于重画，**颜色也要重设**。
        """
        if maximized is None:
            maximized = bool(getattr(self.parent(), "_maximized", False))
        self.btn_max.set_kind("restore" if maximized else "max")
        self.btn_max.set_theme(self.t)


class RoundedRoot(QWidget):
    """画抗锯齿圆角背景的中央容器。

    为什么不用更省事的两种做法
    ----------------------------
    · QSS 的 ``border-radius``：走样式渲染路径，**不裁窗口**。
      DWM 仍按矩形合成，切角处漏出黑色底层（实测左上角黑三角）。
    · ``QWidget.setMask(QRegion(...))``：像素级**二值**裁切，
      边缘是硬阶梯，7 倍放大就能看到锯齿。

    这里用 QPainter 画圆角矩形——drawRoundedRect 开抗锯齿，
    圆角外保持 alpha=0。配合 ``WA_TranslucentBackground``，Windows
    对全透明像素自动放行点击穿透，所以圆角外的区域点不到本窗口。

    注意：QWidget 默认不透明，必须显式 WA_TranslucentBackground，
    否则 paintEvent 里留的透明区域会被填成父级底色。
    """

    def __init__(self, radius: int = 14, parent=None):
        super().__init__(parent)
        self._r = radius
        self._bg = QColor("#f4f7fb")
        self._edge = QColor("#dbe4f0")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 三个属性缺一不可，任意一个漏掉都会让圆角外的透明区被
        # 填成不透明（QPainter 明明画了抗锯齿，屏幕上却是硬阶梯）：
        #   WA_TranslucentBackground  走分层窗口，保留 alpha
        #   WA_NoSystemBackground     不让系统刷一层底色
        #   autoFillBackground = False 不让 Qt 用调色板窗口色填充
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def retheme(self, theme: dict[str, Any]) -> None:
        # 键名是 "window"（见 theme.LIGHT/DARK），不是 "window_bg"。
        # 写错键名不会报错，只会静默 fallback 到白色——切深色
        # 主题时窗口圆角仍是白的，是很难看出来的一类 bug。
        self._bg = QColor(theme.get("window", "#f4f7fb"))
        self._edge = QColor(theme.get("border", "#dbe4f0"))
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self._r
        w, h = self.width(), self.height()
        if w <= 2 * r or h <= 2 * r:
            p.fillRect(self.rect(), self._bg)
            return

        # 外层：抗锯齿圆角底色。1px 内缩让描边正好占满一个像素
        # ——画在整数坐标上的 1px 线会跨两个像素，渲染成 0.5 宽
        # 的淡线。
        outer = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._bg)
        p.drawRoundedRect(outer, r, r)

        # 描边画成「向内 1px 的第二个圆角」，而不是给同一个路径
        # 加 pen。理由：pen 画在路径**中心线**上，一半会伸到
        # 圆角外侧，把本该透明的区域涂成实色——那正是「黑三角」
        # 的另一种形态。向内画则天然不会越界。
        #
        # 描边必须用 QPainter 而不是 QSS：QSS 的 border 会被不透明
        # 底色盖住，且样式渲染路径不参与抗锯齿，圆角处会变硬。
        inner = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        p.setPen(QPen(self._edge, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(inner, r - 1.0, r - 1.0)


# ---------------------------------------------------------------- #
#  主窗口
# ---------------------------------------------------------------- #
class App(QMainWindow):
    # 窗口圆角半径。固定值，不随窗口尺寸放大——与 Windows 11
    # 的系统窗口一致，也让 window.py 里的角落 hit-test 可预测。
    # 12 在 1120x760 这种大窗口上视觉太弱（浅色底上几乎看不出
    # 圆角），14 配合下面 paintEvent 里的 1px 描边才立得住。
    CORNER = 14

    def __init__(self, theme_name: str | None = None):
        super().__init__()
        self._mask_size = (0, 0)
        self.theme_name = theme_name or self._detect_theme()
        self.t = T.apply(QApplication.instance(), self.theme_name)
        self.dispatcher = Dispatcher()
        self.pages: dict[str, Any] = {}
        self._current: str | None = None
        self._closing = False

        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION}")
        self.resize(1120, 760)
        self.setMinimumSize(QSize(940, 620))
        self._apply_icon()

        # 无边框 + 透明
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 圆角**不**用 setMask。QRegion 是像素级二值裁切，边缘
        # 必然是硬阶梯（7 倍放大可见）。也不用 QSS 的
        # border-radius——它走样式渲染路径，根本不裁窗口，DWM 仍
        # 按矩形合成，切角处会漏出黑色底层。
        #
        # 做法：中央控件换成自绘的 RoundedRoot，QPainter 画圆角
        # 矩形（有抗锯齿），圆角外留全透明。配合
        # WA_TranslucentBackground，Windows 对全透明像素自动放行
        # 点击穿透，所以点不到「窗口外的桌面」。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        root = RoundedRoot(self.CORNER)
        self.setCentralWidget(root)
        self._round_root = root

        self._build()
        self.switch_to(PAGES[0].NAME)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)

    # ---------------------------------------------------------------- #
    def _apply_icon(self) -> None:
        """设置窗口与任务栏图标。

        两处都要设，缺一不可：
        · QApplication.setWindowIcon —— 某些 Windows 版本的任务栏
          从这里取图标
        · self.setWindowIcon —— 无边框窗口（FramelessWindowHint）
          **不会**自动继承 QApplication 的图标，不设的话任务栏
          就是一个没有图标的白块

        资源路径用 resource_dir() 统一解析，不在这里手写 dirname。
        """
        ico = os.path.join(resource_dir(), "icon.ico")
        png = os.path.join(resource_dir(), "icon_256.png")
        try:
            if os.path.isfile(ico):
                icon = QIcon(ico)
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    app = QApplication.instance()
                    if app is not None:
                        app.setWindowIcon(icon)
        except Exception:
            pass
        # .ico 在某些 Qt 构建里解析失败，退回 PNG
        if self.windowIcon().isNull() and os.path.isfile(png):
            try:
                icon = QIcon(png)
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    app = QApplication.instance()
                    if app is not None:
                        app.setWindowIcon(icon)
            except Exception:
                pass

    def _detect_theme(self) -> str:
        """默认浅色。

        不跟随系统主题：排障时需要看清输出区的等宽文本，浅色背景下
        可读性更好；跟随系统也不是使用者能预期的行为。
        """
        return "light"

    # ---------------------------------------------------------------- #
    def _build(self) -> None:
        # RoundedRoot 负责画抗锯齿圆角背景；内容照常加进去。
        # 刻意**不加阴影**：QGraphicsDropShadowEffect 的模糊从控件
        # 边缘向外扩散 blur/2 像素，blur=60 意味着 30px 灰雾铺在
        # 窗口四周——在浅色底上就是「发灰、不细腻」的直接来源。
        root = self._round_root
        root.retheme(self.t)

        outer = QVBoxLayout(root)
        # 描边已移除，边距必须归零，否则圆角内侧会留一圈空隙
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 标题栏 ----
        self.titlebar = TitleBar(self.t, root)
        self.titlebar.minimize.connect(self.showMinimized)
        self.titlebar.close.connect(self._on_close)
        # **这条不能漏。** 漏了的话深浅切换按钮会点不动：
        # 信号定义了（TitleBar.toggle_theme）、
        # 发射端也写了（btn_theme.clicked -> toggle_theme.emit），
        # 但**接收端从来没连上**，所以点一下什么都不发生。
        #
        # 之所以全套测试都没抓到：第 11 条是直接调 probe.toggle_theme()，
        # **绕过了点击路径**。信号断在哪里它就看不见。
        self.titlebar.toggle_theme.connect(self.toggle_theme)
        outer.addWidget(self.titlebar)

        body = QWidget()
        body.setObjectName("BodyArea")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # ---- 侧边栏 ----
        self.sidebar = Sidebar(self.t, body)
        self.sidebar.navigate.connect(self.switch_to)
        bl.addWidget(self.sidebar)

        # ---- 页面栈 ----
        self.stack = QStackedWidget(body)
        bl.addWidget(self.stack, 1)
        # **ALL_PAGES**：一级平铺的 + 每个二级菜单组里的。
        # stack 的顺序必须与 switch_to 的索引算法一致，所以这里和
        # 下面 [c.NAME for c in ALL_PAGES] 必须用同一个序列。
        for cls in ALL_PAGES:
            page = cls(self.dispatcher, self.t)
            self.pages[cls.NAME] = page
            self.stack.addWidget(page)

        outer.addWidget(body, 1)

        # ---- 无边框窗口的拖动 / 缩放 ----
        self._dragger = WindowDragger(self, self.titlebar)
        # 最大化按钮和双击标题栏必须走同一段逻辑，否则两处的
        # 还原行为会不一致（frameless 下 isMaximized() 与可见几何
        # 可能互相矛盾，判定要统一用 windowState()）
        self.titlebar.maximize.connect(self._dragger._toggle_maximize)

        # ---- 事件泵 ----
        self._timer = QTimer(self)
        self._timer.setInterval(_PUMP_MS)
        self._timer.timeout.connect(self._pump)
        self._timer.start()

    # ---------------------------------------------------------------- #
    def switch_to(self, name: str) -> None:
        page = self.pages.get(name)
        if page is None:
            return
        if self._current == name:
            return
        self._current = name
        idx = [c.NAME for c in ALL_PAGES].index(name)
        self.stack.setCurrentIndex(idx)
        self.sidebar.select(name)
        page.on_show()

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.t = T.apply(QApplication.instance(), self.theme_name)
        self.titlebar.t = self.t
        # RoundedRoot 自己是 QPainter 画的背景，QSS 管不到它，
        # 必须显式换色，否则切到深色后窗口圆角外仍是浅色
        self._round_root.retheme(self.t)
        self.sidebar.t = self.t
        # 主题按钮现在在标题栏上，图标要跟着当前主题走。
        # **两侧都要更新**：文字说的是「点下去会变成什么」，而
        # 图标只能由实际状态推导，初始化时也必须设对一次——
        # 只在 toggle_theme 里更新的话，首次打开是空图标。
        self.titlebar.btn_theme.set_dark(self.theme_name == "dark")
        # 窗口控制的图标是自绘的，QSS 的 color 管不到——必须逐个
        # 递新主题字典下去，否则切到深色后图标还是浅灰，在深色标题栏
        # 上几乎看不见。
        for b in (self.titlebar.btn_min, self.titlebar.btn_max,
                  self.titlebar.btn_close):
            b.set_theme(self.t)
        # 最大化/还原图标会随窗口状态变，两个按钮都可能带 _icon_color
        self.titlebar.sync_win_states()
        # 页面**不重建**。
        #
        # 早期版本这里调 _rebuild()，理由是「页面持有主题字典，整体
        # 重建最省心」。结果用户点一下主题切换就崩：
        #   RuntimeError: libshiboken: Internal C++ object
        #   (AboutPage) already deleted.
        #
        # 根因是 _rebuild() 自己：它先 old.deleteLater()，而
        # deleteLater() 是**延迟**删除——真正的 C++ 对象销毁要等到
        # 事件循环下一次转起来。它紧接着又 self.stack.addWidget()
        # 建新页面并 self.switch_to(cur)，此时栈里新旧混在一起，
        # 索引已经和 PAGES 顺序对不上；等到下一轮事件处理，延迟删除
        # 才真正生效，此时 AboutPage 的 C++ 对象已经没了。
        #
        # 而且没必要重建：整份 QSS 是应用级的（QApplication
        # .setStyleSheet），所有控件都会重绘，主题字典只被少数几个
        # 自绘组件（RoundedRoot、Console 背景、BrandMark）当作画笔
        # 颜色用——那些逐一 retheme 即可。顺带还省掉了「切一次主题
        # 丢一次当前页输入内容」的副作用。
        for page in self.pages.values():
            page.retheme(self.t)

    def changeEvent(self, ev) -> None:
        if ev.type() == QEvent.Type.WindowStateChange:
            self.titlebar.sync_win_states(self.isMaximized())

    # ---------------------------------------------------------------- #
    def _pump(self) -> None:
        if self._closing:
            return
        for kind, payload, task in self.dispatcher.drain():
            page = self.pages.get(task)
            if page is None:
                continue
            try:
                page.on_event(kind, payload)
            except Exception as e:                  # noqa: BLE001
                # 单页渲染失败不该拖垮整个 UI
                print(f"[aicbbuu] 页面 {task} 渲染失败: {e}", file=sys.stderr)

    def _on_close(self) -> None:
        self._closing = True
        self._timer.stop()
        self.dispatcher.cancel_all()
        # 光 cancel 只让探测循环跳出循环，**跑着的子进程还在**——
        # tracert.exe / ping.exe 会变成孤儿进程留在任务管理器里继续跑，
        # 而且重新打包时残留的 exe 会锁住输出文件（WinError 5）。
        # 这个信号会让 encoding.iter_lines 立刻 kill 掉它们。
        request_shutdown()
        QApplication.instance().quit()
