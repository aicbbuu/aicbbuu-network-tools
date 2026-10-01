"""网络信息页。

只做一件事：展示本机网络配置的完整快照。WiFi 质量、局域网、
TCP 握手各有独立页面（挤在这一页既不好用也不好找），这里保持纯粹。
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout

from ...core import probes
from ...core.runner import KIND_LINE
from .base import Page
from ..widgets import hline


class NetInfoPage(Page):
    NAME = "net"
    TITLE = "网络信息"
    SUBTITLE = "本机网卡、IP、DNS、代理与当前 TCP 连接"
    HINT = "点「刷新」读取网卡、路由和 DNS 的当前配置"

    def _build_form(self, lay: QVBoxLayout) -> None:
        tip = QLabel("点击「开始测试」采集本机网络配置。信息只在本机读取，"
                     "不会上传到任何地方。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addWidget(hline(self.t))

    def task(self):
        return (probes.network_info, "正在采集网络信息…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_LINE:
            self.console.info(str(payload))
            return
        super().on_event(kind, payload)
