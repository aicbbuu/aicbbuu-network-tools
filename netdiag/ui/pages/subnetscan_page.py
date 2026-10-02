"""子网 IP 使用情况扫描页。

三层证据交叉判断「哪些地址真的被占用」。**ping 不通不等于空闲**
——Windows 防火墙默认拦截入站 ICMP，很多设备因此 ping 不通却
照样占着地址。给已占用的地址分配新设备会造成 IP 冲突，所以这一页
刻意把「确定在用」和「不确定」分开显示。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
)

from ...core import subnetscan
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow, note


class SubnetScanPage(Page):
    NAME = "subnetscan"
    TITLE = "子网扫描"
    SUBTITLE = "ARP / TCP / ICMP 三层交叉判断哪些 IP 真的被占用"
    HAS_STATS = True
    HINT = "点「开始」扫描地址段，每个 IP 的存活情况会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("扫描地址", "确定在用", "高概率在用", "仅 ICMP 响应", "扫描耗时")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.cidr = QLineEdit("192.168.1.0/24")
        self.cidr.setPlaceholderText("网段，如 192.168.1.0/24")
        self.cidr.setMinimumWidth(240)
        row.addWidget(FieldRow("网段", self.cidr))

        self.use_tcp = QCheckBox("扫 TCP 端口")
        self.use_tcp.setChecked(True)
        self.use_tcp.setToolTip("并发试 9 个常用端口（445/135/139/3389/22/80/443\n"
                                "与两个 iOS 特征端口）。关掉能快一倍，\n"
                                "但会漏掉屏蔽了 ping 的设备。")
        self.use_icmp = QCheckBox("扫 ICMP")
        self.use_icmp.setChecked(True)
        self.use_icmp.setToolTip("对剩下的地址各 ping 一次。\n"
                                 "只用来补充参考——ping 不通不代表空闲。")
        row.addWidget(self.use_tcp)
        row.addWidget(self.use_icmp)
        lay.addLayout(row)

        tip = note(
            "**「ping 通」不等于「有人在用」**，反过来也一样。四种常见情况：\n"
            "  · 设备待机        → ping 通，但没人用（IP 仍被占）\n"
            "  · Windows 防火墙  → ping 不通，但**确实有人在用**\n"
            "  · Linux / 网络设备 → 默认禁 ping，但有人在用\n"
            "  · DHCP 刚续约     → ping 不通，但地址已被保留\n\n"
            "所以本页分三层取证：ARP 表是**确定**证据（设备必须回 ARP "
            "才能通信），TCP 端口是**高概率**证据（445 开着基本确定是 "
            "Windows），ICMP 只作补充。\n\n"
            "只支持本机直连的子网。跨网段 ARP 看不到，判断会明显变慢且"
            "准确度下降。")
        # 说明文字走 note()：它把 **强调** 转成 HTML 粗体。
        # 直接用 QLabel 会把 ** 原样显示成一串裸露的星号。
        lay.addWidget(tip)

    def task(self):
        cidr = self.cidr.text().strip()
        if not cidr:
            raise ValueError("请填写网段，例如 192.168.1.0/24")
        # 先本地校验一遍，别让用户等 5 秒才看到格式错误
        try:
            start, total = subnetscan.parse_cidr_list(cidr)
        except ValueError as e:
            raise ValueError(f"网段格式有误：{e}") from e
        if total == 0:
            raise ValueError("这个网段没有可分配的主机地址")
        want_tcp = self.use_tcp.isChecked()
        want_icmp = self.use_icmp.isChecked()
        if not want_tcp and not want_icmp:
            raise ValueError("TCP 与 ICMP 至少要选一个，否则扫不出任何结果")
        return (lambda post: subnetscan.scan_subnet(
                    post, cidr, want_tcp=want_tcp, want_icmp=want_icmp),
                f"正在扫描 {cidr}（{total} 个地址）…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            if isinstance(payload, dict):
                # scan_begin / scan_progress / scan_end 供 UI 做进度显示，
                # 统计格与表格都由 core 用文本事件输出，这里不重复处理
                return
            for tile in self.tiles:
                if tile.key.text() == str(payload[0]):
                    tile.set_value(str(payload[1]))
                    return
            return
        if kind == KIND_LINE:
            line = str(payload)
            if line.startswith("✓"):
                self.console.write(line, "ok")
            elif "⚠" in line or line.startswith("○") or line.startswith("◆"):
                self.console.write(line, "warn")
            elif line.startswith("═") or line.startswith("状态"):
                self.console.head(line)
            elif line.startswith("●") or line.startswith("◐"):
                self.console.write(line, "ok" if line.startswith("●")
                                   else "dim")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
