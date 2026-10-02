"""故障自动归因页。

其余页面是工具箱——用户得先知道要测什么才去点。这一页反过来：
只给一句「网页打不开」，由工具自己按协议栈从底向上查，输出结论
和证据链，而不是丢一堆数据让用户自己看。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QVBoxLayout,
)

from ...core import diagnose
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from ..widgets import note
from .. import theme as T


class DiagnosePage(Page):
    NAME = "diagnose"
    TITLE = "故障诊断"
    SUBTITLE = "只说「网页打不开」？这里会告诉你问题出在哪一层"
    HAS_STATS = True
    HINT = "点「开始」按顺序跑一遍检测，逐项结论会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("诊断结论", "异常层数", "总耗时")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.deep = QCheckBox("深度诊断")
        self.deep.setChecked(True)
        self.deep.setToolTip("深度模式会 ping 网关与公网，证据更充分，"
                             "但要等 ICMP 超时。\n"
                             "网络已经很糟糕时取消勾选，"
                             "改用 TCP 判定，快一半。")
        row.addWidget(self.deep)
        row.addStretch(1)
        lay.addLayout(row)

        tip = note(
            "诊断按网络协议栈**从底向上**逐层进行，"
            "**第一个异常层就是根本原因**——\n"
            "后面的层失败只是它的表现，而不是另一个问题。\n\n"
            "    ① 本机配置  网卡 · MTU · DNS · 第三方网络组件\n"
            "    ② 网关连通  能 ping 通吗（不通就与运营商无关）\n"
            "    ③ 公网连通  出不去的话问题在上游\n"
            "    ④ DNS 解析  域名能不能变成 IP\n"
            "    ⑤ HTTPS     真实发起一次网页请求\n\n"
            "**只做只读检查，不修改任何设置。**")
        # 说明文字走 note()：它默认开 RichText，**强调**才会渲染成粗体。
        # 直接用 QLabel 会把 ** 原样显示出来——v1.0.0 起就有的问题。
        lay.addWidget(tip)

    def task(self):
        deep = self.deep.isChecked()
        return (lambda post: diagnose.diagnose(post, deep=deep),
                "正在逐层诊断网络…" if deep else
                "正在快速诊断（跳过 ping）…")

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
            if line.startswith("  ✓") or line.startswith("✓"):
                self.console.write(line, "ok")
            elif line.startswith("  ✗") or line.startswith("✗") \
                    or line.startswith("  ⚠") or line.startswith("⚠"):
                self.console.write(line, "warn")
            elif line.startswith("═") or line.startswith("证据链") \
                    or line.startswith("建议"):
                self.console.head(line)
            elif line.startswith("▸"):
                self.console.write(line, "accent")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
