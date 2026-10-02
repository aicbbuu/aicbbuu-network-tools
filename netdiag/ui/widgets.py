"""
通用组件。

与常规 Qt 项目的最大差别：**圆角和阴影由 QSS 与
QGraphicsDropShadowEffect 负责**，不再需要用 Canvas 手绘几何。
所以这里的类都很薄——大部分只是组装布局 + 挂样式。

所有类都接受 ``theme`` 字典并从中取色，但**不自己保存主题**：
主题切换时由 App 统一重建界面，组件只管当下长什么样。
"""
from __future__ import annotations

import re

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from . import theme as T


# ---------------------------------------------------------------- #
#  阴影
# ---------------------------------------------------------------- #
def add_shadow(w: QWidget, blur: int = 28, dy: int = 6, alpha: int | None = None,
               color: str | None = None) -> QWidget:
    """给控件加柔和阴影。

    Qt 的阴影是真实的高斯模糊扩散，不是画一圈灰色边框那种假阴影。
    代价：QGraphicsEffect 不支持带圆角的子控件叠加多个效果，
    所以每层阴影只能挂一个——所以别给每个子控件都加。

    ``color`` 传主题里的 shadow 色（形如 "#0b1220"）。以前这里是
    写死的 ``QColor(15, 23, 42, a)``，而阴影颜色**从不随主题变化**：
    深色下需要更黑更重的阴影，写死的深蓝灰在深底上几乎看不见，
    卡片和背景糊在一起分不出层次。
    """
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    a = alpha if alpha is not None else 26
    base = QColor(color or "#0f172a")
    eff.setColor(QColor(base.red(), base.green(), base.blue(), a))
    w.setGraphicsEffect(eff)
    return w


# ---------------------------------------------------------------- #
#  卡片
# ---------------------------------------------------------------- #
class Card(QFrame):
    """带圆角和阴影的内容容器。"""

    def __init__(self, parent=None, theme: dict[str, Any] | None = None,
                 padded: bool = True, shadow: bool = True):
        super().__init__(parent)
        self.setObjectName("Card")
        t = theme or T.LIGHT
        self._t = t
        self._pad = padded
        self._want_shadow = shadow
        if shadow:
            add_shadow(self, blur=30, dy=6,
                       alpha=t.get("shadow_a", 26),
                       color=t.get("shadow", "#0f172a"))
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(
            T.SPACE_LG if padded else 0, T.SPACE_LG if padded else 0,
            T.SPACE_LG if padded else 0, T.SPACE_LG if padded else 0)
        self._body.setSpacing(T.SPACE_MD)

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self._body.addWidget(w, stretch)
        return w

    def add_layout(self, lay) -> None:
        self._body.addLayout(lay)

    def body(self) -> QVBoxLayout:
        return self._body

    def retheme(self, theme: dict[str, Any]) -> None:
        """切主题时重设阴影颜色。

        Card 必须实现 retheme，否则 Page.retheme 的
        findChildren 遍历会跳过它，阴影将固定为浅色主题的参数。
        背景靠 QSS 能跟着换，阴影是 QGraphicsEffect 的属性，只能
        手动重建。
        """
        self._t = theme
        if not self._want_shadow:
            return
        self.setGraphicsEffect(None)          # 必须先摘掉才能重设
        add_shadow(self, blur=30, dy=6,
                   alpha=theme.get("shadow_a", 26),
                   color=theme.get("shadow", "#0f172a"))


# ---------------------------------------------------------------- #
#  统计块
# ---------------------------------------------------------------- #
class StatTile(QFrame):
    """一个数值 + 标签的小方块。"""

    def __init__(self, key: str, theme: dict[str, Any] | None = None,
                 parent=None):
        super().__init__(parent)
        t = theme or T.LIGHT
        # 刻意用 objectName + 全局 QSS，而不是内联 setStyleSheet：
        # 内联样式是**冻结**的，切换主题时不会跟着变。页面已经不
        # 重建了（见 App.toggle_theme），统计块必须靠 retheme()
        # 里的 setProperty + 样式刷新才能换色。
        self.setObjectName("StatTile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE_MD, T.SPACE_SM + 2,
                               T.SPACE_MD, T.SPACE_SM + 2)
        lay.setSpacing(1)

        self.value = QLabel("—")
        self.value.setObjectName("StatVal")
        self.value.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.Bold))
        self.key = QLabel(key)
        self.key.setObjectName("StatKey")
        lay.addWidget(self.value)
        lay.addWidget(self.key)

    def set_value(self, text: str) -> None:
        """刻意不叫 set —— 会和 QWidget 的方法混淆。"""
        self.value.setText(text)

    def set_key(self, text: str) -> None:
        """改标签。

        WiFi 页有两个子页（当前连接 / 网络扫描），两边要显示**完全
        不同**的四项指标。以前靠 ``_set_tile`` 用标签文本匹配去填，
        结果扫描页的「可见网络 / 最强信号 / 开放网络 / 加密情况」
        一个都匹配不上，四个块永远显示「—」——而页面看起来完全
        正常，没有任何报错。

        这是「按名字找块」这种设计的必然结果：core 改个标签名，
        界面就静默失效。切标签页时改名的方式比按名字找更可靠。
        """
        self.key.setText(text)

    def retheme(self, theme: dict[str, Any]) -> None:
        """切主题时通知 QSS 重新读取属性。

        这里只需要清掉**可能内联**的样式再让 QSS 重新接管。背景
        已经在 QSS 里按 #StatTile 声明，所以严格说什么都不用做——
        但保留这个方法是刻意的：一旦将来有人给 StatTile 加了
        内联 setStyleSheet，Page.retheme 会因为找不到 retheme 而
        静默跳过换色，又是一次「切主题后某个角落颜色不对」。
        """
        self.setStyleSheet("")


# ---------------------------------------------------------------- #
#  按钮
# ---------------------------------------------------------------- #
class PrimaryButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Primary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        # 点击后不要键盘焦点——否则按钮上的字会带一圈虚线框（Qt 画的
        # 焦点指示器）。QSS 里有 outline: none
        # 兜底，但那是纯样式层的做法；这里从控件层面直接不给焦点，
        # 双保险（QSS 规则被 objectName 选择器覆盖时会失效）。
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class GhostButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


# ---------------------------------------------------------------- #
#  表单行
# ---------------------------------------------------------------- #
class FieldRow(QWidget):
    """标签 + 控件的横排。标签固定宽度，保证各行控件左对齐。"""

    LABEL_W = 66

    def __init__(self, label: str, widget: QWidget,
                 parent=None, stretch: bool = True):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE_MD)
        lbl = QLabel(label)
        lbl.setObjectName("Dim")
        lbl.setFixedWidth(self.LABEL_W)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(lbl)
        lay.addWidget(widget, 1 if stretch else 0)
        self.widget = widget


# ---------------------------------------------------------------- #
#  说明文字
# ---------------------------------------------------------------- #
def note(text: str, *, parent=None) -> QLabel:
    """页面顶部的灰色说明文字。

    **会自动把 Markdown 的 ``**强调**`` 转成 HTML 粗体。**

    QLabel 的默认文本格式是 AutoText：Qt 看到不像 HTML 的开头就按纯文本
    显示，于是 ``**会原样显示成一串裸露的星号``。这个 bug 从 v1.0.0 就有
    ——已发布的故障诊断页上能看到「诊断按网络协议栈**从底向上**逐层进行」
    这样带星号的文案，多目标对比、系统网络状态、网卡健康等页也一样。

    注意 ``setTextFormat(RichText)`` 本身**不够**：RichText 走的是
    **HTML** 解析器，而 ``**`` 是 Markdown 语法、不是 HTML 标签，HTML
    解析器不认它。所以必须先把 ``**xxx**`` 换成 ``<b>xxx</b>``。

    所以凡是用 ``**`` 写说明文字的地方，都必须走这个工厂，不能直接
    ``QLabel("...**...**...")``。
    """
    lbl = QLabel(_md_bold(text), parent)
    lbl.setObjectName("Dim")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setWordWrap(True)
    return lbl


#: ``**xxx**`` -> ``<b>xxx</b>``。非配对的 ``**``（比如代码示例里单独
#: 出现的一对）原样保留——宁可少强调，也不要输出一堆裸星号。
_RE_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _md_bold(text: str) -> str:
    """把 Markdown 粗体标记换成 HTML 粗体标签。

    先转义 ``&`` / ``<`` / ``>`` 再插标签，否则原文里本来就有尖括号
    （比如 ``<512``）会被 HTML 解析器当成标签，界面上的文字会莫名其妙
    少一截。
    """
    out = (text.replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;"))
    return _RE_MD_BOLD.sub(r"<b>\1</b>", out)


# ---------------------------------------------------------------- #
#  输出面板
# ---------------------------------------------------------------- #
# 各页面在构造 Console 时可以传的提示语。放在这里而不是各页面自己
# 写字面量，是为了文案风格统一（都用「点…执行，…」这个句式）。
HINT_BY_ROLE: dict[str, str] = {
    "generic": "点「开始」执行，输出会显示在这里",
    "scan": "点「开始」扫描，扫描过程和结果会显示在这里",
    "ping": "点「开始」测试，每个目标的逐包结果会显示在这里",
    "trace": "点「开始」追踪，每一跳的探测结果会显示在这里",
    "mtu": "点「开始」探测，每档 MTU 的结果会显示在这里",
}

class Console(QPlainTextEdit):
    """只读等宽输出区，带语义着色。

    用 QTextCharFormat 逐段着色，
    同样零依赖。QPlainTextEdit 只支持纯文本，性能比 QTextEdit 好，
    但仍然支持逐字符格式（通过 QTextCursor 插入）。
    """

    MAX_LINES = 5000
    MAX_CHARS = 400_000

    def __init__(self, parent=None, theme: dict[str, Any] | None = None,
                 hint: str | None = None):
        super().__init__(parent)
        t = theme or T.LIGHT
        self._t = t
        self.setObjectName("Console")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # 空状态提示。**用 Qt 原生的 placeholder，不是真的 append
        # 一行文字**——那样清空后提示会残留，而且会被
        # QTextCharFormat 染成正文色，看起来像真实输出。原生
        # placeholder 是灰的、只要有任何内容写入就自动消失，
        # 正好是我们要的语义。
        self.setPlaceholderText(
            hint or HINT_BY_ROLE["generic"])

        # QPlainTextEdit 内部有一个 viewport 子控件，QSS 的 background
        # 只作用在控件本体上。viewport 保持默认浅色，视觉上就是
        # 「深色框里一块白底」——必须显式一起设。
        vp = self.viewport()
        vp.setObjectName("ConsoleViewport")
        vp.setAutoFillBackground(True)

        f = QFont("Consolas", 10)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(f)

        # 配色缓存
        self._fmt = self._build_fmt(t)

    def _build_fmt(self, t: dict[str, Any]) -> dict[str, QTextCharFormat]:
        return {
            "ok":     self._fmt_color(t["ok"]),
            "err":    self._fmt_color(t["err"]),
            "warn":   self._fmt_color(t["warn"]),
            "dim":    self._fmt_color(t["console_dim"]),
            "accent": self._fmt_color(t["accent_hi"], bold=True),
            "head":   self._fmt_color(t["console_fg"], bold=True),
        }

    def retheme(self, theme: dict[str, Any]) -> None:
        """换主题后重算配色。

        Console 的背景/文字色由 QSS 管（QSS 换了立刻生效），但**正文
        的语义色是 QTextCharFormat，在 __init__ 时就烤死了**——不重算
        的话，切到深色后已有的那些行还留着浅色主题的墨色，在深底上
        可能糊掉。

        已渲染的旧行保持原样（QTextCharFormat 是逐段的，改起来要重建
        全文，没那个必要）；新写入的行用新配色。
        """
        self._t = theme
        self._fmt = self._build_fmt(theme)

    def _fmt_color(self, hexstr: str, bold: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(hexstr))
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        return f

    def _append(self, text: str, tag: str) -> None:
        """在末尾追加一段带格式的文本。"""
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        fmt = self._fmt.get(tag, self._fmt["head"])
        cur.insertText(text, fmt)
        self.setTextCursor(cur)
        # 每插入一段就检查一次上限。放在插入**之后**是有意的：先删再插
        # 会让用户看到内容闪烁，而超出量最多就是刚加的这一段。
        self.trim()

    # ---------------- 公开接口 ----------------
    def write(self, text: str = "", tag: str = "head") -> None:
        self._append(text + "\n", tag)

    def info(self, text: str) -> None:
        self._append(text + "\n", "dim")

    def ok(self, text: str) -> None:
        self._append(text + "\n", "ok")

    def warn(self, text: str) -> None:
        self._append(text + "\n", "warn")

    def err(self, text: str) -> None:
        self._append(text + "\n", "err")

    def head(self, text: str) -> None:
        self._append(text + "\n", "head")

    def rule(self, ch: str = "─") -> None:
        self._append(ch * 60 + "\n", "dim")

    def clear_all(self) -> None:
        """清空全部内容。

        刻意**不叫 clear**：QPlainTextEdit 已有 clear()，
        覆写成 `def clear(self): self.clear()` 就是无限递归，
        而且栈里全是同一行，排查极其费时间。
        """
        super().clear()

    def replace_last(self, text: str, tag: str = "head") -> None:
        """替换最后一行（测速进度刷新用）。

        Qt 文档结构：QTextDocument 末尾**永远有一个 text=="" 的空块**。
        写入 "A\nB\n" 后块是 ["A", "B", ""]，真正要刷新的最后一行
        是从尾块往前第一个有内容的块。

        两个必须注意的点（都是实测踩出来的）：

        1. 选区是半开区间 [start, start + length)。四种组合实测：
             [s, s+len-1) + "X\n"  -> 多出一个空行
             [s, s+len-1) + "X"    -> 行尾换行被吃掉
             [s, s+len)   + "X"    -> 和下一行粘在一起
             [s, s+len)   + "X\n"  -> 正确

        2. 插入文本会让 QTextBlock 迭代器失效，所以必须先把
           start/length 存成整数再用，不能拿着 block 对象跨越
           insertText 调用。

        实测：连续刷新 39 次，行数恒定不变。
        """
        doc = self.document()
        blk = doc.lastBlock()
        while blk.isValid() and not blk.text():
            blk = blk.previous()
        if not blk.isValid():                 # 文档确实为空
            self._append(text + "\n", tag)
            return

        start = blk.position()                # 存成 int，脱离 block 引用
        length = blk.length()

        cur = self.textCursor()
        cur.setPosition(start)
        cur.setPosition(start + length, cur.MoveMode.KeepAnchor)
        cur.insertText(text + "\n", self._fmt.get(tag, self._fmt["head"]))
        # replace_last 不增加行数（替换最后一行），但文档末尾那个
        # 隐含空块可能被吞掉，trim 里会补回来
        self.trim()

    def trim(self) -> None:
        """行数超上限时丢弃最旧的内容。

        刻意**从来没被调用过**——定义了但零调用点，所以 MAX_LINES=5000
        完全是摆设。路由追踪（30 跳 × 3 次探测）或长跑测速能让输出区
        无限增长，QTextDocument 每插入一个字符都要重新布局，内存和
        CPU 一起涨，用户会感到「越用越卡」。现在在 _append 里自动调。

        按**行**删而不是按字符位置删：取 MAX_CHARS 个字符再删，
        一次删掉的行数不确定（可能一行都删不掉，因为 MAX_CHARS 落在
        某行中间时就只删了半行），于是既不保证上限生效，删完还会留下
        半个残行。这里显式算出要删多少个 block。
        """
        excess = self.blockCount() - self.MAX_LINES
        if excess <= 0:
            return
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.Start)
        for _ in range(excess):
            cur.movePosition(cur.MoveOperation.NextBlock)
        # 删到「要删的最后一块的开头」为止，保留它及其后的内容
        cur.movePosition(cur.MoveOperation.Start, cur.MoveMode.KeepAnchor)
        cur.removeSelectedText()
        # 文档永远保留一个末尾空块；刚才的删除可能把它也吃掉，
        # 补一个空行，否则 QTextDocument 会拒绝后续插入。
        if self.blockCount() == 0:
            self._append("\n", "head")


# ---------------------------------------------------------------- #
#  其他小组件
# ---------------------------------------------------------------- #
def hline(theme: dict[str, Any] | None = None) -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return f


def vspace(h: int) -> QWidget:
    w = QWidget()
    w.setFixedHeight(h)
    return w


def hspace(w: int) -> QWidget:
    x = QWidget()
    x.setFixedWidth(w)
    return x


def state_dot(theme: dict[str, Any], color: str | None = None) -> QLabel:
    """状态小圆点。"""
    c = color or theme["text_mute"]
    lbl = QLabel("●")
    lbl.setStyleSheet(f"color: {c}; font-size: 9px; background: transparent;")
    return lbl

class ComboItemDelegate(QStyledItemDelegate):
    """让下拉列表每一项的高亮块**四周内缩**，而不是铺满整行。

    **必须完全自己画，不能调 ``super().paint()``。**

    踩过的三道坑，每一道都得靠实测像素才发现：

    ① QSS 里 ``QComboBox QAbstractItemView::item`` 上的 ``padding``
       不生效——Qt 的 item 是 delegate 画的，padding 落在内容矩形
       **之外**，直接被裁掉。
    ② view 自己的 ``padding: 5px`` 只让整个列表内缩，
       ``visualRect()`` 仍返回铺满宽度的矩形（实测 row0 宽 218px，
       正好等于 viewport 宽）。
    ③ **改 ``option.rect`` 再调 ``super().paint()`` 也不行**——
       实测高亮块照样铺满。原因是默认绘制最终走
       ``QStyle.renderControl(CE_ItemViewItem)``，它自己再算一遍
       绘制矩形，还会叠上 QSS 的 ``::item:selected`` 规则，
       把我们内缩的那块盖掉。

    所以：背景（含高亮与悬停）全部自己画圆角矩形，文字自己用
    ``QTextLayout`` 画。**完全不碰 ``super().paint()``**，让 QSS 没有
    机会再画一遍。

    代价是 QSS 的 ``::item:selected`` / ``::item:hover`` / ``color``
    / ``font-weight`` 对下拉列表全部失效——颜色和字重在这里自己取，
    主题字典一改，切主题时调 ``retint`` 即可（见 polish_combo）。
    """

    MARGIN_X = 5
    MARGIN_Y = 1
    RADIUS = 6.0
    PAD_LEFT = 10          # 高亮块左边到文字起笔的距离

    def __init__(self, theme: dict[str, Any], parent=None):
        super().__init__(parent)
        self._t = theme

    def paint(self, painter, option, index) -> None:
        from PySide6.QtCore import Qt, QRect, QPointF
        # QPainter 必须在列表里。漏了它不会在import 时报错——只在
        # paint() 真正执行到那一行才抛NameError，而 paint() 只在
        # **用户点开下拉框**时才被调用。所以自动化测试全绿、
        # 用户一用就崩。（我第一版清理未用导入时把它一起删了。）
        from PySide6.QtGui import (QColor, QFont, QPainter, QTextLayout,
                                   QTextOption)

        t = self._t
        full = option.rect
        st = option.state

        sel = bool(st & QStyle.StateFlag.State_Selected)
        hov = bool(st & QStyle.StateFlag.State_MouseOver)
        dis = not bool(st & QStyle.StateFlag.State_Enabled)

        # ---- 底色 ----
        if sel:
            bg = t["accent_soft"]
            fg = t["accent_dim"] if t["name"] == "light" else t["accent_hi"]
            # PySide6 里枚举在 QFont.Weight 上，不在 QFont.FontWeight
            # （后者是 C++ 侧的写法，Python 访问会 AttributeError）
            font_w = QFont.Weight.DemiBold
        elif hov:
            bg = t["hover"]
            fg = t["text"]
            font_w = QFont.Weight.Normal
        else:
            bg = None
            fg = t["text_mute"] if dis else t["text"]
            font_w = QFont.Weight.Normal

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if bg is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(
                full.adjusted(self.MARGIN_X, self.MARGIN_Y,
                              -self.MARGIN_X, -self.MARGIN_Y),
                self.RADIUS, self.RADIUS)

        # ---- 文字 ----
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            font = option.font
            font.setWeight(font_w)
            painter.setFont(font)
            painter.setPen(QColor(fg))
            layout = QTextLayout(str(text), font)
            fmt = QTextOption()
            fmt.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
            layout.setTextOption(fmt)
            layout.beginLayout()
            line = layout.createLine()
            if line.isValid():
                # 垂直居中：先算整块高度，再顶到项的中线
                lh = line.height()
                y = full.y() + (full.height() - lh) / 2.0
                line.setPosition(QPointF(self.PAD_LEFT, y))
                layout.endLayout()
                painter.translate(0, 0)
                layout.draw(painter, QPointF(0, 0))
        painter.restore()



def polish_combo(combo: QComboBox,
                 theme: dict[str, Any] | None = None) -> QComboBox:
    """给下拉框装上「高亮块内缩」的 delegate，返回同一个 combo。

    挂一个 ``retint(t)`` 方法上去，**切主题时要调它**——delegate 是
    Python 对象，颜色只能从主题字典取，而 QSS 管不到它。忘了调，深色
    主题下选中项就是一块浅蓝，在深底上非常刺眼。

    （QSS 的 ``::item:selected`` 规则这时是失效的：我们已经把
    ``State_Selected`` 从 option 里摘掉，默认绘制不会去查那条规则。）
    """
    t = theme or T.LIGHT
    dele = ComboItemDelegate(t, combo)
    combo.setItemDelegate(dele)
    combo.view().setSpacing(0)
    combo.retint = lambda nt: setattr(dele, "_t", nt)

    return combo
