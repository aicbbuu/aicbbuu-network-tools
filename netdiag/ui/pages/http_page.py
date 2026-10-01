"""HTTP 可用性检测页。

为什么单独一页：ping 只测 ICMP，而「ping 得通但网页打不开」是
国内网络最常见的故障形态。DNS 污染、443 被阻断、TLS 被劫持
三种情况在 ping 看来完全一样（都是 RTT 正常）。这个页面把
DNS -> TCP -> TLS/HTTP 三层拆开计时，直接指出卡在哪一层。
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout

from ...core import probes_ext
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class HttpPage(Page):
    NAME = "http"
    TITLE = "HTTP 检测"
    SUBTITLE = "分层验证能不能上网：DNS 解析 → TCP 连接 → TLS/HTTP"
    HAS_STATS = True
    HINT = "点「开始」发送 HTTP 请求，状态码、响应头和耗时会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("DNS 解析", "TCP 握手", "HTTP 响应", "总耗时")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.host = QLineEdit("")
        self.host.setPlaceholderText("留空则自动测三个站点；也可填域名或 IP")
        self.host.setMinimumWidth(280)
        row.addWidget(FieldRow("目标", self.host))
        row.addStretch(1)
        lay.addLayout(row)

    def task(self):
        host = self.host.text().strip()
        return (lambda post: probes_ext.http_check(post, host),
                f"正在检测 {host or '内置三个站点'} …")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
        """按标签文本填统计块。StatTile 的标签 QLabel 存在 .key 上。"""
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
            if line.startswith("  ✓"):
                self.console.write(line, "ok")
            elif line.startswith("  ") and "ms" in line:
                self.console.info(line)
            elif line.startswith("──") or not line.strip():
                self.console.info(line)
            elif line.startswith("    合计"):
                self.console.write(line, "ok")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
