"""子网内 IP 使用情况扫描。

**「ping 通」不等于「有人在用」**——这是本页存在的前提。四种常见
情况都会骗人：

    设备开机待机          ping 通    没在用，但 IP 被占着
    Windows 防火墙拦 ICMP ping 不通  **有人在用**（致命误判）
    Linux / 网络设备      常常不通   有人在用（默认禁 ping）
    DHCP 刚续约           ping 不通   已被保留，不是真空闲

所以用三层证据交叉判断，各管一段：

    ARP 表    瞬时、免费   ——「确定在用」。设备必须回 ARP 才能通信，
                              这是唯一没有误判的证据。局限是只覆盖
                              本广播域。
    TCP 端口  快速拒绝     ——「高概率在用」。445 开着基本确定是
                              Windows 设备，哪怕它的 ICMP 被防火墙
                              全封了。
    ICMP ping  最慢最弱    ——兜底。信号最弱但覆盖最广。

状态列必须区分「确定」与「不确定」：把 ping 不通直接标成「空闲」
会导致用户给已占用的地址分配新设备，直接造成 IP 冲突。
"""
from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from . import ipmath
from .encoding import IS_WIN, run
from .probes_ext import parse_arp
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post

#: 扫的端口。挑选依据是「能反推设备类型」而不是「常用」——
#: 22=类 Unix，135/139/445=SMB(Windows/域控)，3389=远程桌面，
#: 80/443=Web 服务器，62078 与 28514 是 iOS 设备在无 DHCP 时
#: 自选的 APIPA 地址特征端口。
SCAN_PORTS: tuple[tuple[int, str], ...] = (
    (445, "SMB/Windows"),
    (135, "RPC"),
    (139, "NetBIOS"),
    (3389, "远程桌面"),
    (22, "SSH"),
    (80, "Web"),
    (443, "HTTPS"),
    (62078, "iOS 设备"),
    (28514, "iOS 设备"),
)

#: TCP 阶段的并发上限。太高会一次性建满 socket 队列，被系统限流后
#: 全部超时，结果是「全都关」——比不扫更误导。每台机器的连接队列
#: 和文件句柄都有限，32 是实测下来既能压满时间又不触发限流的值。
_TCP_WORKERS = 32
#: ICMP 阶段是拉子进程，进程数比 socket 昂贵得多，降到 48。
_PING_WORKERS = 48
#: 单个 IP 的 TCP 阶段超时。局域网内通常毫秒级响应，0.4s 足够区分
#: 「拒绝」（立即返回）与「静默丢弃」（等满）。
_TCP_TIMEOUT = 0.4
#: 单个 IP 的 ping 超时。
_PING_TIMEOUT_MS = 300


class Host(NamedTuple):
    """一个被扫到有响应的地址，以及判定依据。"""

    ip: str
    state: str            # confirmed / likely / icmp_only
    basis: str            # 中文依据，直接显示
    mac: str = "-"         # ARP 才有
    ports: str = "-"      # TCP 阶段命中的端口描述
    ms: float | None = None

    @property
    def symbol(self) -> str:
        return {"confirmed": "●", "likely": "◆", "icmp_only": "◐"}[self.state]

    @property
    def label(self) -> str:
        return {"confirmed": "确定在用", "likely": "高概率在用",
                "icmp_only": "响应 ICMP"}[self.state]


def own_addresses() -> set[str]:
    """本机所有 IPv4 地址（用于把「自己」标出来）。"""
    out: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不真正发包，只是让系统选一个出口地址
        s.connect(("10.255.255.255", 1))
        got = s.getsockname()[0]
        if isinstance(got, str):
            out.add(got)
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    return out


def parse_cidr_list(cidr: str) -> tuple[int, int]:
    """把 CIDR 转成 (起始整数, 地址总数)。

    /31 与 /32 不掐头去尾——RFC 3021 的点到点链路两个地址都能用，
    而 /32 本来就只有一个。

    ``ipaddress`` 抛的异常是英文的（"Octet 999 (> 255) not permitted"），
    直接透给用户不友好，这里换成中文。
    """
    try:
        net = ipmath.parse_cidr(cidr)
    except ValueError:
        raise ValueError(
            f"「{cidr.strip()}」不是合法的 IP 或网段。"
            f"正确写法如 192.168.1.0/24 或 192.168.1.10"
        ) from None
    base = int(net.network_address)
    total = 1 << (32 - net.prefixlen)
    if net.prefixlen >= 31:
        return base, total
    return base + 1, total - 2


def probe_tcp(ip: str, ports: tuple[tuple[int, str], ...] = SCAN_PORTS,
              timeout: float = _TCP_TIMEOUT) -> list[tuple[int, str]]:
    """并发探测一个 IP 上的多个端口，返回命中的 (端口, 描述)。

    遇到第一个开放端口就返回——设备类型从这一个端口就能判出来，
    把 9 个端口全扫完只是浪费。用 ``socket.connect`` 而不是
    ``connect_ex``，是为了区分「拒绝」与「超时」：前者说明主机
    在线但端口没开，后者是被静默丢弃。
    """
    hits: list[tuple[int, str]] = []

    def one(port: int, label: str) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((ip, port))
            hits.append((port, label))
        except OSError:
            pass
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=min(len(ports), _TCP_WORKERS)) as ex:
        list(ex.map(lambda p: one(*p), ports))
    return sorted(hits)


def probe_icmp(ip: str, timeout_ms: int = _PING_TIMEOUT_MS) -> float | None:
    """ping 一个 IP，返回毫秒延迟；不通返回 None。

    **这一层最不可靠**：防火墙可以静默丢弃 ICMP 而设备完全正常。
    所以它只用来补充说明，不单独作为「有人在用」的依据。
    """
    flag = "-n" if IS_WIN else "-c"
    wait = ["-w", str(timeout_ms)] if IS_WIN else \
        ["-W", str(max(1, timeout_ms // 1000))]
    import re
    out: list[str] = []
    try:
        run(["ping", flag, "1", *wait, ip], out.append)
    except Exception:                                  # noqa: BLE001
        return None
    for line in out:
        m = re.search(r"(?:time|时间)[=<]\s*([\d.]+)\s*ms", line, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def scan_subnet(post: Post, cidr: str, *, want_tcp: bool = True,
                want_icmp: bool = True) -> None:
    """扫描一个子网，逐层收集证据并输出结论。

    顺序是刻意的：ARP 最便宜最准先做，TCP 次之，ICMP 最后。
    已经能从 ARP 确认的地址不再进 TCP/ICMP 阶段——那纯属浪费，
    而且对已确认的设备继续发包没有任何诊断价值。
    """
    if not IS_WIN:
        post(KIND_ERROR, "子网扫描目前仅支持 Windows")
        return

    try:
        start, total = parse_cidr_list(cidr)
    except ValueError as e:
        post(KIND_ERROR, f"网段格式有误：{e}")
        return

    if total <= 1:
        # /32 只有一个地址，扫它必然只找到本机或空，没有诊断价值。
        # 提示要说清「换个网段」而不是「没扫到」，否则用户会以为
        # 网段写错了。
        post(KIND_ERROR, f"这个网段只有 {total} 个可用地址，"
                         f"扫描没有意义。\n"
                         f"    请填写所在网段，例如 192.168.1.0/24。")
        return
    if total > 2000:
        post(KIND_ERROR, f"网段有 {total} 个地址，扫描会持续数分钟且"
                         f"可能触发网关的风控。请缩小范围（至少 /21）")
        return

    net = ipmath.parse_cidr(cidr)
    t0 = time.perf_counter()
    post(KIND_STAT, ("扫描地址", f"{total} 个"))
    post(KIND_STAT, {"kind": "scan_begin", "cidr": str(net),
                     "total": total})
    post(KIND_LINE, f"扫描 {net}（{total} 个可用地址）…")

    ips = [ipmath.int_to_ip(start + i) for i in range(total)]
    me = own_addresses()

    # ---- 层 1：ARP ----
    arp: dict[str, str] = {}
    out: list[str] = []
    try:
        run(["arp", "-a"], out.append)
    except Exception:                              # noqa: BLE001
        pass
    for ip, mac in parse_arp("\n".join(out)):
        arp[ip] = mac
    # ARP 表只反映「最近通信过」的设备，不是全量。想让扫描更准，
    # 先 ping 一下广播地址能促使系统与网段内所有设备建 ARP 条目。
    # 这里主动 ping 网关与广播，多半能把邻居唤出来。
    for seed in _wake_neighbors(net, me):
        probe_icmp(seed, 200)

    found: list[Host] = []
    for ip in ips:
        if ip in me:
            found.append(Host(ip, "confirmed", "本机", mac=ip))
        elif ip in arp:
            found.append(Host(ip, "confirmed", "ARP 表", mac=arp[ip]))

    post(KIND_STAT, ("ARP 发现", f"{len([h for h in found if h.basis == 'ARP 表'])} 个"))
    post(KIND_LINE, f"层 1 ARP：{len(arp)} 条邻居记录，"
                    f"确认 {len(found)} 个地址在用")
    post(KIND_STAT, {"kind": "scan_progress", "phase": "arp",
                     "done": len(found), "total": total})
    post(KIND_LINE, "")

    # ---- 层 2：TCP ----
    todo = [h.ip for h in found if h.basis != "本机"]
    if want_tcp and todo:
        post(KIND_LINE, f"层 2 TCP 端口：{len(todo)} 个地址，"
                        f"各试 {len(SCAN_PORTS)} 个端口…")
        t1 = time.perf_counter()
        results: dict[str, list[tuple[int, str]]] = {}
        with ThreadPoolExecutor(max_workers=_TCP_WORKERS) as ex:
            for ip, hits in zip(todo, ex.map(probe_tcp, todo)):
                if hits:
                    results[ip] = hits
        # 只补 TCP 命中的端口信息，**不改状态**：本机（basis="本机"）
        # 与 ARP 已确认的地址本来就更可信，不能因为「没扫到开放端口」
        # 就把本机降级成「高概率」。开 0 个服务端口的机器多了去了。
        found = [h._replace(
            ports=",".join(lbl for _, lbl in results.get(h.ip, [])) or h.ports,
        ) for h in found]
        new = [Host(ip, "likely", "TCP 端口",
                    ports=",".join(lbl for _, lbl in hits))
               for ip, hits in results.items()
               if not any(f.ip == ip for f in found)]
        found.extend(new)
        post(KIND_STAT, ("TCP 发现", f"{len(results)} 个地址"))
        post(KIND_STAT, {"kind": "scan_progress", "phase": "tcp",
                         "done": len(found), "total": total})
        post(KIND_LINE, f"  用时 {time.perf_counter() - t1:.1f}s，"
                        f"命中 {len(results)} 个")
    post(KIND_LINE, "")

    # ---- 层 3：ICMP ----
    rest = [ip for ip in ips
            if not any(f.ip == ip for f in found) and ip not in me]
    if want_icmp and rest:
        post(KIND_LINE, f"层 3 ICMP：{len(rest)} 个地址，"
                        f"各 ping 一次（并发 {_PING_WORKERS}）…")
        t2 = time.perf_counter()
        replies: list[tuple[str, float]] = []
        with ThreadPoolExecutor(max_workers=_PING_WORKERS) as ex:
            for ip, ms in zip(rest, ex.map(probe_icmp, rest)):
                if ms is not None:
                    replies.append((ip, ms))
        for ip, ms in replies:
            found.append(Host(ip, "icmp_only", "ping 响应", ms=ms))
        post(KIND_STAT, ("ICMP 响应", f"{len(replies)} 个"))
        post(KIND_STAT, {"kind": "scan_progress", "phase": "icmp",
                         "done": len(found), "total": total})
        post(KIND_LINE, f"  用时 {time.perf_counter() - t2:.1f}s，"
                        f"{len(replies)} 个有响应")
    post(KIND_LINE, "")

    # ---- 输出 ----
    el = time.perf_counter() - t0
    confirmed = [h for h in found if h.state == "confirmed"]
    likely = [h for h in found if h.state == "likely"]
    icmp_only = [h for h in found if h.state == "icmp_only"]

    post(KIND_STAT, ("确定在用", f"{len(confirmed)} 个"))
    post(KIND_STAT, ("高概率在用", f"{len(likely)} 个"))
    post(KIND_STAT, ("仅 ICMP 响应", f"{len(icmp_only)} 个"))
    post(KIND_STAT, ("扫描耗时", f"{el:.1f}s"))

    if not found:
        post(KIND_ERROR, f"{total} 个地址都没有任何响应。"
                         f"可能是网段写错了、或者整段被防火墙隔离。")
        return

    # 列宽按**显示宽度**给：输出区是等宽字体，一个汉字占两列，
    # 用 f-string 的 <8 会按 len(字符数) 补空格，中文标签照样把
    # 整张表挤歪。
    header = (_pad("状态", 14) + _pad("IP 地址", 17) + _pad("MAC 地址", 20)
              + _pad("依据", 12) + "备注")
    for group, title in ((confirmed, "确定在用"),
                         (likely, "高概率在用"),
                         (icmp_only, "仅响应 ICMP")):
        if not group:
            continue
        group = sorted(group, key=lambda h: ipmath.ip_to_int(h.ip))
        post(KIND_LINE, f"\n{'═' * 3} {title}　{len(group)} 个 {'═' * 3}")
        post(KIND_LINE, header)
        for h in group:
            ms = f"  {h.ms:.0f}ms" if h.ms is not None else ""
            post(KIND_LINE, _pad(f"{h.symbol} {h.label}", 14)
                            + _pad(h.ip, 17) + _pad(h.mac, 20)
                            + _pad(h.basis, 12)
                            + (f"{h.ports}{ms}" if h.ports != "-" else ""))

    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)
    post(KIND_STAT, {"kind": "scan_end", "rows": len(found)})

    # ---- 结论 ----
    post(KIND_LINE, "")
    mine = [h for h in confirmed if h.basis == "本机"]
    if confirmed:
        extra = f"（含本机 {len(mine)} 个）" if mine else ""
        post(KIND_LINE, f"✓ {len(confirmed)} 个地址可确认已被占用{extra}。"
                        f"给新设备分配地址时请避开它们。")
    if likely:
        post(KIND_LINE, f"◆ {len(likely)} 个地址开放了常见服务端口，"
                        f"大概率有设备在用，即使它们屏蔽了 ping。")
    idle = total - len(confirmed) - len(likely)
    if icmp_only:
        post(KIND_LINE, f"◐ 另有 {len(icmp_only)} 个地址响应 ping。"
                        f"它们可能开启了防火墙，也可能确实没有设备。")
    if idle > 0:
        post(KIND_LINE, f"○ {idle} 个地址没有任何响应。**这不等于空闲**——"
                        f"Windows 防火墙默认拦截入站 ICMP，"
                        f"很多设备因此 ping 不通但仍在占用地址。")
    post(KIND_LINE, "")
    post(KIND_LINE, "要确认某个具体地址是否被占用，在本工具的"
                    "「端口检测」页单独测那个 IP 的 445 端口最准。")


def _width(s: str) -> int:
    """等宽字体下的显示宽度（汉字算 2 列）。"""
    return sum(2 if ord(c) > 0x2000 else 1 for c in s)


def _pad(s: str, n: int) -> str:
    """按显示宽度左对齐补空格。"""
    return s + " " * max(0, n - _width(s))


def _wake_neighbors(net: ipaddress.IPv4Network,
                    me: set[str]) -> list[str]:
    """挑几个地址先 ping 一下，促使系统与网段内设备建立 ARP 条目。

    ARP 表是**被动**的——只有本机刚和某设备通信过才有一条目。直接
    扫的话表里可能只有网关，扫出来的「在用设备」少得离谱。先 ping
    网关和广播地址能让系统主动与网段内设备握手，ARP 表才完整。
    """
    seeds: list[str] = []
    # 广播地址：ping 它会让系统与全网段交换 ARP
    bcast = str(net.broadcast_address)
    if bcast not in me:
        seeds.append(bcast)
    # 第一个和最后一个可用地址：覆盖范围两端
    first = int(net.network_address) + 1
    last = int(net.broadcast_address) - 1
    for host in (first, last):
        if net.prefixlen < 31:
            ip = ipmath.int_to_ip(host)
            if ip not in me:
                seeds.append(ip)
    return seeds[:3]
