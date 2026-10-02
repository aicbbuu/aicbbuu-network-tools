"""网卡与协议栈健康度页（只读）。

回答「网卡是不是坏了」。关键在于把三种病因分开，因为处理方式完全不同：

  · 硬件 / 驱动层——媒体状态断开、驱动异常。得换驱动或换网卡。
  · 配置层——静态 IP 填错、没有默认网关。改设置就行。
  · 协议栈层——装卸软件在网络栈里留了第三方组件。得卸干净。

混在一起报「网络有问题」，用户就只能瞎试。这里每一条都指明是哪一层。
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout

from ...core import netsys
from ...core.netsys import _STACK_CORE_DLLS as _STACK_CORE
from ...core.runner import KIND_ERROR, KIND_LINE, KIND_STAT
from .base import Page
from ..widgets import note


class NicHealthPage(Page):
    NAME = "nichealth"
    TITLE = "网卡健康"
    SUBTITLE = "网卡状态、MTU、TCP 参数、协议栈组件（全部只读）"
    HAS_STATS = True
    HINT = "点「开始」检查网卡与协议栈，逐层定位问题出在哪一层"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("网卡数", "MTU 正常", "协议栈组件", "第三方组件")

    def _build_form(self, lay: QVBoxLayout) -> None:
        tip = note(
            "**三层分开看**：\n"
            "· **硬件 / 驱动层**——媒体状态、驱动版本。对症是装驱动或换网卡。\n"
            "· **配置层**——IP、掩码、网关、DHCP。填错就改，Windows 本身没问题。\n"
            "· **协议栈层**——装卸软件往网络栈里插的组件。装完软件网络就坏，"
            "先看这一层。\n\n"
            "**MTU 变小是个隐形坑**：VPN 装完 MTU 可能从 1500 变 1400，"
            "小包正常、大包被静默丢弃——表现为 ping 一切正常但网页很慢。")
        # 说明文字走 note()：它默认开 RichText，**强调**才会渲染成粗体。
        # 直接用 QLabel 会把 ** 原样显示出来——v1.0.0 起就有的问题。
        lay.addWidget(tip)

    def task(self):
        return (lambda post: netsys.nic_health(post),
                "正在检查网卡与协议栈…")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
        for tile in self.tiles:
            if tile.key.text() == label:
                tile.set_value(value)
                return

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_ERROR:
            self.console.err(str(payload))
            for lbl in self._stat_keys():
                self._set_tile(lbl, "—")
            return
        if kind == KIND_STAT:
            d = payload if isinstance(payload, dict) else {}
            self._set_tile("网卡数", str(d.get("nics", "—")))
            self._set_tile("MTU 正常", str(d.get("mtu_ok", "—")))
            # 协议栈组件显示成「2 / 2」：分母是内置的必备组件数，分子是
            # 实际在 Winsock 目录里找到的数。「2」单独一个数字看不出好坏。
            found = int(d.get("stack", 0) or 0)
            self._set_tile("协议栈组件", f"{found} / {len(_STACK_CORE)}")
            n3 = int(d.get("third", 0) or 0)
            self._set_tile("第三方组件", str(n3) if n3 else "无")
            return
        if kind == KIND_LINE:
            line = str(payload)
            # 「✗」开头是异常，「✓」是正常，中间的 · 是中性信息
            st = line.strip()
            if st.startswith("✗"):
                self.console.err(line)
            elif st.startswith("✓"):
                self.console.write(line, "ok")
            elif not st or st.startswith("═══") or st.startswith("──"):
                self.console.info(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
