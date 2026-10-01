"""带宽测速页。

源可选择：公共测速源经常挂（限流、机房故障、被墙），自动降级能
保证「总能测出一个数」，但降级后的数字和用户选的源对不上。所以给
一个下拉框让用户自己定，「自动」为默认。
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout

from ...core import probes
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T
from ..widgets import FieldRow, polish_combo

AUTO = "自动（失败自动换源）"


class SpeedPage(Page):
    NAME = "speed"
    TITLE = "带宽测速"
    SUBTITLE = "多源下载测速，结果受网络拥塞影响，仅供参考"
    HAS_STATS = True
    HINT = "点「开始」按选定时长测速，实时速率和最终结果会显示在这里"

    def _stat_keys(self) -> tuple[str, ...]:
        return ("下载速度", "已下载", "耗时", "来源")

    def _build_form(self, lay: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.src = polish_combo(QComboBox())
        self.src.addItem(AUTO)
        for name, _url, _n in probes.SPEED_SOURCES:
            self.src.addItem(name)
        self.src.setMinimumWidth(230)
        row.addWidget(FieldRow("测速源", self.src))

        # 时长可选。**原来固定 30 MB 收工是准不准的根源**：
        # 千兆链路上 30 MB 只要 0.24 秒，TCP 慢启动还没收敛，
        # 算出来明显偏低；慢链路上又要干等几十秒。定流量两头不讨好。
        # 按时长才对——跑够几秒，慢启动必然收敛，与链路过快无关。
        self.dur = polish_combo(QComboBox())
        for sec in probes.TIME_CHOICES:
            self.dur.addItem(f"{sec} 秒", sec)
        default = probes.TIME_CHOICES.index(probes._DEFAULT_SECONDS) \
            if probes._DEFAULT_SECONDS in probes.TIME_CHOICES else 1
        self.dur.setCurrentIndex(default)
        self.dur.setMinimumWidth(110)
        row.addWidget(FieldRow("测速时长", self.dur))
        row.addStretch(1)
        lay.addLayout(row)

        tip = QLabel(
            "测速会占用流量，用量随你选的时长和带宽而定"
            "（10 Mbps 跑 10 秒约 12 MB，100 Mbps 则约 125 MB）。"
            "时长越短结果波动越大，10 秒以上更稳。\n"
            "选「自动」时按顺序试源，哪个通用哪个；选具体源则只测那一个，"
            "失败直接报错而不换源——换出来的速度和用户选的源对不上，"
            "没有意义。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

    def task(self):
        choice = self.src.currentText()
        only = None if choice == AUTO else choice
        seconds = self.dur.currentData()
        return (lambda post: probes.bandwidth_test(post, only, seconds),
                f"正在测速（约 {seconds} 秒），请保持网络空闲…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            self._fill(payload)
            return
        if kind == KIND_LINE:
            line = str(payload)
            if "测速源" in line or "测试源" in line:
                self.console.head(line)
            elif "失败" in line or "失败" in line:
                self.console.write(line, "err")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)

    def _fill(self, stat) -> None:
        """把 core 的统计事件填到统计块和进度条。

        协议必须和 probes.bandwidth_test 对齐：core 发的是
        ``kind`` 为 ``speed_progress`` / ``speed_done``，字段是
        ``bytes`` / ``elapsed`` / ``mbps``，而 mbps 是比特每秒。

        早期这里判的是 ``progress`` / ``done``，读的是 ``mb`` / ``secs``，
        还把 ``mbps`` 当 MB/s 显示——三处都对不上。结果测速页从头到尾
        没显示过一行进度，统计块永远是「—」，真跑通了数字还差 8 倍。
        """
        if not isinstance(stat, dict):
            return
        kind = stat.get("kind")
        if kind == "speed_progress":
            mbps = stat.get("mbps", 0.0)              # 比特/秒
            mb = stat.get("bytes", 0) / 1048576
            secs = stat.get("elapsed", 0.0)
            bar_w = 24
            # 以 1000 Mbps 为满格：家宽通常 10-80，1 GbE 上限约 1000
            filled = min(bar_w, max(1, int(mbps / 1000 * bar_w)))
            bar = "█" * filled + "░" * (bar_w - filled)
            self.console.replace_last(
                f"  {bar}  {mbps / 8:6.2f} MB/s  ({mbps:6.1f} Mbps)"
                f"   {mb:6.1f} MB   {secs:4.1f}s", "accent")
        elif kind == "speed_done":
            mbps = stat.get("mbps", 0.0)
            mb = stat.get("bytes", 0) / 1048576
            secs = stat.get("elapsed", 0.0)
            self.tiles[0].set_value(f"{mbps / 8:.2f} MB/s")
            self.tiles[1].set_value(f"{mb:.1f} MB")
            self.tiles[2].set_value(f"{secs:.1f} s")
            self.tiles[3].set_value(str(stat.get("source", "—")))
            self.console.write("")
            self.console.ok(f"测速完成：{mbps / 8:.2f} MB/s（{mbps:.1f} Mbps）"
                            f"  来源：{stat.get('source', '—')}")
        elif kind == "speed_failed":
            # 这个分支 core 从来不发（已 grep 确认）——实际失败走的是
            # KIND_ERROR，而基类现在会把它写进输出区。留着是为了将来
            # core 真的区分「源失败」和「全部失败」时不必再改 UI。
            self.tiles[0].set_value("失败")
            self.console.err(str(stat.get("reason", "测速失败")))
