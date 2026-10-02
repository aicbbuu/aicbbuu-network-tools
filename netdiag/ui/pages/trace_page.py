"""路由追踪页。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from ...core import probes
from ...core.runner import KIND_LINE
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class TracePage(Page):
    NAME = "trace"
    TITLE = "路由追踪"
    SUBTITLE = "逐跳显示数据包经过的路由器，定位链路在哪一段出问题"
    HINT = "点「开始」追踪，每一跳的地址、时延和丢包率会显示在这里"

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)

        self.host = QLineEdit("163.com")
        self.host.setPlaceholderText("域名或 IP")
        self.host.setMinimumWidth(260)
        self.hops = QSpinBox()
        self.hops.setRange(1, 64)
        self.hops.setValue(30)
        self.hops.setSuffix(" 跳")
        self.hops.setFixedWidth(96)
        self.hops.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(FieldRow("目标主机", self.host))
        row.addStretch(1)
        row.addWidget(FieldRow("最大跳数", self.hops, stretch=False))
        lay.addLayout(row)

        tip = QLabel("这里默认填外网地址：路由追踪打 localhost 只会 1 跳就"
                     "结束，看不出中间经过了哪些路由器。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

    def task(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("请填写目标主机")
        hops = self.hops.value()
        return (lambda post: probes.traceroute(post, host, hops),
                f"正在追踪到 {host} 的路径…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_LINE:
            line = str(payload)
            low = line.lower()
            if "time out" in low or "超时" in line or "*" == line.strip():
                self.console.write(line, "warn")
            elif "unreachable" in low:
                self.console.write(line, "err")
            else:
                self.console.write(line, "head" if line[:1].isdigit() else "dim")
            return
        super().on_event(kind, payload)
