"""页面注册表。

顺序即侧边栏顺序。新增页面只需在这里加一行。

**网络修复是唯一的二级菜单**
--------------------------
`GROUPS` 里的 8 个修复操作不直接出现在一级菜单，而是收在
「网络修复」这一项下面。二级菜单按**风险从低到高**排列，用户
从上往下试即可。

为什么只有它用二级菜单：它是唯一一组「点下去会改系统设置」的
操作，收在一起才看得出哪些要小心。其它 19 页都是只读工具，
平铺出来更好找。
"""
from __future__ import annotations

from .base import Page
from .about_page import AboutPage
from .arp_page import ArpPage
from .dns_page import DnsPage
from .http_page import HttpPage
from .ip_page import IpToolPage
from .lan_page import LanPage
from .mtu_page import MtuPage
from .netinfo_page import NetInfoPage
from .ping_page import PingPage
from .ports_page import PortsPage
from .portowner_page import PortOwnerPage
from .route_page import RoutePage
from .nichealth_page import NicHealthPage
from .loss_page import LossPage
from .speed_page import SpeedPage
from .subnetscan_page import SubnetScanPage
from .diagnose_page import DiagnosePage
from .fix_page import FIX_PAGE_CLASSES, FixPage
from .hopmtu_page import HopMtuPage
from .multiprobe_page import MultiProbePage
from .sysdiag_page import SysDiagPage
from .tcp_page import TcpPage
from .trace_page import TracePage
from .wifi_page import WifiPage

# 顺序按「用户实际排障的路径」排：
#
#   不知道哪儿坏了（diagnose）—— 首选入口。工具自己逐层查完给结论，
#     不用用户先想清楚该测什么。查完有具体指向，再往下点对应页面。
#   知道问题在哪、想动手修（fix）—— 紧跟其后，因为「诊断」页的建议
#     直接指向这里的某一项。修是唯一会改系统设置的一页，排在只读
#     工具之后是刻意的：先诊断，别直接上来就重置。
#   然后是「知道自己想测什么」的单项工具，按网络栈从外到内：
#     外网侧  ping → http → mtu → hopmtu（逐跳）→ trace
#            → multiprobe（多目标对比）
#     名字侧  dns → ports
#     带宽    speed
#   本机侧  wifi → lan → tcp → arp → subnet → sys → net
#   最后是纯计算工具（ip）和关于页。
#
# diagnose 排第一是有意的：其余 16 页是工具箱，得先知道要测什么；
# 而真实场景是「网页打不开，但不知道哪一层坏了」。
#: 一级菜单里平铺的页面（不含二级菜单组里的）
PAGES: tuple[type[Page], ...] = (
    DiagnosePage,
    PingPage,
    HttpPage,
    MtuPage,
    HopMtuPage,
    MultiProbePage,
    TracePage,
    DnsPage,
    PortsPage,
    PortOwnerPage,
    RoutePage,
    NicHealthPage,
    LossPage,
    SpeedPage,
    WifiPage,
    LanPage,
    TcpPage,
    ArpPage,
    SubnetScanPage,
    SysDiagPage,
    NetInfoPage,
    IpToolPage,
    AboutPage,
)

#: 二级菜单：标题 -> 组内的页面类。组内顺序即菜单顺序。
GROUPS: dict[str, tuple[type[Page], ...]] = {
    "网络修复": tuple(FIX_PAGE_CLASSES),
}

#: 侧边栏的完整顺序。元素是页面类（平铺）或 ("group", 标题)
#: （二级菜单，先渲染组标题再缩进渲染组内页面）。
SIDEBAR: tuple[object, ...] = (
    DiagnosePage,
    ("group", "网络修复"),
    PingPage,
    HttpPage,
    MtuPage,
    HopMtuPage,
    MultiProbePage,
    TracePage,
    DnsPage,
    PortsPage,
    PortOwnerPage,
    RoutePage,
    NicHealthPage,
    LossPage,
    SpeedPage,
    WifiPage,
    LanPage,
    TcpPage,
    ArpPage,
    SubnetScanPage,
    SysDiagPage,
    NetInfoPage,
    IpToolPage,
    AboutPage,
)

#: 实际要实例化的页面 = 一级平铺的 + 每个组里的
ALL_PAGES: tuple[type[Page], ...] = PAGES + tuple(
    p for g in GROUPS.values() for p in g)

__all__ = ["PAGES", "GROUPS", "SIDEBAR", "ALL_PAGES", "Page",
           "AboutPage", "ArpPage", "DiagnosePage",
           "DnsPage", "FixPage", "HopMtuPage", "HttpPage", "MultiProbePage", "IpToolPage", "LanPage", "MtuPage",
           "NetInfoPage", "PingPage", "PortsPage", "PortOwnerPage", "RoutePage", "NicHealthPage", "LossPage",
           "SpeedPage",
           "SubnetScanPage", "SysDiagPage", "TcpPage", "TracePage",
           "WifiPage"]
