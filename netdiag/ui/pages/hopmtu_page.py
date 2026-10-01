"""MTU 逐跳定位页。

现有的 MTU 页只告诉你「终点路径 MTU 是多少」，但要修就得知道
**是哪一跳卡的**。这一页追踪路径后对每一跳做二分探测，输出 MTU
曲线，并从**下降沿**推出瓶颈位置和该找谁修。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
)

from ...core import hopmtu
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class HopMtuPage(Page):
    NAME = "hopmtu"
    TITLE = "MTU 逐跳"
    SUBTITLE = "定位是哪一段链路压低了 MTU，告诉你该找谁修"
    HAS_STATS = True
    HINT = "点「开始」逐跳改小包探测，能通过的最大包长会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("路径跳数", "应答跳", "路径最小 MTU", "降 MTU 位置", "评价")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.host = QLineEdit("8.8.8.8")
        self.host.setPlaceholderText("目标主机或 IP")
        self.host.setMinimumWidth(220)
        row.addWidget(FieldRow("目标", self.host))

        self.short = QCheckBox("只看前 6 跳")
        self.short.setChecked(True)
        self.short.setToolTip("只追前 6 跳能快 3 倍。\n"
                              "MTU 瓶颈通常在前几跳，\n"
                              "要全路径就取消勾选。")
        row.addWidget(self.short)
        lay.addLayout(row)

        tip = QLabel(
            "**「路径 MTU 变小」还不够，得知道在哪一段变小。**\n\n"
            "    「延迟测试」页的 MTU 只测终点，"
            "这里对路径上**每一跳**都做二分探测。\n\n"
            "关注的是 **MTU 的下降沿** —— 某一跳的 MTU 突然变小，"
            "说明**上一段链路**被压低了，\n"
            "是那一段的设备干的。\n\n"
            "下降点在私有地址段（192.168.x / 10.x / 172.16-31.x）"
            "就是你自己的路由器；\n"
            "在公网段则是运营商的 PPPoE 或隧道封装。\n\n"
            "**无响应的跳测不出 MTU**（设备不回复 ICMP），"
            "这类跳会单独列出。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

    def task(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("请填写目标主机，例如 8.8.8.8")
        hops = 6 if self.short.isChecked() else 15
        return (lambda post: hopmtu.hop_mtu_scan(post, host, max_hops=hops),
                f"正在逐跳探测 {host} 的 MTU（最多 {hops} 跳）…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            if isinstance(payload, dict):
                return
            for tile in self.tiles:
                if tile.key.text() == str(payload[0]):
                    tile.set_value(str(payload[1]))
                    return
            return
        if kind == KIND_LINE:
            line = str(payload)
            if line.startswith("✓") or "MTU 1500" in line:
                self.console.write(line, "ok")
            elif line.startswith("✗") or "⚠" in line or "下降" in line:
                self.console.write(line, "warn")
            elif line.startswith("═") or line.startswith("▸") \
                    or line.startswith("逐跳结果"):
                self.console.head(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
