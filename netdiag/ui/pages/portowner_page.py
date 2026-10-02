"""端口占用页。

回答「这个端口谁占着」和「我能不能用这个端口」两个问题。

以前这两个问题只能靠用户自己开命令行跑 netstat、再对着 PID 去任务管理器
里一个个找。这里把三步并成一步：查端口 -> 找进程 -> 说清能不能处理。

**系统进程单独标记**。PID 4（System）这类进程用户既没权限结束，结束
了也会让 SMB、RPC 之类的系统功能失效。不标出来的话，用户会照着提示
去任务管理器里强杀，然后发现机器出问题了。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from ...core import probes
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow


class PortOwnerPage(Page):
    NAME = "portowner"
    TITLE = "端口占用"
    SUBTITLE = "查这个端口被谁占着，能不能用，怎么腾出来"
    HAS_STATS = True
    HINT = "点「开始」查询，结果和占用它的进程会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("占用进程", "监听条目", "查询端口", "判定")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)

        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setValue(8080)
        self.port.setSpecialValueText("全部")   # 0 = 不筛端口，列全部
        self.port.setFixedWidth(104)
        self.port.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        self.only_listen = QCheckBox("只看正在监听的")
        self.only_listen.setChecked(True)
        self.only_listen.setToolTip(
            "「正在监听」= 服务已启动、端口在等人来连。\n"
            "已建立的连接也会占着端口号，但不代表端口不能被别人绑定。")

        row.addWidget(FieldRow("端口", self.port, stretch=False))
        row.addWidget(self.only_listen, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        lay.addLayout(row)

        tip = QLabel(
            "端口填 0 表示列出本机全部正在监听的端口。"
            "输出里的 PID 可以直接在任务管理器的「详细信息」标签里定位。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

    def task(self):
        raw = self.port.value()
        port = None if raw == 0 else raw
        listen_only = self.only_listen.isChecked()
        if port is None:
            desc = "本机全部监听端口"
        else:
            desc = f"端口 {port}"
        return (lambda post: probes.port_owners(post, port, listen_only),
                f"正在查询 {desc}…")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
        for tile in self.tiles:
            if tile.key.text() == label:
                tile.set_value(value)
                return

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            data = payload if isinstance(payload, dict) else {}
            n = int(data.get("found", 0))
            port = data.get("port")
            self._set_tile("监听条目", str(n))
            self._set_tile("查询端口", str(port) if port else "全部")
            if n == 0:
                self._set_tile("占用进程", "—")
                self._set_tile("判定", "空闲" if port else "无")
            else:
                self._set_tile("占用进程", str(n))
                self._set_tile("判定", "被占用")
            return
        if kind == KIND_LINE:
            line = str(payload)
            low = line.lower()
            if "[系统]" in line or "不建议" in line:
                self.console.write(line, "warn")
            elif "没有任何进程监听" in line or "没有正在监听" in line:
                self.console.write(line, "ok")
            elif "地址已在使用" in line or "多个协议" in line:
                self.console.write(line, "warn")
            elif line.strip().startswith(("要腾出", "端口")) and "占用" in line:
                self.console.write(line, "warn")
            elif not line.strip():
                self.console.info(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)