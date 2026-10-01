"""ARP 列表页。

单独一页而不是塞进「局域网」：两者的使用时机不同。局域网页是
排障入口（先看网关通不通），ARP 页是排查「IP 冲突」「谁在占这个
地址」「这个 MAC 是哪台设备」时的取证工具，需要的是一张完整、
稳定的表，而不是混在别的输出里。
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout

from ...core import probes_ext
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page


class ArpPage(Page):
    NAME = "arp"
    TITLE = "ARP 列表"
    HINT = "点「扫描」读取邻居表，本机学到的 MAC 与 IP 对应关系会显示在这里"
    SUBTITLE = "本机直接相连网段的邻居表（IP ↔ MAC 对应关系）"
    HAS_STATS = True

    def _stat_keys(self) -> tuple[str, ...]:
        return ("邻居总数",)

    def _build_form(self, lay: QVBoxLayout) -> None:
        tip = QLabel("ARP 只在本机直连的网段内有效，而且要有过实际通信"
                     "才会产生条目——ping 一下网关，表里就会有它。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

    def task(self):
        return (probes_ext.arp_list, "正在读取本机 ARP 邻居表…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            for tile in self.tiles:
                if tile.key.text() == str(payload[0]):
                    tile.set_value(str(payload[1]))
                    break
            return
        if kind == KIND_LINE:
            self.console.write(str(payload), "head"
                               if "─" in str(payload) else "dim")
            return
        super().on_event(kind, payload)
