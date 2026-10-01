"""
探测功能实现。

每个函数签名都是 ``fn(post, ...)``，post 是 ``(kind, payload) -> None``
的回调。它们全部是阻塞的，由 core.runner 放到后台线程执行。
"""
from __future__ import annotations

import re
import socket
import time
import urllib.request
from typing import Iterable

from .encoding import IS_WIN, run
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post


__all__ = [
    "ping", "traceroute", "dns_lookup", "port_scan", "bandwidth_test", "network_info",
    "COMMON_PORTS", "parse_ports", "ping_summary",
]


# ================================================================== #
#  延迟测试
# ================================================================== #
# 延迟解析。
# 关键：Windows 的 ping 会按系统语言输出，必须同时匹配中英文，
# 而且 RTT 可能写成 "<1ms"（小于 1 毫秒），不能只认 "time=12ms"。
_RE_RTT = re.compile(
    r"(?:time|时间)\s*[=<≤]\s*(\d+)\s*ms"      # 英文: time=12ms / time<1ms
    r"|(?<=时间)[=<≤]\s*(\d+)"                    # 中文: 时间<1ms / 时间=12
    ,
    re.I,
)


def _first_int(m: re.Match) -> int:
    """正则有两个捕获组，取第一个非空的那个。"""
    for g in m.groups():
        if g is not None:
            return int(g)
    raise ValueError("no capture")


def ping(post: Post, host: str, count: int = 4, timeout_ms: int = 1500) -> None:
    """ICMP 延迟测试。

    注意：ICMP 常被企业网络 / VPN / 容器网络在协议层拦截，
    「ping 不通但 HTTPS 正常」是正常现象，不等于网络故障。
    """
    if not host:
        post(KIND_ERROR, "请填写目标主机")
        return

    flag = "-n" if IS_WIN else "-c"
    wait = ["-w", str(timeout_ms)] if IS_WIN else ["-W", str(max(1, timeout_ms // 1000))]
    rtts: list[int] = []

    def on_line(line: str) -> None:
        if line:
            post(KIND_LINE, line)
        m = _RE_RTT.search(line)
        if m:
            rtts.append(_first_int(m))

    run(["ping", flag, str(max(1, count)), *wait, host], on_line)

    if rtts:
        post(KIND_STAT, ping_summary(rtts, max(1, count)))
    else:
        post(KIND_STAT, ping_summary([], max(1, count), all_failed=True))


def ping_summary(
    rtts: list[int], sent: int, *, all_failed: bool = False
) -> dict:
    """把 RTT 列表汇总成 UI 直接可用的字典。"""
    recv = len(rtts)
    loss = 0.0 if not sent else (sent - recv) * 100.0 / sent
    out = {
        "sent": sent,
        "recv": recv,
        "loss": loss,
        "min": min(rtts) if rtts else None,
        "avg": (sum(rtts) / len(rtts)) if rtts else None,
        "max": max(rtts) if rtts else None,
        "jitter": _jitter(rtts),
        "all_failed": all_failed,
    }
    return out


def _jitter(rtts: list[int]) -> float | None:
    """相邻 RTT 差的平均。抖动大说明链路不稳定。"""
    if len(rtts) < 2:
        return None
    diffs = [abs(b - a) for a, b in zip(rtts, rtts[1:])]
    return sum(diffs) / len(diffs)


# ================================================================== #
#  路由追踪
# ================================================================== #
def traceroute(post: Post, host: str, max_hops: int = 30) -> None:
    """逐跳追踪到目标主机。

    禁用反向 DNS 解析（Windows ``-d`` / Linux ``-n``）：否则每一跳都要
    等 DNS，最坏情况能拖到几分钟。
    """
    if not host:
        post(KIND_ERROR, "请填写目标主机")
        return
    cmd = (
        ["tracert", "-d", "-w", "1000", "-h", str(max_hops), host]
        if IS_WIN
        else ["traceroute", "-n", "-w", "1", "-m", str(max_hops), host]
    )
    run(cmd, lambda line: post(KIND_LINE, line) if line else None)


# ================================================================== #
#  DNS 解析
# ================================================================== #
def dns_lookup(post: Post, host: str) -> None:
    """系统 DNS 查询 + 本机解析耗时。"""
    if not host:
        post(KIND_ERROR, "请填写要查询的域名")
        return

    run(["nslookup", host], lambda line: post(KIND_LINE, line))

    # 系统工具之外，用 socket 再解析一次并计时——这反映的是
    # 「本机到 DNS 服务器」的往返时间，比 nslookup 的整体输出更有参考价值。
    try:
        t0 = time.perf_counter()
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        elapsed = (time.perf_counter() - t0) * 1000
        addrs = sorted({i[4][0] for i in infos})
        post(KIND_STAT, {
            "elapsed_ms": elapsed,
            "addresses": addrs,
            "count": len(addrs),
        })
    except socket.gaierror as e:
        post(KIND_ERROR, f"本机解析失败：{e}")
    except Exception as e:  # noqa: BLE001
        post(KIND_ERROR, f"本机解析异常：{e}")


# ================================================================== #
#  端口检测
# ================================================================== #
#: 常见服务端口。顺序即展示顺序，按「越常用越靠前」排列。
COMMON_PORTS: tuple[tuple[str, int], ...] = (
    ("HTTP", 80),
    ("HTTPS", 443),
    ("RDP", 3389),
    ("SSH", 22),
    ("DNS", 53),
    ("SMB", 445),
    ("MySQL", 3306),
    ("PostgreSQL", 5432),
    ("Redis", 6379),
    ("MongoDB", 27017),
    ("MSSQL", 1433),
    ("SMTP", 25),
    ("IMAP", 143),
    ("POP3", 110),
    ("FTP", 21),
    ("HTTP-Alt", 8080),
    ("提交端口", 587),
    ("IMAPS", 993),
    ("POP3S", 995),
)


def parse_ports(text: str) -> list[tuple[str, int]]:
    """解析用户输入的端口列表。支持 ``80,443,8000-8010``。"""
    text = text.replace("，", ",").replace("、", ",").replace(" ", "")
    if not text:
        return list(COMMON_PORTS)

    out: list[tuple[str, int]] = []
    seen: set[int] = set()
    for piece in text.split(","):
        if not piece:
            continue
        if "-" in piece:
            lo_s, _, hi_s = piece.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError as exc:
                raise ValueError(f"「{piece}」不是合法的端口范围") from exc
            # lo <= hi 而不是 lo < hi：`80-80` 表达的是「只扫 80
            # 这个端口」，是合法写法。用 `lo < hi` 会把它判成非法，
            # 而错误信息写「超出 1-65535」——80 明明在范围内，
            # 用户完全看不懂哪里错了。
            if not (0 < lo <= hi < 65536):
                raise ValueError(f"端口范围「{piece}」不合法，应为 1-65535 且起点不大于终点")
            for p in range(lo, hi + 1):
                if p not in seen:
                    seen.add(p)
                    out.append((str(p), p))
        else:
            try:
                p = int(piece)
            except ValueError as exc:
                raise ValueError(f"「{piece}」不是合法端口号") from exc
            if not 0 < p < 65536:
                raise ValueError(f"端口号 {p} 超出 1-65535")
            if p not in seen:
                seen.add(p)
                out.append((piece, p))
    if not out:
        raise ValueError("没有解析出任何有效端口")
    return out


def port_scan(
    post: Post, host: str, ports: Iterable[tuple[str, int]], timeout: float = 3.0
) -> None:
    """TCP 连接探测。只发 SYN，不传输任何数据。

    区分三种失败：「拒绝」说明主机可达但端口没开；「超时」说明
    被防火墙静默丢弃；这两者含义完全不同，混为一谈会误导诊断。
    """
    if not host:
        post(KIND_ERROR, "请填写目标主机")
        return

    try:
        ip = socket.gethostbyname(host)
    except Exception:  # noqa: BLE001 - 解析失败就用原样主机名，socket 还能再试
        ip = host

    # 刻意先物化成 list 再往下走：签名是 Iterable，而 len(list(x))
    # 会**消耗掉生成器**，传 iter([...]) 的话 total 算得出、后面却
    # 一个端口都扫不到（实测 total=2 但结果为 0 条）。
    port_list = list(ports)
    post(KIND_STAT, {"kind": "scan_begin", "host": host, "ip": ip,
                     "total": len(port_list)})

    for label, port in port_list:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        t0 = time.perf_counter()
        try:
            sock.connect((ip, port))
            ms = (time.perf_counter() - t0) * 1000
            post(KIND_STAT, {
                "kind": "port", "port": port, "label": label,
                "state": "open", "ms": ms,
            })
        except socket.timeout:
            post(KIND_STAT, {
                "kind": "port", "port": port, "label": label,
                "state": "filtered", "ms": None,
            })
        except ConnectionRefusedError:
            post(KIND_STAT, {
                "kind": "port", "port": port, "label": label,
                "state": "closed", "ms": None,
            })
        except OSError as e:
            post(KIND_STAT, {
                "kind": "port", "port": port, "label": label,
                "state": "error", "ms": None, "error": e.strerror or str(e),
            })
        finally:
            sock.close()
        # 心跳：让 UI 逐行刷新，而不是等全部扫完
        post(KIND_LINE, "")


# ================================================================== #
#  带宽测速
# ================================================================== #
#: 公开的测速文件。按可靠性排序，第一个失败自动降级。
#: 只发起普通 HTTP GET，不下载整站。
#: 公开名：UI 层要列出可选源，不该碰私有名
#: 测速源 (名称, URL, 最小有效字节数)。
#:
#: **加入前均已验证**：发 1MB Range 请求，确认返回 206 或完整
#: 1KB 以上。镜像站的可用性变化很快——ISO 版本号一变就 404，
#: 镜像站对非浏览器 UA 可能返回 403，部分老牌测速站的文件已被
#: 撤下。因此只保留验证通过的，宁少勿假。
#:
#: 国内源排在前面：主要用户在国内，先试境内才准。
#: 境外源是给「想知道出国链路多快」用的，不是默认。
SPEED_SOURCES: tuple[tuple[str, str, int], ...] = (
    # ---- 国内（2026-09-29 逐个实测通过）----
    ("华为云 Ubuntu", "https://repo.huaweicloud.com/ubuntu-releases/24.04/"
                      "ubuntu-24.04.5.1-desktop-amd64.iso",            6_000_000),
    ("网易 Ubuntu",   "https://mirrors.163.com/ubuntu-releases/24.04/"
                      "ubuntu-24.04.5.1-desktop-amd64.iso",            6_000_000),
    ("华为云 Arch",   "https://repo.huaweicloud.com/archlinux/iso/latest/"
                      "archlinux-x86_64.iso",                           5_000_000),
    ("网易 Arch",     "https://mirrors.163.com/archlinux/iso/latest/"
                      "archlinux-x86_64.iso",                           5_000_000),
    # ---- 境外（实测通过）----
    ("Cloudflare", "https://speed.cloudflare.com/__down?bytes=50000000",  2_000_000),
    ("OVH 法国",   "https://proof.ovh.net/files/10Mb.dat",               2_000_000),
)

_MIN_BYTES = 500_000      # 少于这个量级的结果没有参考价值
_PROGRESS_EVERY = 1.0     # 进度回显间隔（秒）
#: 单次读取的块大小
_CHUNK = 65_536
#: 向服务器请求的数据上限。**必须给得够宽**，否则时长白设：
#: 原来这个值就是停止阈值（30 MB），服务器据此只回 30 MB，
#: 选「30 秒」时读两秒就 EOF 收工。改成按时长停止后，
#: 这个值只当流量护栏——按「最长时长 × 乐观速率」给，取 600 MB
#: （60 秒 × 80 Mbps 是极端情况下的余量，家用宽带远用不到）。
#: 服务器不支持 Range 时会忽略它并从头给完整文件，循环仍按时长停。
_RANGE_CAP = 600_000_000
#: 测速最短持续时间（秒）。即使时长选得很短，也要跑满这么多——
#: TCP 慢启动前几百毫秒的速率不代表稳态带宽，1 秒的测速结果偏低
#: 得离谱。
_MIN_SECONDS = 2.0
#: 供界面选择测速时长。**按时间而不是按流量**。
#:
#: 原来固定下载 30 MB 就收工，问题是它在快链路上根本等不到收敛：
#: 千兆链路 30 MB 只要 0.24 秒，那点时间 TCP 慢启动还没完成，算出来
#: 明显偏低。而慢链路上 30 MB 要几十秒，用户干等。定流量两头不讨好。
#:
#: 按时长才对：用户知道自己愿意等多久，而**只要跑够几秒，慢启动
#: 必然收敛**，结果与链路过快无关。
#:
#: 停止条件因此只看时间，不再设总量上限——设了反而麻烦：30 秒在
#: 10 G 链路上是 37 GB，任何固定字节上限都必然在「太短」和
#: 「白下几十 GB」之间二选一。
TIME_CHOICES: tuple[int, ...] = (5, 10, 15, 30, 60)
_DEFAULT_SECONDS = 10


def _is_html(chunk: bytes) -> bool:
    """判断响应是不是 HTML 错误页。

    不能只看首字节是不是 ``<``——ISO 和 bin 文件的头部完全可能是
    任意字节，0x3C 就会误判。所以要匹配真实 HTML 的开头特征：
    忽略前导空白后是 ``<!doctype``、``<html`` 或 ``<?xml``。
    """
    head = chunk[:200].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html", b"<?xml"))


def bandwidth_test(post: Post, only: str | None = None,
                   seconds: int | None = None) -> None:
    """下载测速。

    ``only`` 指定只测某一个源（用户在上方选了源）。为 None 时按
    SPEED_SOURCES 顺序逐个尝试，前一个失败自动降级到下一个——
    公共测速源经常挂，这是必要的兜底。

    ``seconds`` 是用户选的测速时长（秒）。为 None 时用
    _DEFAULT_SECONDS。**时长是主判据**：跑够这么久 TCP 慢启动一定
    已经收敛，算出来的速率与链路过快无关。原来固定 30 MB 收工，
    千兆链路上只够 0.24 秒，结果明显偏低。
    """
    sources = SPEED_SOURCES
    if only:
        picked = [t for t in SPEED_SOURCES if t[0] == only]
        if not picked:
            post(KIND_ERROR, f"未知的测速源：{only}")
            return
        # 指定了源，就别偷偷降级到别的源去——那测出来的速度和
        # 指定的源对不上，数字没有意义。
        sources = tuple(picked)

    for name, url, expected in sources:
        post(KIND_LINE, f"测试源：{name}")
        try:
            # Range 头请服务器最多给 _RANGE_CAP 字节。
            #
            # **它是护栏，不是停止条件。** 停止条件是时间（下面
            # target 那行）；这个数字只防「服务器不管你只要多少、
            # 一直发到天荒地老」。取 600MB 是按最坏情况算的：
            # 60 秒 × 1Gbps 满速 = 7.5GB，但那段以太网/5GHz 无线
            # 实际上不了 1Gbps，取 600MB 相当于 800Mbps——
            # 真跑到这个量级，护栏早就该比时长先生效，那说明链路
            # 有问题，测出来的数也不会准。
            #
            # 源不支持 Range（返回 200 而不是 206）也能用：
            # 那就是真的一路下到底，靠时间停。
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Range": f"bytes=0-{_RANGE_CAP - 1}",
            })
            t0 = time.perf_counter()
            total = 0
            last_report = 0.0
            _looks_like_html = False
            with urllib.request.urlopen(req, timeout=25) as resp:
                # **只按时间停。** 时长取用户选的，但不少于
                # _MIN_SECONDS——选 5 秒也要跑满，慢启动没收敛的数字
                # 偏低得离谱，那还不如不测。
                target = max(_MIN_SECONDS, float(seconds or _DEFAULT_SECONDS))
                while (now_dt := time.perf_counter() - t0) < target:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    if not _looks_like_html and _is_html(chunk):
                        _looks_like_html = True
                    total += len(chunk)
                    now = time.perf_counter()
                    if now - last_report >= _PROGRESS_EVERY:
                        last_report = now
                        mbps = total * 8 / max(now - t0, 1e-6) / 1e6
                        post(KIND_STAT, {
                            "kind": "speed_progress", "source": name,
                            "bytes": total, "mbps": mbps, "elapsed": now - t0,
                        })
            dt = time.perf_counter() - t0
            # 有些镜像站对非浏览器请求返回 HTML 错误页。200 + 几百 KB
            # 文本在测速里表现为「超快」，实际毫无意义——而且比源挂掉
            # 更坑：用户会以为自己的宽带真的有那么快。
            if _looks_like_html:
                post(KIND_LINE, "该源返回了网页而不是数据文件（多半触发了它的"
                                "访问限制），换下一个源")
                continue
            if total >= _MIN_BYTES and dt > 0:
                mbps = total * 8 / dt / 1e6
                post(KIND_STAT, {
                    "kind": "speed_done", "source": name, "bytes": total,
                    "elapsed": dt, "mbps": mbps, "mbps_up": None,
                })
                return
            post(KIND_LINE, f"数据量过少（{total} 字节），结果不可信，换下一个源")
        except Exception as e:  # noqa: BLE001
            post(KIND_LINE, f"该源不可用：{e}")

    if only:
        post(KIND_ERROR, f"测速源 {only} 不可用。可以换个源再试。")
    else:
        post(KIND_ERROR, "所有测速源都失败了。可能原因：网络不通、DNS 异常，"
                         "或这些公共源被限制访问。")


# ================================================================== #
#  网络信息
# ================================================================== #
_ADAPTER_KEYS = (
    "适配器", "adapter", "IPv4", "IPv6 地址", "Default Gateway", "默认网关",
    "DNS Servers", "DNS 服务器", "Subnet Mask", "子网掩码",
    "MAC Address", "物理地址", "Media State", "媒体状态",
)

_IP_LOOKUPS = (
    ("Cloudflare", "https://api.ipify.org?format=json"),
    ("ipinfo.io", "https://ipinfo.io/json"),
)


def network_info(post: Post) -> None:
    """本机网络全貌：适配器、出口 IP、当前连接。"""
    post(KIND_LINE, "═══ 网络适配器 ═══")
    seen: list[str] = []
    try:
        def on_ipconfig(line: str) -> None:
            if line and any(k in line for k in _ADAPTER_KEYS):
                seen.append(line)
                post(KIND_LINE, line)
        run(["ipconfig", "/all"] if IS_WIN else ["ifconfig", "-a"], on_ipconfig)
    except Exception as e:  # noqa: BLE001
        post(KIND_ERROR, f"读取适配器信息失败：{e}")

    post(KIND_LINE, "")
    post(KIND_LINE, "═══ 出口 IP ═══")
    for name, url in _IP_LOOKUPS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                body = r.read(2000).decode("utf-8", "replace")
            post(KIND_LINE, f"{name}：{body}")
            break
        except Exception as e:  # noqa: BLE001
            post(KIND_LINE, f"{name} 查询失败：{e}")

    post(KIND_LINE, "")
    post(KIND_LINE, "═══ 当前 TCP 连接 ═══")
    count = 0
    try:
        def on_netstat(line: str) -> None:
            nonlocal count
            if "ESTABLISHED" in line:
                count += 1
                if count <= 30:
                    parts = line.split()
                    post(KIND_LINE, "  " + "  ".join(parts[:4]))
        run(["netstat", "-ano"] if IS_WIN else ["netstat", "-tn"], on_netstat)
    except Exception as e:  # noqa: BLE001
        post(KIND_ERROR, f"读取连接列表失败：{e}")
    if count == 0:
        post(KIND_LINE, "  （没有已建立的连接）")
    elif count > 30:
        post(KIND_LINE, f"  …… 共 {count} 条，只显示前 30 条")
