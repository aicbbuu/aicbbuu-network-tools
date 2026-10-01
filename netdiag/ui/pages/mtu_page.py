"""路径 MTU 探测页。

为什么单独一页：MTU 黑洞的典型症状是「ping 完全正常、网页也能
开，但加载很慢 / 传大文件卡死」。ping 只发小包，永远测不出
MTU 被压缩。这页用设 DF 位（不分片）的二分探测找出路径 MTU。
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from ...core import probes_ext
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class MtuPage(Page):
    NAME = "mtu"
    TITLE = "路径 MTU"
    SUBTITLE = "探测不分片包能通过的最大尺寸，定位 VPN / 隧道导致的 MTU 黑洞"
    HAS_STATS = True
    HINT = "点「开始」探测协商 MTU，接口值和实际生效值会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("路径 MTU", "首跳开销", "评价", "载荷上限")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.host = QLineEdit("www.baidu.com")
        self.host.setPlaceholderText("域名或 IP，例如 www.baidu.com")
        self.host.setMinimumWidth(280)
        row.addWidget(FieldRow("目标主机", self.host))
        row.addStretch(1)
        lay.addLayout(row)

        tip = QLabel("这里默认填外网地址：路径 MTU 探测的是出网卡那段链路，"
                     "打 localhost 不会离开本机，永远返回 1500，测不出真实值。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

    def task(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("请填写目标主机")
        return (lambda post: probes_ext.path_mtu(post, host),
                f"正在探测 {host} 的路径 MTU …")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
        """按标签文本填统计块。StatTile 的标签 QLabel 存在 .key 上。"""
        for tile in self.tiles:
            if tile.key.text() == label:
                tile.set_value(value)
                return

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            label, value = payload
            self._set_tile(str(label), str(value))
            if label == "路径 MTU":
                # 顺带算载荷上限：MTU = IP头20 + ICMP头8 + 载荷
                digits = "".join(ch for ch in str(value) if ch.isdigit())
                if digits:
                    self._set_tile("载荷上限", f"{int(digits) - 28} 字节")
            return
        if kind == KIND_LINE:
            line = str(payload)
            if "✓" in line:
                self.console.write(line, "ok")
            elif "✗" in line or "⚠" in line:
                self.console.write(line, "warn")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
