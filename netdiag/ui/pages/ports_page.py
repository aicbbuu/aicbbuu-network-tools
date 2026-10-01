"""端口检测页。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from ...core import probes
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow

#: 常用端口预设
PRESETS: dict[str, str] = {
    "常用": "22,80,443,3389,8080",
    "Web": "80,443,8080,8443",
    "数据库": "1433,1521,3306,5432,27017",
    "远程": "22,3389,5900,5985,445",
    "全端口": "21,22,23,25,53,80,110,143,443,445,993,995,1433,3306,3389,5432,8080",
}

DEFAULT_PORTS = "22,80,443,3389,8080"
MAX_PORTS = 256


class PortsPage(Page):
    NAME = "ports"
    TITLE = "端口检测"
    SUBTITLE = "TCP 连接探测，区分「拒绝」（端口未开）与「超时」（被防火墙丢弃）"
    HAS_STATS = True
    HINT = "点「开始」逐个探测端口，每个端口的开放情况会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("当前端口", "已扫描", "开放端口")

    def _build_form(self, lay: QVBoxLayout) -> None:
        # 扫描进度。_total 在 scan_begin 事件里才知道确切值
        self._done = 0
        self._open = 0
        self._total = 0
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.host = QLineEdit("127.0.0.1")
        self.host.setPlaceholderText("域名或 IP")
        self.host.setMinimumWidth(210)
        self.ports = QLineEdit(DEFAULT_PORTS)
        self.ports.setPlaceholderText("端口列表，支持 80,443 和 8000-8010")
        self.ports.setMinimumWidth(280)
        row.addWidget(FieldRow("目标主机", self.host))
        row.addWidget(FieldRow("端口", self.ports))
        lay.addLayout(row)

        # 预设快捷条。用 Chip 样式（矮一档、字号更小），
        # 和主操作按钮拉开视觉层级。
        pre = QHBoxLayout()
        pre.setSpacing(T.SPACE_XS)
        tip = QLabel("预设")
        tip.setObjectName("Dim")
        tip.setFixedWidth(34)
        tip.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pre.addWidget(tip)
        for name, value in PRESETS.items():
            btn = QPushButton(name)
            btn.setObjectName("Chip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, v=value: self.ports.setText(v))
            pre.addWidget(btn)
        pre.addStretch(1)
        lay.addSpacing(T.SPACE_XS)
        lay.addLayout(pre)

    def task(self):
        host = self.host.text().strip()
        if not host:
            raise ValueError("请填写目标主机")
        try:
            ports = probes.parse_ports(self.ports.text())
        except Exception as e:                     # noqa: BLE001
            raise ValueError(str(e)) from e
        if not ports:
            raise ValueError("端口列表为空")
        if len(ports) > MAX_PORTS:
            raise ValueError(f"一次最多 {MAX_PORTS} 个端口，当前 {len(ports)} 个")
        return (lambda post: probes.port_scan(post, host, ports),
                f"正在探测 {host} 的 {len(ports)} 个端口…")

    #: 端口状态 -> (中文标签, console 颜色 tag)
    _STATE = {
        "open": ("开放", "ok"),
        "closed": ("关闭", "warn"),
        "filtered": ("被过滤", "dim"),
        "error": ("出错", "err"),
    }

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            data = payload if isinstance(payload, dict) else {}
            what = data.get("kind")

            if what == "scan_begin":
                self._done = 0
                self._open = 0
                self._total = int(data.get("total") or 0)
                self.console.head(
                    f"目标 {data.get('host')}  →  {data.get('ip')}"
                    f"    共 {data.get('total')} 个端口")
                self.console.rule()
                return

            # 每扫完一个端口 core 都会发 {"kind": "port", ...}，
            # 必须逐条处理——若只认 scan_begin 就 return，整个页面
            # 一个结果都不显示，只留一行表头。
            if what == "port":
                self._render_port(data)
                return

            # 别把不认识的统计事件也一起丢掉：至少记一笔，便于发现
            # 避免新的协议不一致。
            self.console.info(f"[未处理的统计事件] {data}")
            return

        if kind == KIND_LINE:
            line = str(payload)
            if not line.strip():
                # core 用空串做心跳让 UI 逐行刷新，不要显示出来
                return
            low = line.lower()
            if "open" in low or "开放" in line:
                self.console.write(line, "ok")
            elif "refused" in low or "拒绝" in line:
                self.console.write(line, "warn")
            elif "timeout" in low or "超时" in line or "filtered" in low:
                self.console.write(line, "dim")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)

    def _render_port(self, data: dict) -> None:
        """渲染一个端口的探测结果。"""
        state = str(data.get("state", ""))
        label, tag = self._STATE.get(state, (state or "未知", "dim"))
        port = data.get("port", "?")
        name = data.get("label") or ""
        ms = data.get("ms")

        if ms is not None:
            when = f"{float(ms):7.1f} ms"
        else:
            when = "        —"
        # 只有用户填的是**服务名**（如 "http"）才显示它；直接填数字端口
        # 时 label 就是端口号本身，重复显示一遍纯属噪音
        if name and name != str(port):
            text = f"{_pad(str(port), 6)}{_pad(label, 8)}{when}  {name}"
        else:
            text = f"{_pad(str(port), 6)}{_pad(label, 8)}{when}"
        if state == "open":
            self.console.write(text, "ok")
        else:
            self.console.write(text, tag)

        self._done += 1
        if state == "open":
            self._open += 1
        self._update_tiles()

    def _update_tiles(self) -> None:
        if not self.tiles:
            return
        for tile in self.tiles:
            key = tile.key.text()
            if key == "已扫描":
                tile.set_value(f"{self._done} / {self._total}")
            elif key == "开放端口":
                tile.set_value(str(self._open))
            elif key == "当前端口":
                tile.set_value(str(self._done) if self._done else "—")


def _pad(s: str, n: int) -> str:
    """按显示宽度左对齐（汉字算两列）。"""
    w = sum(2 if ord(c) > 0x2000 else 1 for c in s)
    return s + " " * max(0, n - w)
