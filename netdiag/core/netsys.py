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
