"""WiFi 页：当前连接 + 网络扫描。

拆成两个标签页而不是只留一个：
- **当前连接**（`probes_ext.wifi_info`）读 `netsh wlan show
  interfaces`，只要网卡连着就有数据，直接回答「我的网络卡不卡」。
- **网络扫描**（`core/wifi_scan`）读 `netsh wlan show networks
  mode=bssid`，列出周围所有可见 AP。排障时真正的问题常常是
  「哪个 SSID 是我家的」「邻居的 5GHz 是不是把我的 2.4 塞满了」
  ——只读当前连接看不出来。

两者的失败原因不同（服务没运行 / 没网卡 / 被禁广播），分开呈现
比混在一个输出里好排查。
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout

from ...core import probes_ext, wifi_scan
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page


class WifiPage(Page):
    NAME = "wifi"
    TITLE = "WiFi 无线"
    SUBTITLE = "当前连接的质量，以及周围所有可见的无线网络"
    HAS_STATS = True
    HINT = "点「扫描」搜索附近无线网络，信号强度和加密方式会显示在这里"

    #: 两个子页要显示的指标完全不同。
    #:
    #: 需与扫描发出的键一致。扫描发的键是
    #: 「可见网络/频段分布/最强信号/加密情况/开放网络」——**交集为空**，
    #: 于是扫描时四个块全是「—」。用 set_tiles 按子页改标签。
    _TILES_CURRENT = ("信号强度", "信号评价", "协商下行", "协商上行")
    _TILES_SCAN = ("可见网络", "最强信号", "开放网络", "频段分布")

    def _stat_keys(self) -> tuple[str, ...]:
        return self._TILES_CURRENT

    def _build_form(self, lay: QVBoxLayout) -> None:
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._sync_tiles)
        self.tabs.addTab(self._note(
            "点击「开始测试」读取当前无线连接的信号强度、频段、信道"
            "与协商速率。数据来自 Windows 的 WLAN 服务，无需管理员权限。\n\n"
            "若提示服务未运行：Win+R 输入 services.msc，找到 "
            "「WLAN AutoConfig（无线自动配置）」设为自动并启动。"),
            "当前连接")
        self.tabs.currentChanged.connect(self._sync_tiles)
        self.tabs.addTab(self._note(
            "点击「开始测试」扫描周围所有可见的无线网络，"
            "按 6GHz / 5GHz / 2.4GHz 分组列出。\n\n"
            "每条包含：SSID、BSSID（即 MAC 地址）、频段、信道、"
            "信号强度、加密类型、身份验证、协商速率。\n\n"
            "需要无线网卡。用网线连接或没有 WiFi 硬件的机器无法扫描，"
            "这是硬件限制。"),
            "网络扫描")
        lay.addWidget(self.tabs)

    @staticmethod
    def _note(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Dim")
        lab.setWordWrap(True)
        return lab

    def _sync_tiles(self) -> None:
        keys = (self._TILES_CURRENT if self.tabs.currentIndex() == 0
                else self._TILES_SCAN)
        self.set_tiles(keys)

    def task(self):
        if self.tabs.currentIndex() == 0:
            return (probes_ext.wifi_info, "正在读取当前 WiFi 连接…")
        return (wifi_scan.wifi_scan, "正在扫描周围无线网络…")

    # ---------------------------------------------------------------- #
    def _set_tile(self, label: str, value: str) -> None:
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
            if line.startswith("═══"):
                # 频段分组标题：突出显示，让分组一眼可见
                self.console.write(line, "accent")
            elif line.startswith("═") or line.startswith("─"):
                self.console.write(line, "dim")
            elif line.startswith("✓"):
                self.console.write(line, "ok")
            elif "⚠" in line:
                self.console.write(line, "warn")
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)
