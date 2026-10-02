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
    "ping", "tcping", "traceroute", "dns_lookup", "port_scan",
    "bandwidth_test", "network_info",
    "COMMON_PORTS", "parse_ports", "ping_summary",
    "port_owners", "parse_netstat_listen",
    "loss_probe",
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


# ================================================================== #
#  TCP 延迟（tcping）
# ================================================================== #
def tcping(post: Post, host: str, port: int = 443, count: int = 4,
           timeout: float = 3.0) -> None:
    """TCP 握手延迟测试（tcping）。

    为什么要有这个：ICMP 常被企业网络 / VPN / 云主机在协议层丢弃，
    「ping 不通但网页能开」是常态，icmp 测不出这类网络到底快不快。
    TCP 握手走的是真实业务路径（同样的端口、同样的中间设备），
    ICMP 被封时它照样测得出延迟。

    「端口通不通」和「延迟多少」是两件事，所以这里逐次报告状态：
    一次 connect() 成功说明该次握手成功，耗时即 RTT（不含应用层）。
    """
    if not host:
        post(KIND_ERROR, "请填写目标主机")
        return

    name = host.strip()
    try:
        ip = socket.gethostbyname(name)
    except socket.gaierror as e:
        post(KIND_ERROR, f"DNS 解析失败：{name} -> {e.strerror or e}")
        return

    post(KIND_LINE, f"目标：{name}:{port}  ({ip})  TCP 握手 {count} 次")
    post(KIND_LINE, "")

    rtts: list[int] = []
    for i in range(1, max(1, count) + 1):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                ms = (time.perf_counter() - t0) * 1000
        except OSError as e:
            # 超时和「连接被拒」含义完全不同：前者多半是丢包/被过滤，
            # 后者是端口没开或被防火墙 RST。分开说，不然用户会误判。
            reason = "超时" if isinstance(e, socket.timeout) else \
                f"连接失败（{type(e).__name__}）"
            post(KIND_LINE, f"  第 {i} 次  {reason}"
                            f"  {port} 端口未响应")
            continue

        ms_i = max(0, int(round(ms)))
        rtts.append(ms_i)
        post(KIND_LINE, f"  第 {i} 次  握手成功  {ms_i} ms")

    if rtts:
        post(KIND_STAT, ping_summary(rtts, max(1, count)))
    else:
        post(KIND_STAT, ping_summary([], max(1, count), all_failed=True))
        post(KIND_LINE, "")
        post(KIND_LINE, f"{count} 次都没能完成 TCP 握手。"
                        f"可能是端口没开、被防火墙拦，或该地址不回应此端口。")


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


# ================================================================== #
#  端口占用
# ================================================================== #
# netstat -ano 的输出形如：
#   TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    1234
# 本地地址和外部地址都可能带 IPv6 的 [::]:8080，解析时要一起处理。
_RE_NETSTAT_ROW = re.compile(
    r"^\s*(TCP|UDP)\s+(\S+):(\d+)\s+(\S+)\s*"
    r"(?:(\S+)\s+)?(\d+)\s*$"
)


def parse_netstat_listen(rows: Iterable[str]) -> list[dict]:
    """从 netstat -ano 的行里抽出「本机正在监听」的条目。

    只留 LISTENING / 侦听 那类——已建立的连接占了同一个端口但不代表
    「有人占着这个端口不让别人绑」，用户问的永远是后者。
    """
    out: list[dict] = []
    for line in rows:
        m = _RE_NETSTAT_ROW.match(line or "")
        if not m:
            continue
        proto, local, port, _remote, state, pid = m.groups()
        # state 为空说明是 UDP——UDP 没有「监听」这个状态，netstat 直接
        # 把状态列留空。这里把「有状态」当成「已建立的连接」，那种不是
        # 端口被占用（同一端口被多条连接共用是正常的）。
        if state:
            if state.upper() not in ("LISTENING", "侦听"):
                continue
            listening = True
        else:
            listening = True          # UDP 行：没有状态列即为占用
        out.append({
            "proto": proto.lower(),
            "local": local,
            "port": int(port),
            "pid": int(pid),
            "listen": listening,
        })
    return out


def _pid_to_name(pid: int) -> tuple[str, str]:
    """PID -> (进程名, 可执行文件路径)。取不到就返回占位。

    tasklist 输出是 GBK 的表格，用 -FO CSV 拿干净字段；FO 参数在旧版
    Windows 上不支持，所以失败时退回按行匹配。
    """
    try:
        out: list[str] = []
        run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            out.append)
        for line in out:
            if not line.strip() or line.startswith("信息") or "INFO" in line:
                continue
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2 and parts[1] == str(pid):
                name = parts[0]
                return name, name
    except Exception:  # noqa: BLE001
        pass
    return f"PID {pid}", ""


def port_owners(post: Post, port: int | None = None,
                listening_only: bool = True) -> None:
    """谁占着这个端口。

    ``port`` 给定时只报这一个端口；给 None 时列出全部监听端口。
    """
    rows: list[str] = []
    try:
        if IS_WIN:
            run(["netstat", "-ano"], rows.append)
        else:
            # 非 Windows 没有 -o（拿不到 PID），退回只看端口
            run(["netstat", "-tuln"], rows.append)
    except Exception as e:  # noqa: BLE001
        post(KIND_ERROR, f"读取端口列表失败：{e}")
        return

    entries = parse_netstat_listen(rows)
    if port is not None:
        entries = [e for e in entries if e["port"] == port]

    if not entries:
        if port is not None:
            post(KIND_STAT, {"found": 0, "port": port})
            post(KIND_LINE, f"端口 {port} 当前没有任何进程监听。")
            post(KIND_LINE, "「可以绑定」的意思是：这个端口现在没被占。")
            post(KIND_LINE, "如果你启动服务时仍报「地址已在使用」，"
                            "可能是 UDP 也被占了，或者占用进程刚好退出。")
        else:
            post(KIND_STAT, {"found": 0})
            post(KIND_LINE, "没有正在监听的端口。")
        return

    # 同一个端口可能被 IPv4 和 IPv6 各占一条，去重后按端口号排
    merged: dict[tuple[str, int], dict] = {}
    for e in entries:
        key = (e["proto"], e["port"])
        if key not in merged or e["listen"]:
            merged[key] = e
    entries = sorted(merged.values(), key=lambda x: (x["port"], x["proto"]))

    post(KIND_STAT, {"found": len(entries)})
    post(KIND_LINE, f"共{len(entries)} 个监听条目"
                    + (f"（端口 {port}）" if port is not None else ""))
    post(KIND_LINE, "")

    # 系统进程（PID 4是 System、0-1000 多为系统）单独标出来，
    # 因为这类进程用户通常没法也不该去结束它。
    for e in entries:
        name, path = _pid_to_name(e["pid"])
        sys_proc = e["pid"] in (0, 4) or e["pid"] < 1000
        mark = "  [系统]" if sys_proc else ""
        # netstat 的本地地址带端口后缀，要先剥掉。
        # IPv6 是 ``[::]:8080`` 这种带方括号的写法，rsplit(":") 会把
        # ``[::]`` 切成 ``[::``，所以要按右方括号判断而不是找最后一个冒号。
        addr = e["local"]
        if addr.endswith(f":{e['port']}"):
            addr = addr[: -len(str(e["port"])) - 1]
        addr = addr.strip("[]")

        # 地址语义化：[::] / 0.0.0.0 是「所有网卡」，127.x 是「只对本机」，
        # 其它具体地址才是「只对这台机器的该网卡开放」。写成中文，
        # 因为面向的是不知道 [::] 是什么意思的普通用户。
        if addr in ("0.0.0.0", "*", "::", ""):
            scope = "所有网卡（局域网内其它设备也能连）"
        elif addr.startswith("127.") or addr == "::1":
            scope = "只对本机开放"
        else:
            scope = "只对这台机器开放"
        fam = "IPv6" if ":" in addr else "IPv4"

        post(KIND_LINE,
             f"  {e['port']:>6} /{e['proto']:<4} PID {e['pid']:<7} {name}{mark}")
        post(KIND_LINE, f"         {fam} {addr} —— {scope}")
        if sys_proc:
            post(KIND_LINE, f"         系统进程，结束它需要管理员权限且可能"
                            f"影响系统功能，不建议手动处理")

    if port is not None and entries:
        post(KIND_LINE, "")
        pids = {e["pid"] for e in entries}
        if len(pids) == 1:
            pid = pids.pop()
            post(KIND_LINE, f"端口 {port} 被 PID {pid} 占用。")
            post(KIND_LINE, f"要腾出这个端口，先关掉那个程序；"
                            f"确实需要强制结束可以在任务管理器里找 PID {pid}。")
        else:
            post(KIND_LINE, f"端口 {port} 上有多个协议在监听（IPv4/IPv6 各一条"
                            f"通常是同一个进程），不代表有多个程序抢。")


# ================================================================== #
#  持续丢包 / 抖动定位
# ================================================================== #
#: 单次 ping 一发一收就能知道 RTT，但**丢包必须逐次发包**才看得见：
#: ping -n 20 是把 20 个包一口气发出去，回来的行和发出去的包**没有
#: 可靠的对应关系**（超时的那次 ping 根本不打印任何行）。所以这里每次
#: 只发一个包、拿到结果再发下一个——慢一点，但换来「第 7 次丢了」这种
#: 能直接定位的信息。
_LOSS_REPLY = re.compile(r"(TTL|ttl)=", re.I)
_LOSS_STATS = re.compile(r"已发送| Sent|Sending|发送")


def _one_icmp(host: str, timeout_ms: int) -> tuple[int | None, str]:
    """发一个 ICMP 包。返回 (RTT 毫秒, 未回复原因)。

    RTT 为 None 就是丢了。Windows 的 ping 每次都会先打印
    「正在 Ping ...」再逐行回复；不发包就没法知道是「超时」还是
    「目标不可达」，所以这里靠回复行里有没有 TTL= 判断。
    """
    flag = "-n" if IS_WIN else "-c"
    wait = ["-w", str(timeout_ms)] if IS_WIN else ["-W", str(max(1, timeout_ms // 1000))]
    lines: list[str] = []
    run(["ping", flag, "1", *wait, host], lines.append)

    for line in lines:
        m = _RE_RTT.search(line)
        if m:
            return _first_int(m), ""
    # 没收到 RTT —— 判断原因
    joined = "\n".join(lines)
    if any(k in joined for k in ("unreachable", "不可达", "无法访问",
                                 "Destination host", "TTL expired", "超时")):
        if "TTL expired" in joined or "TTL 已超时" in joined:
            return None, "TTL 超时（超过跳数限制）"
        return None, "目标不可达"
    if any(k in joined for k in ("unrecognized", "invalid", "无法解析",
                                 "not found", "Non-existent")):
        return None, "主机名无法解析"
    if any(k in joined for k in ("100% 丢包", "100% loss", "0 received",
                                 "请求超时", "timed out")):
        return None, "请求超时"
    return None, "无响应"


def _one_tcp(host: str, port: int, timeout: float) -> tuple[int | None, str]:
    """一次 TCP 握手。返回 (耗时毫秒, 失败原因)。"""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return int(round((time.perf_counter() - t0) * 1000)), ""
    except socket.timeout:
        return None, "连接超时"
    except ConnectionRefusedError:
        return None, "连接被拒绝（端口没开）"
    except OSError as e:
        return None, e.strerror or str(e)


def loss_probe(post: Post, host: str, *, count: int = 30,
               interval: float = 0.5, timeout_ms: int = 1500,
               mode: str = "icmp", port: int = 443) -> None:
    """持续采样，定位丢包发生在哪几次、抖动有多大。

    为什么不是 ``ping -n 30`` 一把梭：ping 打包发包时，超时的那一次
    **不产生任何输出**，事后无法判断是哪几次丢的。视频会议卡顿、
    游戏跳 ping 这类问题问的恰恰是「第几分钟丢了一次」——必须逐次采样。

    采样期间 post 一条 KIND_PROGRESS 之外的普通行做进度提示，UI 那边
    逐条追加，所以用户能看到它在动，不是卡住。

    ``mode`` 为 ``"tcp"`` 时用 TCP 握手测——ICMP 被封的环境（企业网、
    云主机、部分校园网）里 ICMP 全丢，但业务正常，用 TCP 才测得准。
    """
    if not host:
        post(KIND_ERROR, "请填写目标主机")
        return
    count = max(1, min(count, 200))
    name = host.strip()

    if mode == "tcp":
        try:
            ip = socket.gethostbyname(name)
        except socket.gaierror as e:
            post(KIND_ERROR, f"DNS 解析失败：{name} -> {e.strerror or e}")
            return
        post(KIND_LINE, f"目标：{name}:{port}  ({ip})")
        post(KIND_LINE, f"TCP 握手采样 {count} 次，间隔 {interval:g} 秒，"
                        f"单次超时 {timeout_ms / 1000:g} 秒")
    else:
        post(KIND_LINE, f"目标：{name}")
        post(KIND_LINE, f"ICMP 采样 {count} 次，间隔 {interval:g} 秒，"
                        f"单次超时 {timeout_ms / 1000:g} 秒")
    post(KIND_LINE, "")

    samples: list[dict] = []          # 逐次结果，最后交给 UI 画图
    for i in range(count):
        if mode == "tcp":
            rtt, why = _one_tcp(name, port, timeout_ms / 1000.0)
        else:
            rtt, why = _one_icmp(name, timeout_ms)

        samples.append({
            "index": i + 1,
            "ok": rtt is not None,
            "rtt": rtt,
            "reason": why,
        })
        if rtt is not None:
            post(KIND_LINE, f"  {i + 1:>3} / {count}   {rtt:>5} ms")
        else:
            post(KIND_LINE, f"  {i + 1:>3} / {count}    ——   丢包（{why}）")

        # 最后一次之后不用再等
        if i < count - 1:
            time.sleep(interval)

    rtts = [x["rtt"] for x in samples if x["ok"]]
    lost = [x["index"] for x in samples if not x["ok"]]
    stat = ping_summary(rtts, count)
    stat["samples"] = samples
    stat["lost_at"] = lost
    post(KIND_STAT, stat)

    _loss_verdict(post, stat, lost, samples, mode)


def _loss_verdict(post: Post, stat: dict, lost: list[int],
                  samples: list[dict], mode: str) -> None:
    """把数字翻译成结论。用户问的是「我网络有没有问题」。

    判定的关键是看**丢包的形状**，不是只看丢包率：

    · 全部超时 + ICMP  → 先排除「ICMP 被协议层拦截」，这是最常见的
      误判来源，直接归因到硬件会把人带偏。
    · 成段连续丢      → 链路或设备故障，重试无用。
    · 零散丢          → 无线干扰 / 接触不良，是常态。
    """
    post(KIND_LINE, "")
    post(KIND_LINE, "═══ 结论 ═══")

    sent = stat["sent"]
    loss = stat["loss"]
    if sent == 0:
        return

    all_lost = len(lost) == sent

    if not lost:
        post(KIND_LINE, f"  ✓ {sent} 次采样全部成功，没有丢包。")
    elif all_lost:
        post(KIND_LINE, f"  ✗ {sent} 次采样全部无响应")
    else:
        post(KIND_LINE, f"  ⚠ 丢包 {loss:.1f}%（{len(lost)}/{sent} 次）")

    # ---- 丢包的「形状」比丢包率更能说明问题 ----
    if lost:
        runs = _loss_runs(lost)

        if all_lost:
            # 全丢的成因和「部分丢」完全不同：ICMP 被协议层拦掉时，
            # 表现就是 100% 超时，但它和网线坏了的现象**一模一样**。
            # 所以先给一条能立刻自查的判断，再谈硬件。
            if mode == "icmp":
                post(KIND_LINE, "  先别急着换硬件——ICMP 被协议层拦截时，"
                                "现象和断网完全一样。")
                post(KIND_LINE, "      企业网、云主机、容器网络经常在协议层"
                                "丢掉 ping 包。")
                post(KIND_LINE, "      把上面的方式切到「TCP 握手」再测一次："
                                "走真实业务路径，ICMP 被封也能测出结果。")
                post(KIND_LINE, "      如果 TCP 握手也全丢，才是真的断了——"
                                "这时才该去查网线、网口、路由器。")
            else:
                post(KIND_LINE, "  TCP 握手也全部失败，说明目标端口确实不通。")
                post(KIND_LINE, "      可能是服务没开、端口被防火墙拦截、"
                                "或者目标真的宕机了。先换一个端口试。")
        else:
            longest = max(runs, key=lambda r: r[1] - r[0])
            span = longest[1] - longest[0] + 1
            if span >= 3:
                post(KIND_LINE, f"  丢包成段出现（第 {longest[0]}–{longest[1]} 次"
                                f"连续丢 {span} 次）")
                post(KIND_LINE, "      成段丢包通常不是随机干扰，而是某一段链路"
                                "或某个设备出了问题：网线/网口接触不良、"
                                "交换机端口、光模块、或者上游运营商的这段线路。")
                post(KIND_LINE, "      随机重试解决不了，需要换硬件或找运营商。")
            else:
                shown = "、".join(map(str, lost[:12]))
                post(KIND_LINE, f"  丢包零散出现在第 {shown} 次"
                                + ("…" if len(lost) > 12 else ""))
                post(KIND_LINE, "      零散丢一两包在无线网络和家用路由器上是常态，"
                                "人眼和耳朵基本感知不到。")
                post(KIND_LINE, "      但如果你正在经历视频卡顿或游戏跳 ping，"
                                "那这些零星丢包就是元凶——无线信号弱、"
                                "2.4G 频段被邻居干扰、或网线水晶头接触不良。")

    # ---- 抖动 ----
    if stat.get("jitter") is not None and stat["avg"]:
        j = stat["jitter"]
        if j / stat["avg"] > 0.3:
            post(KIND_LINE, f"  ⚠ 抖动 {j:.1f} ms，相对平均延迟 {stat['avg']:.0f} ms "
                            f"波动很大")
            post(KIND_LINE, "      延迟忽高忽低会让实时应用（会议、游戏、语音）"
                            "明显卡顿，即使平均延迟看起来不差。")
        else:
            post(KIND_LINE, f"  ✓ 抖动 {j:.1f} ms（相对平均 {stat['avg']:.0f} ms "
                            f"算平稳）")

    if mode == "icmp" and all_lost:
        post(KIND_LINE, "")
        post(KIND_LINE, "  补充：如果你确信网页能正常打开，那这就是 ICMP 被封，"
                        "不是网络故障。")


def _loss_runs(lost: list[int]) -> list[tuple[int, int]]:
    """把丢包序号压成连续段：``[3,4,5,9]`` -> ``[(3,5), (9,9)]``。"""
    if not lost:
        return []
    runs: list[tuple[int, int]] = []
    start = prev = lost[0]
    for n in lost[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev))
        start = prev = n
    runs.append((start, prev))
    return runs
