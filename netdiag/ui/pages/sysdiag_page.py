"""系统网络状态页（只读）。

把 Windows 自带的诊断命令汇总到一处：路由表、DNS 缓存、TCP 全局
参数、网卡与 MTU、防火墙、Winsock 目录、协议统计、WLAN 报告。

全部**只读**——跑多少遍都不会改动系统配置，最坏结果是某一项
读不到（组策略禁用、或需要管理员权限）。会改配置的修复操作在
另一个页面，两边物理分开。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

from ...core import netsys
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from ..widgets import hline, note


class SysDiagPage(Page):
    NAME = "sysdiag"
    TITLE = "系统网络状态"
    SUBTITLE = "路由表、DNS 缓存、TCP 参数、网卡 MTU、防火墙与 Winsock 目录（全部只读）"
    HAS_STATS = True
    HINT = "点「开始」采集系统信息，逐项结果会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("活动网卡", "默认网关", "拥塞控制", "路由条数",
                "DNS 缓存", "防火墙", "第三方 LSP", "成功读取")

    def _build_form(self, lay: QVBoxLayout) -> None:
        tip = note(
            "这一页只读取状态，不改动任何配置，随时可跑。\n\n"
            "**路由表**回答「流量会走哪个出口」——多条默认路由说明"
            "多网卡或 VPN 叠加，访问内网可能走错了出口。\n\n"
            "**网卡 MTU**回答「VPN 有没有把 MTU 改小」——MTU 被压低时"
            "小包正常、大包被静默丢弃，症状是 ping 一切正常但网页很慢。\n\n"
            "**Winsock 目录**里有第三方项，说明有软件往网络栈里插了"
            "组件。装完某软件后网络异常，优先怀疑它们。")
        # 说明文字走 note()：它默认开 RichText，**强调**才会渲染成粗体。
        # 直接用 QLabel 会把 ** 原样显示出来——v1.0.0 起就有的问题。
        lay.addWidget(tip)

        row = QVBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("WLAN 报告目录"))
        self.report_path = QLabel(self._report_dir())
        self.report_path.setObjectName("Mono")
        self.report_path.setWordWrap(True)
        self.report_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.report_path)
        lay.addLayout(row)
        lay.addWidget(hline(self.t))

    @staticmethod
    def _report_dir() -> str:
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return os.path.join(base, "Microsoft", "Wlansvc", "Reports")

    def task(self):
        return (netsys.sys_diag, "正在读取系统网络状态…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            for tile in self.tiles:
                if tile.key.text() == str(payload[0]):
                    tile.set_value(str(payload[1]))
                    return
            return
        if kind == KIND_LINE:
            line = str(payload)
            if line.startswith("✓"):
                self.console.write(line, "ok")
            elif line.startswith("⚠") or "⚠" in line:
                self.console.write(line, "warn")
            elif line.startswith("──") or line.startswith("═"):
                self.console.head(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
