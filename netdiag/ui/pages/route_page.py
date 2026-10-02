"""路由策略页（只读）。

和「系统网络状态」里那个路由表摘要的区别：那一页只列前 12 条、回答
「大致长什么样」；这一页列出**全部**活动路由，并回答「有没有问题」。

判断策略路由问题的依据是三条可查的事实，不猜：

  · 有几条默认路由——多条并存时只有跃点最小的真正生效
  · 默认路由的出口网卡是不是虚拟网卡——是的话说明 VPN / 加速器
    在接管流量，副作用是内网访问不到
  · 有没有默认路由——没有的话上不了任何外网

只读，不改任何路由。
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout

from ...core import netsys
from ...core.runner import KIND_ERROR, KIND_LINE, KIND_STAT
from .base import Page
from ..widgets import note
from .. import theme as T


class RoutePage(Page):
    NAME = "route"
    TITLE = "路由策略"
    SUBTITLE = "完整路由表 + 策略分析：默认路由、虚拟网卡接管、内网分段（全部只读）"
    HAS_STATS = True
    HINT = "点「开始」读取路由表并分析，多默认路由或 VPN 抢内网会明确指出"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("活动路由", "默认路由", "网卡数", "发现的问题")

    def _build_form(self, lay: QVBoxLayout) -> None:
        tip = note(
            "**跃点**决定同一目标有两条路时走哪条——数字越小越优先。\n\n"
            "**默认路由**（0.0.0.0/0）是「不知道往哪走时的默认出口」。"
            "有多条不代表出错，Windows 按跃点自动选第一条。\n\n"
            "**出口网卡带 [虚拟网卡]** 说明这个地址属于 VPN / 加速器 / "
            "虚拟机的虚拟接口。如果它拿到了默认路由，你的所有流量都会先进它，"
            "内网设备（打印机、NAS）可能就联不上了。")
        # 说明文字走 note()：它默认开 RichText，**强调**才会渲染成粗体。
        # 直接用 QLabel 会把 ** 原样显示出来——v1.0.0 起就有的问题。
        lay.addWidget(tip)

    def task(self):
        return (lambda post: netsys.route_report(post),
                "正在读取路由表并分析策略…")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
        for tile in self.tiles:
            if tile.key.text() == label:
                tile.set_value(value)
                return

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_ERROR:
            self.console.err(str(payload))
            for lbl in ("活动路由", "默认路由", "网卡数", "发现的问题"):
                self._set_tile(lbl, "—")
            return
        if kind == KIND_STAT:
            d = payload if isinstance(payload, dict) else {}
            self._set_tile("活动路由", str(d.get("total", "—")))
            self._set_tile("默认路由", str(d.get("default", "—")))
            self._set_tile("网卡数", str(d.get("ifaces", "—")))
            n = int(d.get("issues", 0))
            self._set_tile("发现的问题", str(n) if n else "无")
            return
        if kind == KIND_LINE:
            line = str(payload)
            if line.startswith("  ⚠"):
                self.console.warn(line)
            elif line.startswith("  ✗"):
                self.console.err(line)
            elif line.strip().startswith("路由表结构正常"):
                self.console.write(line, "ok")
            elif not line.strip():
                self.console.info(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
