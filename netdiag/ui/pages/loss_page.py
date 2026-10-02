"""持续丢包 / 抖动页。

和「延迟测试」页的区别：那一页测的是「现在有多快」，这一页测的是
「稳不稳」。视频会议卡顿、游戏跳ping 的元凶往往不是平均延迟高，
而是**偶发的丢包**——平均值好看得不得了，体验一样烂。

所以这一页的核心不是数字而是**形状**：第几次丢的、连着丢还是零散丢、
延迟起伏多大。折线图就是给这个用的。

**为什么自己用 QPainter 画而不引图表库**：项目对用户的承诺是
「不装任何第三方库、不联网、不上传数据」。为了画一条折线去装
pyqtgraph 或 matplotlib违背这个承诺，而且它们带来的依赖树
（numpy、matplotlib 一整套）比这个图表本身大两个数量级。这里
自绘反而更轻：零依赖。

踩过的坑，都写在对应代码里：
  · FieldRow 的控件属性是 ``.widget``，不是 ``.edit``
  · 说明文字要��� widgets.note()，直接 QLabel 会把 ** 原样显示
  · Console 的错误方法是 ``.err()``，不是 ``.error()``
"""
from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

from ...core import probes
from ...core.runner import KIND_ERROR, KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import Card, FieldRow, note


# ================================================================== #
#  折线图
# ================================================================== #
class RttChart(QWidget):
    """RTT 趋势 + 丢包标记。

    自己画的原因：不引第三方图表库。画法上有三处是刻意的——

    · **纵轴不从 0 开始**，而是从「最小值 - 15%」开始。否则一条
      1ms 上下波动的曲线会压在图底，看起来像一条直线，什么都看不出来。
      代价是必须明确标出坐标值，不能让人误读绝对大小。
    · **丢包点画成贯穿图高的红线**，而不是把曲线断开。断开的话，
      「连着丢 6 次」在图上就是一段空白，看不出是连续还是零散——
      而这恰恰是判断链路故障还是无线干扰的关键。
    · **均值线用虚线**，避免和实际曲线混为一谈。
    """

    #: 候选刻度步长。1/2/2.5/5 是一组「好读」的十进制步长，挑完之后
    #: 刻度值一定落在整数或 .5 上，不会出现 16.6667 ms。
    _STEPS = (0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 500, 1000)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: list[dict] = []
        self._avg: float | None = None
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setObjectName("RttChart")

    def set_data(self, samples: list[dict], avg: float | None) -> None:
        self._samples = list(samples or [])
        self._avg = avg
        self.update()

    def clear(self) -> None:
        self._samples = []
        self._avg = None
        self.update()

    # ---------------------------------------------------------------- #
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        c = T.get(None)
        w, h = self.width(), self.height()

        # 背景：极淡的竖向渐变，和整体风格一致
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(c["grad_top"]))
        grad.setColorAt(1.0, QColor(c["surface"]))
        p.fillRect(self.rect(), grad)

        # 图表内边距：左边给 Y 轴标签，下边给 X 轴标签
        pad_l, pad_r, pad_t, pad_b = 46, 14, 16, 26
        plot = QRectF(pad_l, pad_t, max(1.0, w - pad_l - pad_r),
                      max(1.0, h - pad_t - pad_b))

        if not self._samples:
            self._draw_empty(p, plot, c)
            p.end()
            return

        rtts = [s["rtt"] for s in self._samples
                if s.get("ok") and s.get("rtt") is not None]
        if not rtts:
            self._draw_all_lost(p, plot, c)
            p.end()
            return

        lo, hi = self._y_range(rtts)
        n = len(self._samples)

        def x_of(i: int) -> float:
            """第 i 个采样（0-based）的 X 坐标。"""
            if n <= 1:
                return plot.center().x()
            return plot.left() + plot.width() * i / (n - 1)

        def y_of(v: float) -> float:
            """RTT 值 -> Y 坐标（值越大越靠上）。"""
            if hi <= lo:
                return plot.center().y()
            return plot.bottom() - plot.height() * (v - lo) / (hi - lo)

        self._draw_grid(p, plot, y_of, lo, hi, c)
        self._draw_curve(p, plot, x_of, y_of, c)
        self._draw_loss_marks(p, plot, x_of, y_of, c)
        if self._avg is not None:
            self._draw_avg_line(p, plot, x_of, y_of, c)
        self._draw_axes(p, plot, x_of, y_of, n, c)
        p.end()

    # ---------------------------------------------------------------- #
    @staticmethod
    def _y_range(rtts: list[int]) -> tuple[float, float]:
        """Y 轴范围。

        下界压到「最小值 - 15% 余量」，上界抬到「最大值 + 15%」，
        再兜一个最小跨度 8ms——否则 1ms 和 2ms 的差别会被拉成满屏落差，
        看起来像剧烈抖动，其实只是取值范围太窄。
        """
        lo, hi = float(min(rtts)), float(max(rtts))
        # 先按 15% 留余量，再保证**最终跨度**不小于 8ms。
        #
        # 早先写成 ``span = max(hi - lo, 8.0); pad = span * 0.15``——
        # 那个 8ms 只加进了 pad 的计算基数，返回的仍是原始 lo/hi 加余量：
        # 数据 [1, 2] 时最终跨度只有 3.4ms，最小跨度等于没兜住。
        # 曲线会被拉成满屏落差，1ms 和 2ms 的正常波动看着像剧烈抖动。
        pad = max(hi - lo, 8.0) * 0.15
        lo -= pad
        hi += pad
        if hi - lo < 8.0:                 # 数据极窄时再撑开
            need = (8.0 - (hi - lo)) / 2.0
            lo -= need
            hi += need
        # **延迟不能是负数。** 对称撑开会把下界推到 0 以下——局域网
        # 延迟常常只有 1~2ms，此时纵轴变成 -2.5~5.5，标出「-1 ms」
        # 这种根本不存在的读数。下界触到 0 就把缺口全部加到上界。
        if lo < 0.0:
            hi -= lo
            lo = 0.0
        return lo, hi

    def _draw_empty(self, p: QPainter, plot: QRectF, c: dict) -> None:
        p.setPen(QColor(c["text_mute"]))
        f = QFont(self.font())
        f.setPointSize(10)
        p.setFont(f)
        p.drawText(plot, Qt.AlignmentFlag.AlignCenter,
                   "还没有数据 —— 点上方「开始」开始采样")

    def _draw_all_lost(self, p: QPainter, plot: QRectF, c: dict) -> None:
        p.setPen(QColor(c["err"]))
        f = QFont(self.font())
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(plot, Qt.AlignmentFlag.AlignCenter,
                   f"{len(self._samples)} 次采样全部无响应")

        p.setPen(QColor(c["text_dim"]))
        f.setBold(False)
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(QRectF(plot.left(), plot.center().y() + 14,
                          plot.width(), 20),
                   Qt.AlignmentFlag.AlignCenter,
                   "ICMP 被拦截时也会这样 —— 改用 TCP 握手模式再试一次")

    # ---------------------------------------------------------------- #
    @classmethod
    def _ticks(cls, lo: float, hi: float) -> list[float]:
        """挑 Y 轴刻度值。

        做法是「先要 4 条，再反推步长」：拿区间除以 4 得到目标步长，
        向上取整到最近的 _STEPS 成员。这样刻度条数稳定在 3~5 条。

        早先写成「步长 >= 区间/4 就取它」，方向反了——区间 15ms 时
        15/4=3.75，向上取到 5，实际只画得出 3 条；区间更窄时甚至
        只剩 1 条，曲线全挤在顶部、下半张图空着。
        """
        span = max(hi - lo, 1e-6)
        target = span / 4.0
        step = cls._STEPS[-1]
        for cand in cls._STEPS:
            if cand >= target:
                step = cand
                break
        v = math.ceil(lo / step) * step
        out: list[float] = []
        while v <= hi:
            out.append(round(float(v), 4))
            v += step
        return out

    def _draw_grid(self, p: QPainter, plot: QRectF, y_of,
                   lo: float, hi: float, c: dict) -> None:
        """网格 + Y 轴刻度。刻度取 3~5 条，取整到「好看」的步长。"""
        for v in self._ticks(lo, hi):
            y = y_of(v)
            # 网格线：比边框更淡，不抢曲线的视觉重量
            p.setPen(QPen(QColor(c["border_soft"]), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

            p.setPen(QColor(c["text_mute"]))
            f = QFont(self.font())
            f.setPointSize(8)
            p.setFont(f)
            fm = QFontMetrics(f)
            label = f"{v:g} ms"
            p.drawText(QPointF(plot.left() - fm.horizontalAdvance(label) - 6,
                               y + fm.ascent() / 2),
                       label)

    # ---------------------------------------------------------------- #
    def _draw_curve(self, p: QPainter, plot: QRectF, x_of, y_of, c: dict) -> None:
        """RTT 折线。丢包的点直接跳过（由红线标记），线会断开。"""
        pen = QPen(QColor(c["accent"]), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        path = QPainterPath()
        started = False
        for i, s in enumerate(self._samples):
            if not s.get("ok") or s.get("rtt") is None:
                started = False            # 断开，让丢包位置看得出来
                continue
            pt = QPointF(x_of(i), y_of(s["rtt"]))
            if not started:
                path.moveTo(pt)
                started = True
            else:
                path.lineTo(pt)
        p.drawPath(path)

        # 每个成功点画一个小圆点，让「一次一个包」更直观
        p.setBrush(QColor(c["accent"]))
        p.setPen(Qt.PenStyle.NoPen)
        for i, s in enumerate(self._samples):
            if not s.get("ok") or s.get("rtt") is None:
                continue
            p.drawEllipse(QPointF(x_of(i), y_of(s["rtt"])), 2.4, 2.4)

    # ---------------------------------------------------------------- #
    def _draw_loss_marks(self, p: QPainter, plot: QRectF, x_of, y_of,
                         c: dict) -> None:
        """丢包：从曲线位置往图顶画一条红色竖线。

        连续丢会形成一片竖线区域，一眼就能和零散的红线区分开——
        这正是「链路故障」和「无线干扰」的分界。
        """
        lost = [i for i, s in enumerate(self._samples) if not s.get("ok")]
        if not lost:
            return

        pen = QPen(QColor(c["err"]), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for i in lost:
            x = x_of(i)
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        # 丢包位置用小圆点标在图顶
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["err"]))
        for i in lost:
            p.drawEllipse(QPointF(x_of(i), plot.top() + 3), 2.6, 2.6)

    # ---------------------------------------------------------------- #
    def _draw_avg_line(self, p: QPainter, plot: QRectF, x_of, y_of,
                       c: dict) -> None:
        """均值虚线 + 右侧标注。"""
        y = y_of(self._avg)
        pen = QPen(QColor(c["ok"]), 1.0, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        # 标签右对齐到绘图区右沿内侧。早先写死 right-52，短标签
        # （avg 9）会飘到图外被裁掉，长标签又会盖住曲线。
        p.setPen(QColor(c["ok"]))
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        label = f"avg {self._avg:.0f} ms"
        fm = QFontMetrics(f)
        tx = plot.right() - fm.horizontalAdvance(label) - 2
        # 贴着顶/底时把标签挪到线的另一侧，避免出框
        ty = y - 4 if y - 4 > plot.top() + fm.height() else y + fm.height()
        p.drawText(QPointF(tx, ty), label)

    # ---------------------------------------------------------------- #
    def _draw_axes(self, p: QPainter, plot: QRectF, x_of, y_of,
                   n: int, c: dict) -> None:
        """X 轴：首尾和若干个等距采样序号。"""
        p.setPen(QColor(c["text_mute"]))
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        fm = QFontMetrics(f)

        # 边框只画左和下，开放上/右——减少视觉噪声
        p.setPen(QPen(QColor(c["border"]), 1.0))
        p.drawLine(QPointF(plot.left(), plot.top()),
                   QPointF(plot.left(), plot.bottom()))
        p.drawLine(QPointF(plot.left(), plot.bottom()),
                   QPointF(plot.right(), plot.bottom()))

        # 采样点很多时抽稀，最多标 7 个，避免标签互相压住。
        # **末尾必须显式补上**——range(0, n, step) 只取到 step 的倍数，
        # 最后一个采样点往往不在其中，图上就少一个标签，用户会以为
        # 采样次数比实际少。
        step = max(1, (n - 1) // 6) if n > 1 else 1
        marks = list(range(0, n, step))
        if n > 1 and marks[-1] != n - 1:
            marks.append(n - 1)

        p.setPen(QColor(c["text_mute"]))
        for i in marks:
            x = x_of(i)
            label = f"{i + 1}"
            wdt = fm.horizontalAdvance(label)
            if i == 0:
                lx = x
            elif i >= n - 1:
                lx = x - wdt
            else:
                lx = x - wdt / 2
            p.drawText(QPointF(lx, plot.bottom() + 13), label)


# ================================================================== #
#  页面
# ================================================================== #
class LossPage(Page):
    NAME = "loss"
    TITLE = "持续丢包"
    SUBTITLE = "连续采样定位丢在哪几次、成段还是零散、抖动有多大"
    HAS_STATS = True
    HINT = "点「开始」连续采样，图上红线就是丢掉的那些包"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("采样次数", "丢包率", "平均延迟", "抖动")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row1 = QHBoxLayout()
        row1.setSpacing(T.SPACE_MD)

        host_edit = QLineEdit("223.6.6.6")
        host_edit.setPlaceholderText("域名或 IP，例如 223.6.6.6")

        self.mode = QComboBox()
        self.mode.addItem("ICMP（ping）", "icmp")
        self.mode.addItem("TCP 握手", "tcp")
        self.mode.setToolTip(
            "ICMP 常被企业网 / 云主机在协议层拦截。\n"
            "如果网页能开但这里全丢，换成 TCP 握手再试。")

        self.count = QSpinBox()
        self.count.setRange(5, 200)
        self.count.setValue(30)
        self.count.setSuffix(" 次")
        self.count.setFixedWidth(96)

        self.interval = QComboBox()
        for label, val in (("0.2 秒", 0.2), ("0.5 秒", 0.5),
                           ("1 秒", 1.0), ("2 秒", 2.0)):
            self.interval.addItem(label, val)
        self.interval.setCurrentIndex(1)

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(443)
        self.port.setFixedWidth(88)
        self.port.setEnabled(False)         # ICMP 模式下不需要
        self.port_lbl = QLabel("端口")
        self.port_lbl.setEnabled(False)

        row1.addWidget(FieldRow("目标主机", host_edit), 1)
        row1.addWidget(FieldRow("方式", self.mode), 0)
        row1.addWidget(FieldRow("采样", self.count), 0)
        row1.addWidget(FieldRow("间隔", self.interval), 0)
        row1.addWidget(self.port_lbl)
        row1.addWidget(self.port)
        lay.addLayout(row1)

        self.host_edit = host_edit          # 直接存控件，不包 FieldRow

        # TCP 模式才需要端口：切换时一起开关
        self.mode.currentIndexChanged.connect(self._sync_port)
        self._sync_port()

        # 图表
        self.chart = RttChart()
        card = Card()
        head = QLabel("RTT 趋势（红线 = 丢包）")
        head.setObjectName("H2")
        card.add(head)
        card.add(self.chart, 1)
        lay.addWidget(card)
        # 不给 stretch：这一页的主角是输出区的逐次采样和结论，
        # 图表只要够看清形状就行。stretch=1 会让图表吃掉所有剩余高度，
        # 把结论挤到屏幕外（截图实测就是这样）。
        self.chart.setFixedHeight(212)

        lay.addWidget(note(
            "怎么看这张图\n"
            "· 蓝线平稳 + 零星几根红线 —— 无线干扰或接触不良，属常态\n"
            "· 蓝线断成一截截 + 红线连成一片 —— 链路或设备故障，"
            "重试没用，得换硬件\n"
            "· 蓝线上下剧烈起伏（看「抖动」统计块）—— 延迟不稳，"
            "实时应用会卡\n"
            "· 一根红线都没有，但你确实觉得卡 —— 换成 TCP 握手模式再试，"
            "可能 ICMP 被拦了而业务正常"))

    def _sync_port(self) -> None:
        tcp = self.mode.currentData() == "tcp"
        self.port.setEnabled(tcp)
        self.port_lbl.setEnabled(tcp)

    # ---------------------------------------------------------------- #
    def task(self):
        host = self.host_edit.text().strip()
        mode = self.mode.currentData()
        count = self.count.value()
        interval = float(self.interval.currentData())
        port = self.port.value()
        return (lambda post: probes.loss_probe(
                    post, host, count=count, interval=interval,
                    timeout_ms=1500, mode=mode, port=port),
                f"正在采样 {host}，{count} 次…")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
        for tile in self.tiles:
            if tile.key.text() == label:
                tile.set_value(value)
                return

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_ERROR:
            self.console.err(str(payload))
            self.chart.clear()
            for lbl in self._stat_keys():
                self._set_tile(lbl, "—")
            return
        if kind == KIND_STAT:
            d: dict[str, Any] = payload if isinstance(payload, dict) else {}
            self._set_tile("采样次数", f"{d.get('recv', 0)}/{d.get('sent', 0)}")
            loss = d.get("loss")
            self._set_tile("丢包率", f"{loss:.1f}%" if loss is not None else "—")
            avg = d.get("avg")
            self._set_tile("平均延迟",
                           f"{avg:.0f} ms" if avg is not None else "—")
            j = d.get("jitter")
            self._set_tile("抖动", f"{j:.1f} ms" if j is not None else "—")
            self.chart.set_data(d.get("samples") or [], avg)
            return
        if kind == KIND_LINE:
            line = str(payload)
            st = line.strip()
            # 逐次进度行不刷屏：图表已经在画了，控制台只留关键结论
            if " / " in st and ("ms" in st or "丢包" in st):
                return
            if st.startswith("✓"):
                self.console.write(line, "ok")
            elif st.startswith("✗"):
                self.console.err(line)
            elif st.startswith("⚠"):
                self.console.warn(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
