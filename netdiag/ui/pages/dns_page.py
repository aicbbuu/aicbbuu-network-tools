"""DNS 解析页。"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout

from ...core import probes
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class DnsPage(Page):
    NAME = "dns"
    TITLE = "DNS 解析"
    SUBTITLE = "查询域名对应的 IP 地址，并显示解析耗时与 DNS 服务器"
    HAS_STATS = True
    HINT = "点「解析」查询，响应记录、TTL 和耗时会在下方显示"

    def _stat_keys(self) -> tuple[str, ...]:
        # 只放 core 真的发得出的字段。「DNS 服务器」不可用——core
        # 从来不发这个键，于是那一格永远是「—」，而页面看起来完全正常。
        # 宁可少一格，不要给用户一个永远空的框。
        return ("解析耗时", "返回地址")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.host = QLineEdit("localhost")
        self.host.setPlaceholderText("要解析的域名，例如 www.baidu.com")
        self.host.setMinimumWidth(280)
        row.addWidget(FieldRow("域名", self.host))
        row.addStretch(1)
        lay.addLayout(row)

    def task(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("请填写域名")
        return (lambda post: probes.dns_lookup(post, host),
                f"正在解析 {host} …")

    def _set_tile(self, label: str, value: str) -> None:
        for tile in self.tiles:
            if tile.key.text() == label:
                tile.set_value(value)
                return

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            # core 精心算了 elapsed_ms（注释写着「反映的是本机到 DNS
            # 服务器的往返时间，比 nslookup 的整体输出更有参考价值」），
            # 但这一页没有 KIND_STAT 分支，字典被基类静默丢弃——副标题
            # 承诺的「解析耗时」在界面上根本不存在。
            data = payload if isinstance(payload, dict) else {}
            if isinstance(payload, tuple) and len(payload) == 2:
                self._set_tile(str(payload[0]), str(payload[1]))
            else:
                # core 发的是 dict：{"elapsed_ms", "addresses", "count", ...}
                if "elapsed_ms" in data:
                    self._set_tile("解析耗时", f"{float(data['elapsed_ms']):.0f} ms")
                if "count" in data:
                    self._set_tile(
                        "返回地址",
                        f"{data['count']} 个" if data.get("count") else "无")
            return
        if kind == KIND_LINE:
            line = str(payload)
            low = line.lower()
            if "ip" in low and any(c.isdigit() for c in line):
                self.console.write(line, "ok")
            elif "fail" in low or "error" in low or "timed out" in low:
                self.console.write(line, "err")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
