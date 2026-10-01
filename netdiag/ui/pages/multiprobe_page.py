"""多目标并发探测页。

单目标探测的局限：说「网关 ping 不通」没用——可能是网关挂了，
也可能是去那个目标的路径断了。**一次测多个目标才能区分**：
个别不通是目标自己，全不通才是本地问题。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QSpinBox, QVBoxLayout,
)

from ...core import multiprobe
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow, polish_combo


class MultiProbePage(Page):
    NAME = "multiprobe"
    TITLE = "多目标对比"
    SUBTITLE = "一次测多个目标，用对比区分「目标自己的问题」和「本地的问题」"
    HAS_STATS = True
    HINT = "点「开始」并发探测所有目标，逐个目标的耗时和成败会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("目标数", "探测方式", "可达", "总耗时")

    def _build_form(self, lay: QVBoxLayout) -> None:
        # ---- 预置场景 ----
        row0 = QHBoxLayout()
        row0.setSpacing(T.SPACE_MD)
        self.preset = polish_combo(QComboBox())
        for key, label, desc, _fn, _mode in multiprobe.PRESETS:
            self.preset.addItem(f"{label}　—　{desc}", key)
        self.preset.currentIndexChanged.connect(self._load_preset)
        row0.addWidget(FieldRow("预置", self.preset), 1)
        lay.addLayout(row0)

        # ---- 目标列表 ----
        self.targets = QPlainTextEdit()
        self.targets.setPlaceholderText(
            "每行一个目标，也可用空格或逗号分隔。\n"
            "支持域名和 IP。\n\n"
            "例：\n"
            "8.8.8.8\n"
            "1.1.1.1  223.5.5.5")
        self.targets.setMaximumHeight(110)
        self.targets.setObjectName("Mono")
        lay.addWidget(FieldRow("目标", self.targets))

        # ---- 探测方式与并发 ----
        row1 = QHBoxLayout()
        row1.setSpacing(T.SPACE_MD)

        self.use_ping = QCheckBox("用 ping（测主机在不在）")
        self.use_ping.setToolTip(
            "ping 问的是「这台机器在不在」。\n"
            "适合验证网关、本机、其他设备。\n"
            "不适合测网站——很多网站屏蔽 ICMP。")
        row1.addWidget(self.use_ping)

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(443)
        self.port.setPrefix("端口 ")
        self.port.setToolTip("TCP 模式要连的端口。网站一般 443，"
                             "远程桌面 3389，SSH 22。")
        row1.addWidget(self.port)

        self.workers = QSpinBox()
        self.workers.setRange(1, multiprobe._MAX_WORKERS)
        self.workers.setValue(multiprobe._DEFAULT_WORKERS)
        self.workers.setPrefix("并发 ")
        self.workers.setToolTip(
            "默认 12。调太高会触发系统限流，\n"
            "结果变成「全都连不上」——比不测更误导。\n"
            "所以这里最多 32，不再往上开放。")
        row1.addWidget(self.workers)
        row1.addStretch(1)
        lay.addLayout(row1)

        tip = QLabel(
            "**这一页的价值在「对比」，不在单个结果。**\n\n"
            "    4 个里 3 个通   →  本地网络没问题，是那一个目标自己"
            "的问题\n"
            "    4 个全不通     →  问题在本地或链路，先跑「故障诊断」\n"
            "    只有本机通     →  出不去，看网关与上游\n\n"
            "**并发数有上限是刻意的。** 几百个线程一起建连会触发系统"
            "限流，\n"
            "结果变成「全都连不上」，比不测更误导。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self._load_preset()

    def _load_preset(self) -> None:
        key = self.preset.currentData()
        if not key:
            return
        self.targets.setPlainText(
            "\n".join(multiprobe.preset_targets(key)))
        # 预置自带推荐方式：分层定位问「在不在」要用 ping，
        # 网站可达性问「能不能连」要用 TCP 443
        self.use_ping.setChecked(multiprobe.preset_mode(key) == "ping")

    def task(self):
        text = self.targets.toPlainText()
        targets = multiprobe.parse_targets(text)
        if not targets:
            raise ValueError("请至少填写一个目标")
        if len(targets) > 64:
            raise ValueError(f"目标有 {len(targets)} 个，"
                             f"一次最多测 64 个")
        mode = "ping" if self.use_ping.isChecked() else "tcp"
        port = self.port.value()
        workers = self.workers.value()
        what = "ping" if mode == "ping" else f"TCP {port}"
        return (lambda post: multiprobe.multi_probe(
                    post, targets, mode=mode, port=port, workers=workers),
                f"正在用{what}并发探测 {len(targets)} 个目标…")

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
            if line.startswith("✓") or line.startswith("  ✓ "):
                self.console.write(line, "ok")
            elif line.startswith("✗") or line.startswith("⚠") \
                    or line.startswith("  ✗ "):
                self.console.write(line, "warn")
            elif line.startswith("═") or line.startswith("▸") \
                    or line.startswith("状态"):
                self.console.head(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
