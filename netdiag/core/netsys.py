"""Windows 系统网络状态查询（只读）。

这一组功能全是**只读**的：跑一百遍也不会弄坏系统，最坏结果是
「没查到」。与 netsys_repair 里会改配置的修复操作严格分开。

每个探测都遵循同一个约定：命令失败不算错误，只是这一项没数据 ——
因为在别人的机器上，某个子命令可能不存在、被组策略禁用，或者
语言不同导致输出无法解析。这类情况要降级显示，不能抛异常。

netsh 与 ipconfig 的输出都跟随 Windows 显示语言，且**同一语言下
各版本的用词也不同**（Win10 是「频段/信道」，Win11 是「波段/通道」）。
所以解析一律中英双语，标签宽松。
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple

from .encoding import IS_WIN, run
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post

# netsh 的标签行有前导空格（缩进对齐到冒号），所以不加行首锚点；
# 冒号两侧留空，半角全角都接受——中文 Windows 输出的是全角。
_LBL = r"[ \t]*[:：][ \t]*"


def _capture(cmd: list[str], *, timeout_note: str = "") -> tuple[str, int]:
    """跑一条只读命令，返回 (输出, 返回码)。

    失败不抛异常——只读探测里「命令不存在」是常态（精简版 Windows、
    组策略禁用、UAC 未提权），调用方拿到空串后显示「无法读取」即可。
    """
    lines: list[str] = []
    try:
        rc = run(cmd, lines.append)
    except Exception:                                  # noqa: BLE001
        return "", -1
    return "\n".join(lines), rc


# ---------------------------------------------------------------- #
#  1. 路由表
# ---------------------------------------------------------------- #
# route print 的表头形如：
#   IPv4 路由表
#   活动路由:
#   网络掩码        网关          接口              跃点数
#   0.0.0.0          0.0.0.0      192.168.153.2     35
# route print 的活动路由段（真实输出）：
#   网络目标        网络掩码          网关       接口   跃点数
#     0.0.0.0          0.0.0.0    192.168.153.2  192.168.153.128     25
#     127.0.0.0        255.0.0.0        在链路上      127.0.0.1    331
# 中文与英文都是**五列**，第四列是网卡地址（中文是「在链路上」）。
# 只写四列的话只有默认路由那种网关恰好是 IP 的行能对上——实测
# 10 条路由只解析出 1 条就是这个原因。
_RE_ROUTE_ROW = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"(\S+)\s+(\S+)\s+(\d+)\s*$", re.M)

# 「在链路上」= 目标网段直连，没有下一跳
_ON_LINK = re.compile(r"^(?:在链路上|On-link|On link)$", re.I)


class Route(NamedTuple):
    dest: str        # 目标网络
    mask: str        # 子网掩码
    gateway: str     # 下一跳，直连时是「在链路上」
    iface: str       # 出口网卡地址
    metric: int      # 跃点数，越小越优先

    @property
    def is_default(self) -> bool:
        return self.dest == "0.0.0.0"

    @property
    def on_link(self) -> bool:
        return bool(_ON_LINK.match(self.gateway)) or self.gateway == "0.0.0.0"

    def describe(self) -> str:
        if self.is_default:
            return (f"默认路由 → {self.gateway} via {self.iface}"
                    f"（跃点 {self.metric}）")
        bits = _mask_bits(self.mask)
        suffix = f"/{bits}" if bits else f" 掩码 {self.mask}"
        gw = "直连" if self.on_link else f"经 {self.gateway}"
        return f"{self.dest}{suffix} {gw}（跃点 {self.metric}）"


def _mask_bits(mask: str) -> int:
    """点分掩码转前缀长度，非连续返回 0。

    连续掩码是「高位全 1、低位全 0」，等价于 ``n & (n + 1) == 0``
    ——``n + 1`` 只会进位到最低的 0 位，两者按位与为 0 当且仅当
    掩码连续。``n == 0``（掩码 0.0.0.0）不是连续掩码，会被这条
    规则误判成 /0，所以要单独排除。
    """
    try:
        n = 0
        for part in mask.split("."):
            b = int(part)
            if not 0 <= b <= 255:
                return 0
            n = (n << 8) | b
    except (ValueError, AttributeError):
        return 0
    if n == 0:
        return 0
    # 连续掩码的判定：取反后加一是 2 的幂。
    # ``n & (n + 1) == 0`` 判的是「只有一个 1」的掩码
    # （/32、/0），不是连续掩码——0xFFFFFF00 与 0xFFFFFF01 的
    # 按位与并不为 0，用那条规则会把 /24 判成非连续。
    inv = (~n) & 0xFFFFFFFF
    if inv & (inv + 1) == 0:
        return bin(n).count("1")
    return 0


def parse_routes(text: str) -> list[Route]:
    """从 ``route print`` 的输出里取活动路由。

    只取「活动路由」段——持久路由和主机路由一般用不上，全列出来
    只会淹没关键信息。默认路由排最前，其余按跃点升序。
    """
    if not text.strip():
        return []
    seg = text
    for marker in ("活动路由", "Active Routes"):
        i = seg.find(marker)
        if i >= 0:
            seg = seg[i:]
            break
    out: list[Route] = []
    seen: set[tuple[str, str, str, str]] = set()
    for m in _RE_ROUTE_ROW.finditer(seg):
        dest, mask, gw, iface, metric = m.groups()
        key = (dest, mask, gw, iface)
        if key in seen:
            continue
        seen.add(key)
        out.append(Route(dest, mask, gw, iface, int(metric)))
    out.sort(key=lambda r: (not r.is_default, r.metric))
    return out


# ---------------------------------------------------------------- #
#  2. DNS 缓存
# ---------------------------------------------------------------- #
# ipconfig /displaydns 的真实输出：
#   记录名称. . . . . . . : ms_tcpip
#   记录类型. . . . . . . : 1
#   生存时间. . . . . . . : 2
#   A (主机)记录  . . . . : 172.65.90.23
# 标签与冒号之间是 ipconfig 特有的「点 + 空格」填充，\\s 匹配不到点号，
# 所以用 [\\s.．。·]* 把它吃掉。
_DOTS = r"[\s.．。·]*"
_RE_DNS_NAME = re.compile(rf"记录名称{_DOTS}[:：]{_DOTS}(\S[^\r\n]*)", re.M)
_RE_DNS_NAME_EN = re.compile(
    rf"Record Name{_DOTS}[:：]{_DOTS}(\S[^\r\n]*)", re.I | re.M)
_RE_DNS_TYPE = re.compile(rf"记录类型{_DOTS}[:：]{_DOTS}(\S+)", re.M)
_RE_DNS_TYPE_EN = re.compile(
    rf"Record Type{_DOTS}[:：]{_DOTS}(\S+)", re.I | re.M)
_RE_DNS_TTL = re.compile(rf"生存时间{_DOTS}[:：]{_DOTS}(\S+)", re.M)
_RE_DNS_TTL_EN = re.compile(rf"TTL{_DOTS}[:：]{_DOTS}(\S+)", re.I | re.M)
_RE_DNS_DATA = re.compile(
    r"^[ \t]*(?:A|AAAA|CNAME|PTR)[^\r\n]{0,20}[:：][ \t]*(\S+)", re.M)

# 记录类型是数字，查表转成人话
_DNS_TYPES = {"1": "A", "2": "NS", "5": "CNAME", "12": "PTR",
              "15": "MX", "16": "TXT", "28": "AAAA", "33": "SRV"}


def parse_dns_cache(text: str) -> list[dict[str, str]]:
    """从 ``ipconfig /displaydns`` 取出缓存的解析记录。

    按记录块解析：每条记录从「记录名称」开始，到下一个「记录名称」
    之前结束。这样即使某条记录缺了某个字段，后面的记录也不会错位。
    """
    if not text.strip():
        return []
    heads: list[tuple[str, int]] = []
    for rx in (_RE_DNS_NAME, _RE_DNS_NAME_EN):
        heads = [(m.group(1).strip(), m.start()) for m in rx.finditer(text)]
        if heads:
            break
    if not heads:
        return []
    out: list[dict[str, str]] = []
    for i, (name, start) in enumerate(heads):
        end = heads[i + 1][1] if i + 1 < len(heads) else len(text)
        seg = text[start:end]

        def first(patterns: list[re.Pattern]) -> str:
            for rx in patterns:
                m = rx.search(seg)
                if m:
                    return m.group(1).strip()
            return "-"

        t = first([_RE_DNS_TYPE, _RE_DNS_TYPE_EN])
        out.append({
            "name": name,
            "type": _DNS_TYPES.get(t, t),
            "ttl": first([_RE_DNS_TTL, _RE_DNS_TTL_EN]),
            "addr": first([_RE_DNS_DATA]),
        })
    return out


# ---------------------------------------------------------------- #
#  3. TCP 全局参数
# ---------------------------------------------------------------- #
# netsh interface tcp show global 的真实输出（Windows 10/11）：
#   接收方缩放状态          : enabled
#   接收窗口自动调节级别    : normal
#   加载项拥塞控制提供程序  : default
#   最大 SYN 重新传输次数   : 4
#   初始 RTO               : 1000
# 只挑对排障有意义的几个：全量有十四项，多数与「能不能上网」无关。
_RE_TCP_PAIR = re.compile(r"^\s*(\S[^:\uff1a\n]*?)\s*[:\uff1a]\s*(\S.*?)\s*$", re.M)

# netsh 各版本措辞不一（Win11 会说「加载项拥塞控制提供程序」，
# 英文版是「Congestion Control Provider」），所以按关键词匹配
# 而不是精确等值——只列真正影响排障的项。
_TCP_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("接收窗口自动调节", "autotuning level"), "接收窗口调节"),
    (("拥塞控制", "congestion control"), "拥塞控制"),
    (("syn 重新传输", "syn retransmit"), "SYN 重传"),
    (("初始 rto", "initial rto"), "初始 RTO"),
    (("快速打开", "fast open"), "快速打开"),
    (("ecn",), "ECN"),
    (("rfc 1323", "时间戳"), "时间戳"),
)


def parse_tcp_global(text: str) -> list[tuple[str, str]]:
    """解析 ``netsh interface tcp show global``，返回 (中文键, 值)。

    按关键词而非精确标签匹配，因为中英文与各 Windows 版本的措辞
    差异很大（实测 Win11 就有「加载项拥塞控制提供程序」这种写法）。
    """
    if not text.strip():
        return []
    raw: list[tuple[str, str]] = []
    for m in _RE_TCP_PAIR.finditer(text):
        raw.append((m.group(1).strip(), m.group(2).strip()))
    out: list[tuple[str, str]] = []
    used: set[str] = set()
    for keys, label in _TCP_RULES:
        for k, v in raw:
            kl = k.lower()
            if any(word in kl for word in keys) and label not in used:
                out.append((label, v))
                used.add(label)
                break
    return out


# ---------------------------------------------------------------- #
#  4. 网卡列表与 MTU
# ---------------------------------------------------------------- #
# netsh interface ipv4 show interfaces：
#   Idx  Met         MTU          状态                名称
#   1  75  4294967295  connected  Loopback Pseudo-Interface 1
#   5  25        1500  connected  Ethernet0
_RE_NIC = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.+?)\s*$", re.M)
# 英文表头是 Connected / Disconnected
_NIC_UP = {"connected", "已连接", "connected", "up"}
_NIC_DOWN = {"disconnected", "已断开", "down"}


class Nic(NamedTuple):
    idx: int
    metric: int
    mtu: int
    name: str
    up: bool

    def describe(self) -> str:
        state = "已连接" if self.up else "已断开"
        # 回环和隧道接口的 MTU 常是 4294967295（无意义），显示成「—」
        mtu = "—" if self.mtu >= 0xFFFFFFFF or self.mtu == 0 else str(self.mtu)
        return f"[{self.idx}] {self.name}  {state}  MTU {mtu}  跃点 {self.metric}"


def parse_nics(text: str) -> list[Nic]:
    if not text.strip():
        return []
    out: list[Nic] = []
    for m in _RE_NIC.finditer(text):
        idx, metric, mtu, state, name = m.groups()
        # 表头行会被误匹配吗？表头是「Idx  Met  MTU  状态  名称」，
        # 第二列不是纯数字，正则要求 \d+ 必然排除。
        out.append(Nic(int(idx), int(metric), int(mtu), name.strip(),
                       state.lower() in _NIC_UP))
    out.sort(key=lambda n: (not n.up, n.metric))
    return out


# ---------------------------------------------------------------- #
#  5. 防火墙
# ---------------------------------------------------------------- #
# netsh advfirewall show allprofiles 的真实输出：
#   域配置文件 设置:
#   状态                                  启用
#   防火墙策略                          BlockInbound,AllowOutbound
#   LocalFirewallRules                    N/A (仅 GPO 存储)
#   专用配置文件 设置:
#   状态                                  关闭
# 注意两点：段标题「域配置文件 设置」中间有空格（各版本还有别的
# 措辞，所以用正则而非 find 精确匹配）；状态值是「启用/禁用」，
# 不是「开启/关闭」。
_RE_FW_SECTION = re.compile(
    r"^\s*(域|专用|公用|Domain|Private|Public)[^:\uff1a\n]{0,6}\s*"
    r"(?:配置文件|Profile|配置文件 设置|设置)?\s*[:\uff1a]?\s*$",
    re.I | re.M)
_RE_FW_STATE = re.compile(
    r"^\s*状态\s*[:\uff1a]?\s*(\S+)|^\s*State\s*[:\uff1a]?\s*(\S+)",
    re.I | re.M)
# 真实输出里「防火墙策略」后面**没有冒号**，值直接靠空格分隔——
# 和「状态」那两行的格式不同，必须分开写正则。
_RE_FW_POLICY = re.compile(
    r"^\s*(?:防火墙策略|Firewall [Pp]olicy)\s*[:\uff1a]?\s*(\S.*?)\s*$", re.M)
_FW_SECTION_EN = {"domain": "域", "private": "专用", "public": "公用"}


def parse_firewall(text: str) -> dict[str, str]:
    """解析防火墙的三个配置文件（域 / 专用 / 公用）状态。

    返回形如 ``{"域": "启用", "域策略": "BlockInbound,AllowOutbound"}``。
    按段标题切分再在段内找状态——不能用全文 find，否则三个「状态」
    行会互相覆盖，只剩最后一个。
    """
    if not text.strip():
        return {}
    marks: list[tuple[str, int]] = []
    for m in _RE_FW_SECTION.finditer(text):
        word = m.group(1)
        key = _FW_SECTION_EN.get(word.lower(), word)
        if key not in {k for k, _ in marks}:
            marks.append((key, m.start()))
    if not marks:
        return {}
    out: dict[str, str] = {}
    for idx, (key, start) in enumerate(marks):
        end = marks[idx + 1][1] if idx + 1 < len(marks) else len(text)
        seg = text[start:end]
        sm = _RE_FW_STATE.search(seg)
        if sm:
            v = (sm.group(1) or sm.group(2) or "").strip()
            out[key] = "启用" if v.lower() in ("启用", "on", "enabled",
                                            "enable") else "禁用"
        pm = _RE_FW_POLICY.search(seg)
        if pm:
            out[key + "策略"] = pm.group(1).strip()
    return out


# ---------------------------------------------------------------- #
#  6. Winsock 目录
# ---------------------------------------------------------------- #
# netsh winsock show catalog 的真实输出（Windows 10/11）：
#   Winsock 目录提供程序项
#   项类型:                             基本服务提供程序
#   描述:                               MSAFD Tcpip [TCP/IP]
#   提供程序 ID:                        {E70F1AA0-...}
#   提供程序路径:                       %SystemRoot%\system32\mswsock.dll
#   目录项 ID:                          1001
# 装过 VPN / 加速器 / 某些安全软件后，这里会多出第三方的 dll ——
# 那是「装了某软件后网络就坏了」类问题的第一排查点。
_RE_WS_DESC = re.compile(
    r"^\s*(?:描述|Description)\s*[:\uff1a]\s*(\S.*?)\s*$", re.M)
_RE_WS_PATH = re.compile(
    r"^\s*(?:提供程序路径|Provider Path)\s*[:\uff1a]\s*(\S.*?)\s*$",
    re.I | re.M)
# 路径在 System32 / Windows 目录下的算系统自带
_RE_WS_SYSTEM = re.compile(
    r"system32|syswow64|%SystemRoot%|%windir%|\\windows\\", re.I)


def parse_winsock(text: str) -> tuple[list[str], list[str]]:
    """解析 Winsock 目录，返回 (系统自带, 第三方)。

    判定依据是**路径**而不是名字：系统组件也可能有非标准名，而
    第三方装的 dll 一定在 Program Files 或用户目录里。
    """
    if not text.strip():
        return [], []
    descs = [m.group(1).strip() for m in _RE_WS_DESC.finditer(text)]
    paths = [m.group(1).strip() for m in _RE_WS_PATH.finditer(text)]
    sys_items: list[str] = []
    third: list[str] = []
    # 描述与路径在输出里成对出现，数量不同时按短的那个截断
    for i in range(min(len(descs), len(paths))):
        name, path = descs[i], paths[i]
        if not name:
            continue
        bucket = sys_items if _RE_WS_SYSTEM.search(path) else third
        if name not in bucket:
            bucket.append(name)
    return sys_items, third


# ---------------------------------------------------------------- #
#  7. WLAN 报告（微软官方生成）
# ---------------------------------------------------------------- #
def _wlan_report_path() -> str:
    # netsh wlan show wlanreport 会把 HTML 写到
    # %ProgramData%\Microsoft\Wlansvc\Reports\wlan-report-latest.html
    import os
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    return str(os.path.join(base, "Microsoft", "Wlansvc", "Reports",
                            "wlan-report-latest.html"))


# ---------------------------------------------------------------- #
#  呈现
# ---------------------------------------------------------------- #
def sys_diag(post: Post) -> None:
    """一次性拉取全部只读系统网络状态。

    逐项容错：任何一条子命令失败都只影响它自己那一段，其余照常
    输出。全部失败才报整体错误。
    """
    if not IS_WIN:
        post(KIND_ERROR, "系统网络状态查询目前仅支持 Windows")
        return

    ok = 0
    fail: list[str] = []

    # ---- 1. 网卡列表（先跑，它给出 MTU，后面几项都靠它定位问题）----
    txt, _ = _capture(["netsh", "interface", "ipv4", "show", "interfaces"])
    nics = parse_nics(txt)
    if nics:
        ok += 1
        up = [n for n in nics if n.up and "Loopback" not in n.name
              and "Loopback" not in n.name.lower()]
        post(KIND_STAT, ("活动网卡", f"{len(up)} 个"))
        post(KIND_LINE, "── 网卡与 MTU ──")
        for n in nics:
            post(KIND_LINE, "  " + n.describe())
        # MTU 被改小是 VPN / 隧道的典型副作用，值域外直接点出来
        odd = [n for n in nics if n.up and 0 < n.mtu < 576
               and "Loopback" not in n.name]
        if odd:
            post(KIND_LINE, f"  ⚠ 有 {len(odd)} 张活动网卡的 MTU 小于 576，"
                            f"这会切断大包传输（VPN/隧道常见副作用）")
    else:
        fail.append("网卡列表")
        post(KIND_LINE, "── 网卡与 MTU ──\n  无法读取（netsh 输出无法解析）")
    post(KIND_LINE, "")

    # ---- 2. TCP 全局参数 ----
    txt, _ = _capture(["netsh", "interface", "tcp", "show", "global"])
    tcp = parse_tcp_global(txt)
    if tcp:
        ok += 1
        post(KIND_STAT, ("拥塞控制", next(
            (v for k, v in tcp if k == "拥塞控制"), "-")))
        post(KIND_LINE, "── TCP 全局参数 ──")
        for k, v in tcp:
            post(KIND_LINE, f"  {k:<12}{v}")
    else:
        fail.append("TCP 全局参数")
    post(KIND_LINE, "")

    # ---- 3. 路由表 ----
    txt, _ = _capture(["route", "print"])
    routes = parse_routes(txt)
    if routes:
        ok += 1
        default = [r for r in routes if r.is_default]
        post(KIND_STAT, ("路由条数", f"{len(routes)} 条"))
        post(KIND_STAT, ("默认网关", default[0].gateway if default else "无"))
        post(KIND_LINE, "── 路由表（按跃点排序，只列前 12 条）──")
        for r in routes[:12]:
            post(KIND_LINE, "  " + r.describe())
        if len(routes) > 12:
            post(KIND_LINE, f"  … 另有 {len(routes) - 12} 条")
        # 多条默认路由 = 多网卡同时上网，排查「流量走错出口」要看这里
        if len(default) > 1:
            post(KIND_LINE, f"  ⚠ 有 {len(default)} 条默认路由 —— 多网卡"
                            f"或 VPN 叠加，流量可能走非预期的出口")
    else:
        fail.append("路由表")
        post(KIND_LINE, "── 路由表 ──\n  无法读取（route print 输出无法解析）")
    post(KIND_LINE, "")

    # ---- 4. DNS 缓存 ----
    txt, _ = _capture(["ipconfig", "/displaydns"])
    recs = parse_dns_cache(txt)
    if recs:
        ok += 1
        post(KIND_STAT, ("DNS 缓存", f"{len(recs)} 条"))
        post(KIND_LINE, f"── DNS 缓存（{len(recs)} 条，"
                        f"显示最近 {min(12, len(recs))} 条）──")
        for r in recs[:12]:
            ttl = r["ttl"] if r["ttl"] != "-" else "-"
            post(KIND_LINE, f"  {r['name'][:44]:<44}{r['addr'][:40]:<40}"
                            f"TTL {ttl}")
    else:
        post(KIND_STAT, ("DNS 缓存", "0 条"))
        post(KIND_LINE, "── DNS 缓存 ──\n  缓存为空（刚清过，或没解析过域名）")
    post(KIND_LINE, "")

    # ---- 5. 防火墙 ----
    txt, _ = _capture(["netsh", "advfirewall", "show", "allprofiles"])
    fw = parse_firewall(txt)
    if fw:
        ok += 1
        states = [fw.get(k, "?") for k in ("域", "专用", "公用")]
        post(KIND_STAT, ("防火墙", " ".join(states)))
        post(KIND_LINE, "── 防火墙 ──")
        for k in ("域", "专用", "公用"):
            if k in fw:
                pol = fw.get(k + "策略", "")
                post(KIND_LINE, f"  {k}配置文件  {fw[k]}   {pol}")
    else:
        fail.append("防火墙")
    post(KIND_LINE, "")

    # ---- 6. Winsock 目录 ----
    txt, _ = _capture(["netsh", "winsock", "show", "catalog"])
    sysw, third = parse_winsock(txt)
    if sysw or third:
        ok += 1
        post(KIND_STAT, ("第三方 LSP", f"{len(third)} 个"))
        post(KIND_LINE, f"── Winsock 目录（系统 {len(sysw)} 项 / "
                        f"第三方 {len(third)} 项）──")
        if third:
            for t in third[:10]:
                post(KIND_LINE, f"  ⚠ {t}")
            post(KIND_LINE, "  第三方项意味着有软件往网络栈里插了组件。"
                            "装完某软件后网络异常，优先怀疑它们。")
        else:
            post(KIND_LINE, "  ✓ 只有系统自带项，没有第三方 LSP")
    else:
        fail.append("Winsock 目录")
    post(KIND_LINE, "")

    # ---- 7. 协议统计 ----
    txt, _ = _capture(["netstat", "-s"])
    if txt.strip():
        ok += 1
        # 真实输出是「  接收的数据包 = 203787」——数字在行尾、标签在
        # 行首，用 ^\d+ 匹配会一行都取不到。
        pat = re.compile(
            r"^\s*(\S[^=\uff1d\n]{2,30}?)\s*[=\uff1d]\s*(\d[\d,]*)\s*$",
            re.M)
        rows = [(a_, b_) for a_, b_ in pat.findall(txt)]
        # 只取收发包与丢弃这几项，丢包率相关
        watch = ("接收的数据包", "输出的数据包", "丢弃的接收", "丢弃的输出",
                 "Received Packets", "Outgoing Packets",
                 "Received Packet Errors", "Outgoing Packet Errors")
        picked = [(k, v) for k, v in rows
                  if any(w in k for w in watch)]
        if picked:
            post(KIND_STAT, ("累计接收包", f"{picked[0][1]}"))
            post(KIND_LINE, "── 协议收发统计（累计值，重启归零）──")
            for k, v in picked:
                post(KIND_LINE, f"  {k:<24}{int(v.replace(',', '')):,}")
        else:
            post(KIND_LINE, "── 协议收发统计 ──\n  netstat -s 输出无法解析")
        # 丢弃数不为 0 值得提示——但只给数字，不下结论
        drops = [int(v.replace(",", "")) for k, v in rows
                 if "丢弃" in k or "Errors" in k]
        if drops and sum(drops) > 0:
            post(KIND_LINE, f"  累计丢弃 {sum(drops):,} 个包。"
                            f"少量丢弃是正常的（网卡队列满、协议重传），"
                            f"比例持续偏高才需要查。")
    else:
        fail.append("协议统计")
    post(KIND_LINE, "")

    # ---- 8. WLAN 报告 ----
    post(KIND_LINE, "── WLAN 报告 ──")
    txt, _ = _capture(["netsh", "wlan", "show", "wlanreport"])
    if txt.strip() and "没有运行" not in txt and "not running" not in txt.lower():
        ok += 1
        p = _wlan_report_path()
        exists = _file_exists(p)
        post(KIND_STAT, ("WLAN 报告", "已生成" if exists else "已请求"))
        post(KIND_LINE, f"  微软官方分析报告：{p}")
        if not exists:
            post(KIND_LINE, "  报告正在后台生成，稍等几秒后点「打开目录」查看。")
    else:
        post(KIND_LINE, "  WLAN 服务未运行，跳过。")

    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)
    if ok:
        post(KIND_STAT, ("成功读取", f"{ok}/8 项"))
    if fail:
        post(KIND_LINE, f"未能读取：{'、'.join(fail)}")
        post(KIND_LINE, "  这些项通常是被组策略禁用或需要管理员权限，"
                        "不影响其他诊断。")
    else:
        post(KIND_LINE, f"✓ {ok}/8 项全部读取成功")


def _file_exists(path: str) -> bool:
    import os
    try:
        return os.path.isfile(path)
    except OSError:
        return False


# ---------------------------------------------------------------- #
#  路由策略分析
# ---------------------------------------------------------------- #
#: 常见的虚拟网卡名字特征。命中不代表有问题，但**同时**有默认路由
#: 指向它就要看一眼——那意味着「访问内网可能走了 VPN」或者反之。
_VNIC_HINTS = (
    "vpn", "tap", "tun", "wg", "wireguard", "tailscale", "zerotier",
    "nordlynx", "nord", "expressvpn", "openvpn", "pptp", "l2tp", "ike",
    "virtual", "vmware", "virtualbox", "hyper-v", "wintun",
)
#: 回环接口不算「虚拟网卡」——每台机器都有，看到它不该被提示。
#: 它会被当成异常只是因为名字里有 "Loopback"，与本模块的判断无关。
_NOT_VNIC = ("loopback", "software loopback", "环回")

#: 这些网段是「内网」，如果被一条指向虚拟网卡的路由覆盖，说明 VPN
#: 抢了本该走本地网段的流量——访问打印机、NAS、公司内网会失败。
_PRIVATE_PREFIX = (
    "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.2", "172.30.", "172.31.", "169.254.",
)


def _is_vnic(name: str) -> bool:
    """这个名字是不是虚拟网卡（排除回环）。"""
    low = (name or "").lower()
    if any(x in low for x in _NOT_VNIC):
        return False
    return any(h in low for h in _VNIC_HINTS)


def parse_ifaces(text: str) -> dict[int, str]:
    """``route print`` 开头的「接口列表」-> {接口索引: 名字}。

    这一段是唯一能把「192.168.153.128」这个地址还原成
    「Intel(R) 82574L Gigabit Network Connection」的映射，没有它
    用户看到的就是一串 IP，不知道对应哪块网卡。
    """
    out: dict[int, str] = {}
    seg = text
    for marker in ("接口列表", "Interface List"):
        i = seg.find(marker)
        if i >= 0:
            seg = seg[i:]
            break
    for line in seg.splitlines():
        # 格式有两种，取决于系统语言：
        #   5...00 0c 29 31 82 b4 ......Intel(R) 82574L Gigabit...
        #   1...........................Software Loopback Interface 1
        # 前者是「索引 + MAC + ...... + 名字」，后者只有索引和名字。
        # 不管哪种，名字都是**最后一段**——按...... 切最稳。
        m = re.match(r"\s*(\d+)\.{2,}", line)
        if not m:
            continue
        rest = line[m.end():]
        name = rest.split("......")[-1].strip()
        if name:
            out[int(m.group(1))] = name
    return out


def analyze_routes(routes: list[Route], ifaces: dict[int, str]) -> list[dict]:
    """找出路由表里「值得用户看一眼」的地方。

    返回的每条都是**有据可查的事实**，不是猜测：多默认路由、私有网段
    被虚拟网卡接管、默认路由指向本机地址（装了代理软件的典型症状）、
    跃点异常大或相同。判断不了的（谁是 VPN 出口）只提示、不报错。
    """
    issues: list[dict] = []

    def add(level: str, title: str, detail: str, hint: str = "") -> None:
        issues.append({"level": level, "title": title,
                       "detail": detail, "hint": hint})

    defaults = [r for r in routes if r.is_default]

    # ---- 1. 默认路由有几条 ----
    if len(defaults) == 0:
        add("bad", "没有默认路由",
            "路由表里找不到 0.0.0.0 的默认路由。",
            "没有默认路由，访问任何不在本网段的地址都会失败——"
            "典型症状是「能上内网但上不了网」。路由器没接上、"
            "或者静态路由被清掉了，重启路由器通常能恢复。")
    elif len(defaults) > 1:
        names = "、".join(
            f"{r.gateway} via {_iface_name(ifaces, r.iface)}（跃点 {r.metric}）"
            for r in sorted(defaults, key=lambda x: x.metric))
        add("warn", f"有{len(defaults)} 条默认路由",
            names,
            "多条默认路由意味着装了多网卡 / VPN / 虚拟网卡。"
            "Windows 会按跃点小的优先选，只有第一行真正生效。"
            "装过 VPN 或虚拟网卡软件后出现这个是正常的；"
            "但如果你发现访问内网失败，看第二行是不是被虚拟网卡抢走了。")

    # ---- 2. 默认路由指向本机 / 虚拟网卡 ----
    #
    # 三种形态，分别代表不同的东西：
    #   a) 网关 = 127.0.0.1                代理软件装在本机，全部流量交给它
    #   b) 网关 = 出口地址（非回环）        虚拟网卡。下一跳是本机在该虚拟
    #                                      网卡上的地址，流量先进虚拟网卡
    #   c) 网关在虚拟网段（10./172./192.168.）
    #      且出口对应的网卡是虚拟网卡        VPN 客户端，通常是「全局代理」
    # 早先只判「网关 == 出口」，结果 c) 这种最常见的 VPN 形态反而漏了——
    # 因为 VPN 的网关（10.8.0.1）和本机虚拟网卡地址（10.8.0.2）本来
    # 就差 1，不相等。判据必须是「出口网卡是不是虚拟网卡」。
    for r in defaults:
        gw, iface = r.gateway, r.iface
        if not gw or gw in ("0.0.0.0",):
            continue

        if gw == "127.0.0.1":
            add("warn", "默认路由指向本机回环",
                "网关 127.0.0.1",
                "这不是配置错误，而是有代理软件在本机接管了所有流量——"
                "装过 VPN、加速器、游戏代理会出现这一条。"
                "特征是「所有流量都必须经过那个软件」，"
                "把它关掉这条路由就会消失。")
            continue

        if gw == iface:
            add("warn", "默认路由的下一跳是本机",
                f"网关和出口都是 {gw}",
                "下一跳就是本机，说明流量先交给本机的虚拟网卡再出去——"
                "VPN、加速器、虚拟机都会这样。"
                "想确认是谁占的，看上面的网卡清单里哪个名字带 VPN/TAP。")
            continue

        # c) 出口网卡是虚拟网卡（用 ipconfig 建的 IP->名字映射）
        iface_name = _iface_name(ifaces, iface)
        if _is_vnic(iface_name):
            add("warn", "默认路由走虚拟网卡",
                f"网关 {gw}，出口 {iface}（{iface_name}）",
                "所有流量都会先进入这个虚拟网卡。"
                "VPN、加速器、虚拟机都长这样——装了就说明你在用。"
                "副作用是访问内网（打印机、NAS、公司网段）可能失败，"
                "因为那些流量也被塞进虚拟网卡了。"
                "想恢复直连，退出对应软件或关掉它的「全局/全局代理」开关。")

    # ---- 3. 私有网段被指向虚拟网卡----
    for r in routes:
        if r.is_default or r.on_link:
            continue
        name = _iface_name(ifaces, r.iface).lower()
        if not _is_vnic(name):
            continue
        if not r.dest.startswith(_PRIVATE_PREFIX):
            continue
        add("warn", "私有网段被虚拟网卡接管",
            f"{_fmt_net(r)} 经由 {name}（{r.gateway}，跃点 {r.metric}）",
            f"访问 {r.dest} 这个网段会被送到虚拟网卡上。"
            "如果打印机、NAS、公司内网在这个网段里，它们会连不上——"
            "这是装了 VPN 之后最常见的抱怨。"
            "要解决就让 VPN 客户端加一条「不接管内网」的分流规则，"
            "或者临时退出 VPN 软件。")

    # ---- 4. 跃点异常 ----
    metrics = sorted({r.metric for r in defaults})
    if len(metrics) > 1:
        add("info", "默认路由的跃点",
            "、".join(f"跃点 {m}" for m in metrics),
            "跃点越小越优先。多个默认路由并存时，"
            "第一个就是当前生效的出口。")

    return issues


#: 记住 route print 的一个坑：**路由表里的「接口」列给的是接口的
#: IP 地址，不是「接口列表」里的那个数字索引**。所以不能直接拿它去
#: 查 parse_ifaces 的结果——那样永远查不到，分析逻辑会静默失效。
#:
#: 要把 IP 反查成网卡名，只能再跑一次 ipconfig /all，从每个适配器
#: 段落里取「IPv4 地址」和「适配器名」的对应关系。这一步有缓存，
#: 只在需要时做一次。
_IFACE_BY_ADDR: dict[str, str] | None = None


def _load_iface_map() -> dict[str, str]:
    """{接口 IP: 网卡名}。从 ipconfig /all 建一次，之后复用。"""
    global _IFACE_BY_ADDR
    if _IFACE_BY_ADDR is not None:
        return _IFACE_BY_ADDR
    out: dict[str, str] = {}
    txt, rc = _capture(["ipconfig", "/all"])
    if rc == 0:
        name = ""
        # 段落结构：「适配器名」独占一行，之后缩进的是它的属性。
        # 抓到 IPv4 地址就绑定给最近的那个适配器名。
        for line in txt.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            indented = line[:1].isspace()
            if not indented and not stripped.endswith(":"):
                name = stripped.rstrip(":")
            m = re.search(r"IPv4[^:：]*[:：]\s*(\d+\.\d+\.\d+\.\d+)", stripped)
            if m and name:
                out.setdefault(m.group(1), name)
    _IFACE_BY_ADDR = out
    return out


def _iface_name(ifaces: dict[int, str], addr: str) -> str:
    """把路由表里的接口 IP 反查成网卡名。查不到就原样返回 IP。"""
    if not addr or addr in ("127.0.0.1", "0.0.0.0"):
        return addr
    return _load_iface_map().get(addr, addr)


def _fmt_net(r: Route) -> str:
    """目标网段写成 CIDR。

    默认路由的掩码是 0.0.0.0，算出来是 0 位。**0 位是合法结果**
    （/0 就是整个互联网），不是「算不出来」——_mask_bits 失败时也返回
    0，两者在这里无法区分，但对用户来说 /0 恰好就是默认路由该有的
    显示，不需要再区分。
    """
    return f"{r.dest}/{_mask_bits(r.mask)}"


def route_report(post: Post) -> None:
    """完整路由表 + 策略分析。

    和 sysdiag 里那个只列前 12 条的摘要不同：这里列出**全部**活动
    路由（默认按目标网段聚合），并对「多默认路由 / 虚拟网卡抢内网 /
    代理劫持」这些情况给出解读。
    """
    txt, rc = _capture(["route", "print"])
    if rc != 0 or not txt.strip():
        post(KIND_ERROR, "无法读取路由表（route print 执行失败）。"
                         "可能需要以管理员身份运行。")
        return

    ifaces = parse_ifaces(txt)
    routes = parse_routes(txt)

    post(KIND_STAT, {
        "total": len(routes),
        "default": len([r for r in routes if r.is_default]),
        "ifaces": len(ifaces),
        "issues": len(analyze_routes(routes, ifaces)),
    })

    # ---- 接口清单 ----
    post(KIND_LINE, "── 网卡清单 ──")
    if ifaces:
        for idx, name in sorted(ifaces.items()):
            vnic = "  [虚拟网卡]" if _is_vnic(name.lower()) else ""
            post(KIND_LINE, f"  {idx:>3}  {name}{vnic}")
    else:
        post(KIND_LINE, "  （无法解析接口列表）")
    post(KIND_LINE, "")

    # ---- 策略分析 ----
    post(KIND_LINE, "── 策略分析 ──")
    issues = analyze_routes(routes, ifaces)
    if not issues:
        post(KIND_LINE, "  路由表结构正常：单默认路由、无虚拟网卡抢内网。")
    else:
        for it in issues:
            tag = {"bad": "✗", "warn": "⚠", "info": "i"}[it["level"]]
            post(KIND_LINE, f"  {tag} {it['title']}")
            post(KIND_LINE, f"      {it['detail']}")
            if it["hint"]:
                post(KIND_LINE, f"      {it['hint']}")
    post(KIND_LINE, "")

    # ---- 完整路由表 ----
    post(KIND_LINE, f"── 活动路由（全部 {len(routes)} 条）──")
    post(KIND_LINE, "  网络目标/掩码          下一跳                出口            跃点")
    for r in sorted(routes, key=lambda x: (not x.is_default, x.metric, x.dest)):
        net = _fmt_net(r)
        gw = "直连" if r.on_link else r.gateway
        post(KIND_LINE, f"  {net:<24} {gw:<20} {r.iface:<15} {r.metric}")


# ---------------------------------------------------------------- #
#  网卡 / 协议栈健康度
# ---------------------------------------------------------------- #
#: ipconfig /all 里每个适配器段落的名字，就是这里要判断的对象。
#: 用它做段落切分比用正则逐字段抓可靠——段落顺序、缩进、语言 variations
#: 都不影响切分。
#: ``192.168.153.128(首选)`` 这类后缀。
_IP_SUFFIX = re.compile(r"[（(](首选|首选的|Preferred)[）)]\s*$")


def _field(seg: str, *labels: str) -> str:
    r"""从 ipconfig 段落里取某个标签的值。

    **这里不能用一条正则搞定，因为 ipconfig 的「点线填充」。**
    中文 Windows 把标签和值对齐成一列，实际输出是：

        IPv4 地址 . . . . . . . . . . . . : 192.168.153.128(首选)
        子网掩码  . . . . . . . . . . . . : 255.255.255.0

    标签和冒号之间塞满了点和空格。于是
    ``re.search(r"IPv4[^:]*:\s*(\d+\.\d+..)")`` 会把标签**自带的
    那个冒号**（IPv4 的）当成分隔符，取出来的值是
    「地址 . . . . . . . . . . . .」——结果掩码算出来是 /0。

    正确做法：逐行匹配标签，再对那一行做 split(":", 1) 取后半段。
    值永远在最后一个冒号之后，点线填充完全影响不到取值。
    """
    for line in seg.splitlines():
        raw = line.strip()
        if ":" not in raw and "：" not in raw:
            continue
        for lab in labels:
            if not raw.lower().startswith(lab.lower()):
                continue
            val = re.split(r"[:：]", raw, maxsplit=1)[-1].strip()
            return _IP_SUFFIX.sub("", val).strip()
    return ""


def parse_nic_health(text: str) -> list[dict]:
    """把 ipconfig /all 拆成每个适配器一段，抽出健康度相关字段。

    只取有 IPv4 或有 MAC 的段落——纯协议（Teredo、ISATAP、Loopback）
    没有实际网卡意义，列出来只会干扰阅读。
    """
    out: list[dict] = []
    # 段落起点 = 顶格且以冒号结尾的行（中文下是「适配器名:」）
    heads: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines()):
        if line and not line[:1].isspace() and ":" in line \
                or (line and not line[:1].isspace() and "：" in line):
            name = line.strip().rstrip(":：").strip()
            if name:
                heads.append((i, name))

    lines = text.splitlines()
    for k, (start, name) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        seg = "\n".join(lines[start:end])

        ipv4 = _field(seg, "IPv4 地址", "IPv4 Address", "IPv4 配置")
        mac = _field(seg, "物理地址", "Physical Address")
        if not ipv4 and not mac:
            continue

        out.append({
            "name": name,
            "ipv4": ipv4,
            "mask": _field(seg, "子网掩码", "Subnet Mask"),
            "gateway": _field(seg, "默认网关", "Default Gateway"),
            "dhcp": _field(seg, "DHCP 服务器", "DHCP Server"),
            "lease": _field(seg, "获得租约的时间", "Lease Obtained"),
            "expire": _field(seg, "租约过期的时间", "Lease Expires"),
            "desc": _field(seg, "描述", "Description"),
            "mac": mac,
            "status": _field(seg, "媒体状态", "Media State"),
            "driver_ver": _field(seg, "驱动版本", "Driver Version"),
            "driver_date": _field(seg, "驱动日期", "Driver Date"),
        })
    return out


#: Winsock 目录里**必须存在**的套接字提供程序，按 DLL 路径判断。
#:
#: 踩过的坑，记下来免得再犯：
#:
#:  · IP 辅助服务（iphlpapi）、链路层发现（lltdp）、网桥（bridge）
#:    **不是 Winsock 组件**。它们是 NDIS 层 / 内核驱动，压根不出现在
#:    ``netsh winsock show catalog`` 里。早先按这些名字去找，结果在一台
#:    网络完全正常的机器上被判成「协议栈损坏」——三处误报。
#:
#:  · 匹配要按 **DLL 路径**（``mswsock.dll``），不是按显示名。显示名
#:    （``MSAFD Tcpip [TCP/IP]``）跟着系统语言变，英文版机器上叫别的。
#:
#: · 真正值得报的是**非系统目录的 DLL**——那才是往网络栈里插进来的
#:    第三方组件，也是「装完软件网络就坏了」的元凶。
_STACK_CORE_DLLS = (
    ("mswsock.dll", "TCP/IP 套接字"),
    ("vsocklib.dll", "vSockets（Hyper-V 虚拟化套接字）"),
)

#: 微软自带的 Winsock 提供程序名（子串匹配）。命中这些的不是第三方。
_MS_SOCK_PROVIDERS = (
    "MSAFD", "AF_UNIX", "RSVP", "vSockets", "Hyper-V",
    "TCPIP", "NTDS", "PNRP", "NLAv1", "蓝牙", "Bluetooth",
    "Namespace", "命名空间", "命名填充", "Winsock",
)


def _split_catalog(cat_txt: str) -> list[tuple[str, str]]:
    """把 netsh catalog 拆成 ``[(提供程序名, DLL 路径)]``。

    一段以「提供程序项」标题开始；段内「描述」是名字，「提供程序路径」
    是 DLL。命名空间类条目（TCPIP / NTDS / PNRP）没有路径，路径记空串。
    """
    out: list[tuple[str, str]] = []
    name = path = ""
    for raw in cat_txt.splitlines():
        line = raw.strip()
        if not line or "提供程序项" in line or "Provider Catalog Entry" in line:
            continue
        val = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
        if not val:
            continue
        if line.startswith(("描述", "Description")):
            if name:                       # 上一段没写路径就收尾
                out.append((name, path))
            name, path = val, ""
        elif line.startswith(("提供程序路径", "Provider Path")):
            path = val
    if name:
        out.append((name, path))
    return out


def nic_health(post: Post) -> None:
    """网卡与协议栈健康度。全部只读。

    判断「网卡坏了」和「网卡设错了」是两回事：

    · 驱动异常 / 媒体断开  = 硬件或驱动层的问题，重装驱动才行
    · 静态 IP 填错网关    = 配置问题，改一下就好
    · 协议栈缺组件       = 装卸软件留下的痕迹

    把这三类分开报，用户才知道该找谁。
    """
    # ---- 1. 网卡状态 ----
    txt, rc = _capture(["ipconfig", "/all"])
    nics = parse_nic_health(txt) if rc == 0 else []
    if not nics:
        post(KIND_ERROR, "ipconfig /all 执行失败，无法读取网卡信息。")
        post(KIND_STAT, {"nics": 0, "mtu_ok": 0, "stack": 0, "third": 0})
        return

    post(KIND_LINE, f"═══ 网卡（{len(nics)} 个启用）═══")
    for n in nics:
        post(KIND_LINE, "")
        post(KIND_LINE, f"  {n['name']}")

        # 媒体状态：网线拔了 / 网卡禁用都会是「已断开」
        st = n["status"]
        if st and any(k in st for k in ("已断开", "已失效", "断开", "Disabled")):
            post(KIND_LINE, "    ✗ 媒体状态：已断开"
                            "——网线没插、网卡被禁用，或设备本身故障")
        elif st:
            post(KIND_LINE, f"    ✓ 媒体状态：{st}")

        if n["ipv4"]:
            if n["mask"]:
                bits = _mask_bits(n["mask"])
                post(KIND_LINE, f"    ✓ IPv4：{n['ipv4']}/{bits}  掩码 {n['mask']}")
            else:
                post(KIND_LINE, f"    ✓ IPv4：{n['ipv4']}")
        else:
            post(KIND_LINE, "    · 没有 IPv4 地址"
                            "——没连网，或只有 IPv6")

        if n["gateway"]:
            post(KIND_LINE, f"    ✓ 默认网关：{n['gateway']}")
        else:
            post(KIND_LINE, "    ✗ 没有默认网关"
                            "——这张网卡只能访问本网段，出不了网")

        if n["dhcp"]:
            post(KIND_LINE, f"    · DHCP 服务器：{n['dhcp']}"
                            + (f"（{n['lease']}）" if n["lease"] else ""))
        elif n["ipv4"]:
            post(KIND_LINE, "    · 使用静态 IP（不是 DHCP 分配的）")

        if n["driver_ver"]:
            extra = f"，{n['driver_date']}" if n["driver_date"] else ""
            post(KIND_LINE, f"    · 驱动版本 {n['driver_ver']}{extra}")
        if n["desc"]:
            post(KIND_LINE, f"    · 型号 {n['desc']}")

    # ---- 2. 网卡 MTU ----
    post(KIND_LINE, "")
    post(KIND_LINE, "═══ 网卡 MTU ═══")
    try:
        t2, _ = _capture(["netsh", "interface", "ipv4", "show", "subinterfaces"])
        mtu_rows = [x.strip() for x in t2.splitlines() if x.strip()]
    except Exception as e:  # noqa: BLE001
        mtu_rows = []
        post(KIND_LINE, f"  无法读取：{e}")
    for line in mtu_rows:
        post(KIND_LINE, "  " + line.strip())
    post(KIND_LINE, "  MTU 异常偏小（正常是 1500，VPN 后可能变 1400）时，"
                    "小包正常但大包被静默丢弃——症状是 ping 一切正常、网页很慢。")
    # MTU 正常 = 有 IPv4 的网卡数。回环伪接口那个 4294967295 是内核
    # 占位值，不算异常，也不该让用户以为「MTU 不正常」。
    mtu_ok = sum(1 for n in nics
                 if n["ipv4"] and not n["name"].lower().startswith("loopback"))

    # ---- 3. TCP 全局参数 ----
    post(KIND_LINE, "")
    post(KIND_LINE, "═══ TCP 全局参数 ═══")
    # 子命令名在 netsh 的 int / interface 两个别名下都试一遍——精简版
    # Windows 和部分语言版本只认其中一个，报错文本是「找不到下列命令」。
    tcp_txt = ""
    for cmd in (["netsh", "interface", "tcp", "show", "globalparams"],
                ["netsh", "int", "tcp", "show", "globalparams"]):
        t3, _ = _capture(cmd)
        if t3.strip() and "找不到下列命令" not in t3 \
                and "No commands match" not in t3:
            tcp_txt = t3
            break
    if tcp_txt:
        for line in [x.strip() for x in tcp_txt.splitlines() if x.strip()][:20]:
            post(KIND_LINE, "  " + line)
        low = tcp_txt.lower()
        if "cubic" not in low and "new reno" not in low and "newreno" not in low:
            post(KIND_LINE, "  「拥塞控制」既不是 New Reno 也不是 CUBIC，"
                            "多半装了加速器或改过优化工具。")
    else:
        post(KIND_LINE, "  这台系统的 netsh 不提供 TCP 全局参数"
                        "（精简版 Windows 常见），不影响使用。")

    # ---- 4. Winsock 目录：协议栈里装了谁 ----
    post(KIND_LINE, "")
    post(KIND_LINE, "═══ 协议栈组件（Winsock 目录）═══")
    cat_txt = ""
    try:
        cat_txt, _ = _capture(["netsh", "winsock", "show", "catalog"])
    except Exception as e:  # noqa: BLE001
        post(KIND_LINE, f"  无法读取：{e}")
    if cat_txt:
        cat_low = cat_txt.lower()
        third = parse_winsock(cat_txt)[0]
        missing = 0
        for dll, label in _STACK_CORE_DLLS:
            ok = dll.lower() in cat_low
            post(KIND_LINE, f"    {'✓' if ok else '✗'} {label}（{dll}）"
                            + ("" if ok else "——缺失，协议栈可能损坏，"
                                               "用「网络修复」页的「重置 Winsock」"))

        # 第三方 = 路径不在系统目录、且名字不是微软自带的那些。
        # 命名空间类（TCPIP / NTDS / PNRP）没有 DLL 路径，属于正常组件。
        entries = _split_catalog(cat_txt)
        third = []
        for _name, _path in entries:
            if not _path:
                continue
            low = _path.lower()
            if "system32" in low or "syswow64" in low:
                continue
            if any(k in _name.lower() for k in _MS_SOCK_PROVIDERS):
                continue
            third.append((_name, _path))

        post(KIND_LINE, "")
        post(KIND_LINE, f"  目录条目 {len(entries)} 个，"
                        f"其中第三方组件 {len(third)} 个：")
        if third:
            for _name, _path in third[:12]:
                post(KIND_LINE, f"    · {_name}")
                post(KIND_LINE, f"      {_path}")
            post(KIND_LINE, "  第三方组件会插进网络栈。装完某个软件后"
                            "「网络突然坏了」，先看这里有没有陌生的名字。")
        else:
            post(KIND_LINE, "  没有第三方组件，协议栈是干净的。")

    # 统计块在最后统一发：前面各段的结果都要用到，中途发会显示半成品。
    post(KIND_STAT, {
        "nics": len(nics),
        "mtu_ok": mtu_ok,
        "stack": len(_STACK_CORE_DLLS) if cat_txt else 0,
        "third": len(third) if cat_txt else 0,
    })
