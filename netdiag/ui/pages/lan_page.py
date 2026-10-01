"""局域网信息页。

默认网关 / DHCP / ARP 邻居，并实测网关连通性。
网关不通就能立刻判定「问题在本地，不用联系运营商」——这是本页
存在的最大理由。
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout

from ...core import probes_ext
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from ..widgets import hline


class LanPage(Page):
    NAME = "lan"
    TITLE = "局域网"
    SUBTITLE = "默认网关、DHCP 服务器、ARP 邻居表与网关连通性"
    HAS_STATS = True
    HINT = "点「扫描」扫描局域网，在线设备和它们的 IP 会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("默认网关", "DHCP 服务器", "ARP 邻居", "网关连通")

    def _build_form(self, lay: QVBoxLayout) -> None:
        tip = QLabel(
            "全部只读本机状态。\n"
            "先看「网关连通」：网关不通说明问题在本地（网线、网卡、"
            "IP 配置、路由器），不用联系运营商。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addWidget(hline(self.t))

    def task(self):
        return (probes_ext.lan_info, "正在读取局域网信息…")

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
            if line.startswith("✓"):
                self.console.write(line, "ok")
            elif "⚠" in line:
                self.console.write(line, "warn")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
