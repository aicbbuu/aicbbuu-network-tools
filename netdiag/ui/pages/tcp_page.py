"""TCP 握手耗时分解页。

单看 connect() 的总耗时无法区分「延迟本来就高」和「握手丢包重传」：
一个 200ms 的连接，可能是 RTT 本身就是 200ms（正常），也可能是
SYN 丢了一次触发重传（异常）。做法是**建连成功后再连一次**当对照
——第二次连接时 TCP 握手已由内核缓存，耗时接近纯 RTT；两者之差
就是握手阶段的额外开销。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
)

from ...core import probes_ext
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class TcpPage(Page):
    NAME = "tcp"
    TITLE = "TCP 握手"
    SUBTITLE = "把建连耗时拆开，区分「延迟高」与「握手丢包重传」"
    HAS_STATS = True
    HINT = "点「开始」测试连通性，握手结果、时延和失败原因会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("成功轮次", "握手续费", "参考 RTT", "判定")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.host = QLineEdit("163.com")
        self.host.setPlaceholderText("域名，例如 www.baidu.com")
        self.host.setMinimumWidth(260)
        self.port = QLineEdit("443")
        self.port.setPlaceholderText("端口")
        self.port.setFixedWidth(84)
        row.addWidget(FieldRow("目标主机", self.host))
        row.addWidget(FieldRow("端口", self.port, stretch=False))
        row.addStretch(1)
        lay.addLayout(row)

        tip = QLabel(
            "「对照」= 建连成功后立即再连一次，此时握手已被内核缓存，"
            "耗时接近纯 RTT。建连耗时减去对照值，就是握手阶段的额外开销。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

    def task(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("请填写目标主机")
        try:
            port = int(self.port.text().strip() or "443")
        except ValueError:
            raise ValueError("端口必须是数字") from None
        if not 1 <= port <= 65535:
            raise ValueError("端口需在 1-65535 之间")
        return (lambda post: probes_ext.tcp_timing(post, host, port),
                f"正在分解 {host}:{port} 的握手耗时…")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
        for tile in self.tiles:
            if tile.key.text() == label:
                tile.set_value(value)
                return

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            self._set_tile(str(payload[0]), str(payload[1]))
            return
        if kind == KIND_LINE:
            line = str(payload)
            if "重传" in line or "⚠" in line:
                self.console.write(line, "warn")
            elif "连接失败" in line:
                self.console.write(line, "err")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
