"""延迟测试页。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLineEdit, QSpinBox, QVBoxLayout,
)

from ...core import probes
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class PingPage(Page):
    NAME = "ping"
    TITLE = "延迟测试"
    SUBTITLE = "测量到目标主机的往返延迟与丢包率，用于判断链路质量"
    HAS_STATS = True
    HINT = ("点「开始」逐次测延迟，丢包率、时延和统计会显示在这里。ICMP 被封时改用 TCP 握手测，结果更接近真实上网体验。")

    def _stat_keys(self) -> tuple[str, ...]:
        return ("平均", "最低", "最高", "丢包")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)

        self.host = QLineEdit("163.com")
        self.host.setPlaceholderText("域名或 IP，例如 163.com")
        self.host.setMinimumWidth(240)

        # ICMP 和 TCP 两种测法。默认给 ICMP（ping）——它是「链路通不通」
        # 的基准判据；被封时用户自己切到 TCP，不用两个页面。
        self.mode = QComboBox()
        self.mode.addItem("ICMP（ping）", "icmp")
        self.mode.addItem("TCP 握手（tcping）", "tcp")
        self.mode.setFixedWidth(168)
        self.mode.setToolTip(
            "ICMP 常被公司网络、VPN、游戏主机在协议层拦截，\n"
            "此时 ping 不通但网页能开是正常的。\n"
            "TCP 握手走真实业务路径，被封时也能测出延迟。")

        # 端口只在 TCP 模式下用得上
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(443)
        self.port.setFixedWidth(88)
        self.port.setAlignment(Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter)
        self.port.setVisible(False)

        self.count = QSpinBox()
        self.count.setRange(1, 20)
        self.count.setValue(4)
        self.count.setSuffix(" 次")
        self.count.setFixedWidth(92)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight
                            | Qt.AlignmentFlag.AlignVCenter)

        # 切到 TCP 才显示端口，否则那个输入框没有意义
        self.mode.currentIndexChanged.connect(self._sync_port)
        self._port_row: QHBoxLayout | None = None

        row.addWidget(FieldRow("目标主机", self.host))
        row.addWidget(FieldRow("方式", self.mode, stretch=False))
        row.addWidget(self.port)
        row.addStretch(1)
        row.addWidget(FieldRow("次数", self.count, stretch=False))
        lay.addLayout(row)

    def _sync_port(self) -> None:
        self.port.setVisible(self.mode.currentData() == "tcp")

    def task(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("请填写目标主机")
        count = self.count.value()
        if self.mode.currentData() == "tcp":
            port = self.port.value()
            return (lambda post: probes.tcping(post, host, port, count),
                    f"正在 TCP 握手测试 {host}:{port}（{count} 次）…")
        return (lambda post: probes.ping(post, host, count),
                f"正在测试 {host}（{count} 次）…")

    # ---------------------------------------------------------------- #
    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            self._fill_stats(payload)
            return
        if kind == KIND_LINE:
            line = str(payload)
            low = line.lower()
            # 判据顺序要紧：每条回复行都含 "TTL="（中英文实测确认），
            # 若把 "ttl" 判在最前，下面的绿色 ok 分支将**永远不可达**
            # ——ping 的正常回复行全被当成普通信息行显示。
            if ("time" in low and "=" in low) or "时间" in line:
                self.console.write(line, "ok")
            elif ("unreachable" in low or "unreachable" in low
                  or "不可达" in line or "目标主机无法访问" in line):
                self.console.write(line, "warn")
            elif "ttl" in low or "ttl" in line:
                self.console.info(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)

    def _fill_stats(self, stat) -> None:
        data = stat if isinstance(stat, dict) else {}
        # core 专门为「4 次全超时」发了 all_failed=True，README 也承诺
        # 「工具会提示这一点」——因此这一页必须读取该字段。
        # 迁移到 Qt 时又丢了。
        #
        # 这条提示对非技术用户极其重要：公司网络、VPN、游戏主机会在协议
        # 层拦掉 ping，ping 不通完全正常。不知道的话，用户会直接认定
        # 「我家网络坏了」，然后去折腾路由器和运营商。
        if data.get("all_failed"):
            if self.mode.currentData() == "tcp":
                # TCP 全失败的原因和 ICMP 完全不同：ICMP 是「被协议层
                # 拦截」，TCP 是「端口没开 / 被防火墙拦」。照抄 ICMP 那句
                # 会把用户引到错误的方向。
                self.console.warn(
                    f"{self.count.value()} 次都没能完成 TCP 握手。"
                    "常见原因：该端口没开放，或被防火墙拦截。")
                self.console.info(
                    "换一个常用端口再试（比如 443、80），"
                    "或先用「端口检测」页确认这个端口通不通。")
            else:
                self.console.warn(
                    "4 次全部超时。这不一定代表网络故障——公司网络、VPN、"
                    "游戏主机都会在协议层拦截 ping。")
                self.console.info(
                    "要判断能不能正常上网，请用「HTTP 检测」页试一次，"
                    "那才是真正的判据。")
                self.console.info(
                    "也可以把上面的「方式」切成 TCP 握手，"
                    "绕开 ICMP 拦截直接测延迟。")
        # 单位必须按 **key** 判断，不能按值的类型。core 的 min/max 是
        # int() 转出来的、avg 是除法结果 float，所以按 isinstance 判断的
        # 后果是四个块里两个没单位——「最低 11」「最高 40」，用户得
        # 自己猜这是毫秒还是微秒。
        for tile, key in zip(self.tiles, ("avg", "min", "max", "loss")):
            val = data.get(key)
            if val is None:
                tile.set_value("—")
            elif key == "loss":
                tile.set_value(f"{float(val):.0f}%")
            else:
                tile.set_value(f"{float(val):.1f} ms")
