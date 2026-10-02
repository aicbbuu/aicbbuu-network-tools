"""新增探测：HTTP 可用性、路径 MTU、TCP 握手分解、WiFi、局域网。

与 :mod:`probes` 同一套契约：``fn(post, ...)``，阻塞，由 runner
放到后台线程。
"""
from __future__ import annotations

import re
import socket
import ssl
import time
import urllib.error
import urllib.request

from .encoding import IS_WIN, run
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post


__all__ = [
    "http_check", "path_mtu", "tcp_timing", "wifi_info", "lan_info",
    "arp_list", "parse_arp",
    "DEFAULT_HTTP_TARGETS", "grade_signal", "grade_mtu",
]

# 探测用的目标。选国内可达性好的几个，避免用户以为是自己的问题。
#
# UI 上「留空会测哪三个」的那行提示直接读这个常量，不要在页面里再抄
# 一份——抄的那份迟早会和这里对不上，而用户是照着提示文字判断的。
DEFAULT_HTTP_TARGETS: tuple[str, ...] = (
    "www.baidu.com",
    "www.qq.com",
    "www.microsoft.com",
)


def _resolve(host: str, port: int) -> tuple[str, list[str]]:
    """解析主机名。返回 (第一个 IP, 全部去重 IP)。失败抛 gaierror。

    getaddrinfo 的 sockaddr 是 (host, port) 或 (flowinfo, ...) /
    (scope_id, ...) 的变体，第 0 项类型标注是 str|int，必须转 str。
    """
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    uniq = sorted({str(info[4][0]) for info in infos})
    return uniq[0], uniq


# ================================================================== #
#  HTTP 可用性
def http_check(post: Post, host: str = "", timeout: float = 8.0) -> None:
    """分层判断「能不能上网」：DNS -> TCP -> TLS/HTTP。

    为什么要分层
    ------------
    ping 通 != 能上网。最常见的三种「ping 得通但网页打不开」分别是：
    DNS 污染（解析到错误 IP）、TCP 被阻断（443 连不上）、TLS 握手
    失败（中间设备重置连接）。只看 ping 的 RTT 完全区分不出来。

    每层单独计时，出问题时直接告诉用户「卡在哪一层」。
    """
    target = host.strip()
    targets = [target] if target else list(DEFAULT_HTTP_TARGETS)
    post(KIND_LINE, f"目标：{target if target else '内置三个站点'}")
    post(KIND_LINE, "")

    any_ok = False
    for name in targets:
        post(KIND_LINE, f"── {name} " + "─" * max(0, 46 - len(name)))

        # ---- 第 1 层：DNS ----
        t0 = time.perf_counter()
        try:
            ip, uniq = _resolve(name, 443)
        except socket.gaierror as e:
            post(KIND_ERROR, f"DNS 解析失败：{name} -> {e.strerror or e}\n"
                             f"    换 DNS 服务器，或用「DNS 解析」页确认")
            continue
        dns_ms = (time.perf_counter() - t0) * 1000
        post(KIND_STAT, ("DNS 解析", f"{dns_ms:.0f} ms"))
        shown = ", ".join(uniq[:3]) + (" …" if len(uniq) > 3 else "")
        post(KIND_LINE, f"  \u2713 DNS   {dns_ms:6.0f} ms  \u2192  {shown}")

        # ---- 第 2 层：TCP 握手 ----
        t0 = time.perf_counter()
        try:
            with socket.create_connection((ip, 443), timeout=timeout):
                tcp_ms = (time.perf_counter() - t0) * 1000
        except (socket.timeout, OSError) as e:
            post(KIND_ERROR, f"TCP 连接失败：{ip}:443  "
                             f"{type(e).__name__}: {e}\n"
                             f"    端口被阻断或本地防火墙拦截 —— "
                             f"此时 ping 正常也上不了网")
            continue
        post(KIND_STAT, ("TCP 握手", f"{tcp_ms:.0f} ms"))
        post(KIND_LINE, f"  \u2713 TCP   {tcp_ms:6.0f} ms  \u2192  连通")

        # ---- 第 3 层：TLS + HTTP ----
        t0 = time.perf_counter()
        req = urllib.request.Request(
            f"https://{name}/", method="HEAD",
            headers={"User-Agent": "aicbbuu-nettools/2.0"})
        server = ""
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                code, server = r.status, r.headers.get("Server", "")
        except urllib.error.HTTPError as e:
            code, server = e.code, e.headers.get("Server", "")   # 4xx 也算连通
        except (urllib.error.URLError, ssl.SSLError, OSError) as e:
            reason = getattr(e, "reason", e)
            post(KIND_ERROR, f"TLS/HTTP 失败：{url_of(name)}\n"
                             f"    {type(reason).__name__}: {reason}\n"
                             f"    TCP 通但 TLS 不通 —— 通常是中间设备"
                             f"重置连接或证书被劫持")
            continue
        http_ms = (time.perf_counter() - t0) * 1000
        total = dns_ms + tcp_ms + http_ms
        post(KIND_STAT, ("HTTP 响应", f"{http_ms:.0f} ms"))
        post(KIND_STAT, ("总耗时", f"{total:.0f} ms"))
        post(KIND_LINE, f"  \u2713 HTTP  {http_ms:6.0f} ms  \u2192  {code}  {server}".rstrip())
        post(KIND_LINE, f"    合计 {total:.0f} ms（DNS {dns_ms:.0f}"
                        f" + TCP {tcp_ms:.0f} + HTTP {http_ms:.0f}）"
                        f"  {_grade_total(total)}")
        any_ok = True
        post(KIND_LINE, "")

    if not any_ok:
        post(KIND_ERROR, "全部目标均不可达。对照每层失败位置判断："
                         "DNS 层失败 → DNS 问题；TCP 层失败 → 阻断或防火墙；"
                         "TLS 层失败 → 中间设备干扰。")


def url_of(name: str) -> str:
    return f"https://{name}/"


def _grade_total(ms: float) -> str:
    if ms < 150:
        return "流畅"
    if ms < 400:
        return "一般"
    if ms < 1000:
        return "偏慢"
    return "很慢"


#  路径 MTU
def path_mtu(post: Post, host: str = "", timeout: float = 2.0) -> None:
    """探测路径 MTU（设 DF 位不分片）。

    为什么重要
    ----------
    挂 VPN / PPPoE / 隧道时，路径 MTU 常被中间设备压低（比如
    1500 → 1400）。此时小包正常、大包被静默丢弃，表现为
    **ping 得通、网页能开但加载卡住、传大文件极慢**。这类问题
    用 ping 完全测不出来，因为它只发小包。

    实现：``ping -f -l <payload>`` 二分试探。Windows 的 ping 自带
    发 ICMP 的能力，**不需要管理员权限或 raw socket**。
    """
    if not host.strip():
        post(KIND_ERROR, "请填写目标主机")
        return
    name = host.strip()

    try:
        ip, _ = _resolve(name, None)
    except socket.gaierror as e:
        post(KIND_ERROR, f"DNS 解析失败：{name} -> {e.strerror or e}\n"
                         f"    MTU 探测需要先拿到 IP，可改填 IP 地址")
        return

    post(KIND_LINE, f"目标：{name}  ({ip})")
    post(KIND_LINE, "设 DF（不分片）位二分试探。收到「需要分片」即超过路径 MTU。")
    post(KIND_LINE, "")

    lo, hi = 576, 1500            # 搜索 MTU 区间
    best: int | None = None
    post(KIND_LINE, "MTU     结果")
    while lo <= hi:
        mid = (lo + hi) // 2
        ok, note = _ping_df(ip, mid, timeout)
        post(KIND_LINE, f"{mid:5d}  {'\u2713 通过' if ok else '\u2717 ' + note}")
        if ok:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    post(KIND_LINE, "")
    if best is None:
        post(KIND_ERROR, f"连 {lo} 字节都发不出去 —— {ip} 完全不通，"
                         f"先解决基础连通性（看「延迟测试」页）")
        return

    grade, hint = grade_mtu(best)
    post(KIND_STAT, ("路径 MTU", f"{best} 字节"))
    if 1500 - best > 0:
        post(KIND_STAT, ("首跳开销", f"{1500 - best} 字节"))
    post(KIND_STAT, ("评价", grade))
    post(KIND_LINE, f"\u2713 路径 MTU = {best} 字节"
                    f"（IPv4 头 20 + ICMP 载荷 {best - 28}）")
    post(KIND_LINE, f"  {grade} —— {hint}")
    if best < 1400:
        post(KIND_LINE, "")
        post(KIND_LINE, "  若你有「网页能开但很卡 / 传大文件卡住」的症状，"
                        "多半就是它。")
        post(KIND_LINE, f"  解决：把对应网卡的 MTU 调低到 {best}"
                        "（以管理员身份运行终端）。")


def grade_mtu(mtu: int) -> tuple[str, str]:
    """按路径 MTU 给评价。抽出来是为了能写单元测试。

    阈值参考实际存在的标准 MTU，而不是「离 1500 有多远」：
    · 1500  标准以太网
    · 1492  PPPoE 的标准值，**完全正常**，不该被警告
    · 1400  一些 VPN 客户端的默认值
    · 1280  IPv6 最小 MTU
    """
    if mtu >= 1500:
        return "正常", "标准以太网 MTU"
    if mtu >= 1492:
        return "正常", "PPPoE 的标准 MTU，属正常值"
    if mtu >= 1400:
        return "轻微压缩", "常见于轻量 VPN 封装，通常无感知"
    if mtu >= 1300:
        return "明显压缩", "有隧道或 VPN，大文件传输可能变慢"
    return "严重压缩", "隧道 MTU 很低，未分片的大包基本发不出去"


def _ping_df(ip: str, mtu: int, timeout: float) -> tuple[bool, str]:
    """用 ping -f -l 试探指定 MTU 是否能通过。返回 (通过?, 原因)。"""
    payload = mtu - 28                     # MTU = IP20 + ICMP8 + payload
    lines: list[str] = []

    def on_line(s: str) -> None:
        lines.append(s)

    try:
        run(["ping", "-n", "1", "-f", "-l", str(payload),
             "-w", str(int(timeout * 1000)), ip], on_line)
    except Exception:                                     # noqa: BLE001
        return False, "ping 调用失败"

    text = "\n".join(lines)
    if "TTL=" in text:
        return True, ""
    low = text.lower()
    if "需要拆分" in text or "fragment" in low or "mtu" in low:
        return False, "超过路径 MTU"
    if "请求超时" in text or "timed out" in low or "超时" in text:
        return False, "超时（不回 ICMP）"
    return False, "失败"


#  TCP 握手耗时分解
def tcp_timing(post: Post, host: str = "", port: int = 443,
               rounds: int = 3, connect_timeout: float = 6.0) -> None:
    """把建连耗时拆成 SYN -> SYN/ACK -> 完成三段。

    单看 connect() 总耗时无法区分「延迟高」和「丢包重传」：
    一个 200ms 的连接可能是 RTT 本身 200ms（正常），也可能是
    SYN 丢了 3 次重传（异常）。拆开看 SYN/ACK 那一段就能分辨。
    """
    if not host.strip():
        post(KIND_ERROR, "请填写目标主机")
        return
    name = host.strip()
    try:
        ip, _ = _resolve(name, port)
    except socket.gaierror as e:
        post(KIND_ERROR, f"DNS 解析失败：{name} -> {e.strerror or e}")
        return

    post(KIND_LINE, f"目标：{host.strip()}:{port}  ({ip})  测 {rounds} 轮取中位数")
    post(KIND_LINE, "")
    post(KIND_LINE, "轮次    建连      对照      额外开销   判定")

    extra_list: list[float] = []
    ok_rounds = 0
    for i in range(1, rounds + 1):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((ip, port),
                                           timeout=connect_timeout):
                first = (time.perf_counter() - t0) * 1000
        except OSError as e:
            post(KIND_LINE, f"  {i}   连接失败：{type(e).__name__}: {e}")
            continue

        t1 = time.perf_counter()
        try:
            with socket.create_connection((ip, port),
                                           timeout=connect_timeout):
                base = (time.perf_counter() - t1) * 1000
        except OSError:
            base = float("nan")

        extra = first - base if base == base else first
        extra_list.append(extra)
        ok_rounds += 1
        verdict = "正常" if extra < max(20.0, base * 0.8) else "握手有重传"
        post(KIND_LINE, f"  {i}   {first:6.0f} ms  {base:6.0f} ms  "
                        f"{extra:7.0f} ms   {verdict}")

    post(KIND_LINE, "")
    if not extra_list:
        post(KIND_ERROR, "全部轮次均连接失败，无法分解耗时")
        return

    extra_list.sort()
    med_extra = extra_list[len(extra_list) // 2]
    post(KIND_STAT, ("成功轮次", f"{ok_rounds}/{rounds}"))
    post(KIND_STAT, ("握手续费", f"{med_extra:.0f} ms"))
    if ok_rounds < rounds:
        post(KIND_LINE, "⚠ 部分轮次失败 —— 链路不稳定或被限速")
    if med_extra > 100:
        post(KIND_LINE, f"⚠ 握手续费 {med_extra:.0f} ms 偏高，"
                        f"说明 SYN 很可能被丢弃后重传过。")


#  WiFi 信号质量
# netsh 的输出用词在不同 Windows 版本上差异很大，且**同一台中文机器
# 上就有两套说法**（Windows 10 用「频段/信道/加密」，Windows 11 改成
# 「波段/通道/密码」；中文版还会把英文标签原样留着，如 AP BSSID、
# Connected Akm-cipher）。所以每个字段都要列出所有见过的写法。
#
# 另外 netsh 的标签在中文系统上可能是全角冒号，英文/半角是半角，
# 两种都要接受。
_LBL = r"[ \t]*[:：][ \t]*"

# 频段：频段(Win10) / 波段(Win11) / Band(英文)
_RE_WIFI_BAND = re.compile(
    r"(?:频段|波段|Band)" + _LBL + r"([^\r\n]+)", re.I)

# 信道：信道(Win10) / 通道(Win11) / Channel(英文)
_RE_WIFI_CHANNEL = re.compile(
    r"(?:信道|通道|Channel)" + _LBL + r"(\d+)", re.I)

# 身份验证：WPA2 - 个人 / WPA2-Personal
_RE_WIFI_AUTH = re.compile(
    r"(?:身份验证|Authentication)" + _LBL + r"([^\r\n]+)", re.I)

# 无线电类型：802.11ax
_RE_WIFI_TYPE = re.compile(
    r"(?:无线电类型|Radio[ \t]+type)" + _LBL + r"([^\r\n]+)", re.I)

# 接收速率：中文版标签里带单位（接收速率(Mbps)），且 Win11 写成
# 「传输速率」而不是「发送速率」。
# 速率的单位位置随版本变：Win11 中文写「接收速率(Mbps) : 574」，
# 单位在标签的括号里；Win10 和英文版写「接收速率 : 864.7 Mbps」，
# 单位跟在值后面。两种都要收，否则同一字段在一半机器上是空的。
_UNIT_OPT = r"(?:[ \t]*\([ \t]*Mbps[ \t]*\))?"
_TAIL_OPT = r"(?:[ \t]*Mbps)?"
_RE_WIFI_RXRATE = re.compile(
    r"(?:接收速率|Receive[ \t]+rate)" + _UNIT_OPT + _LBL
    + r"([\d.]+)" + _TAIL_OPT, re.I)
_RE_WIFI_TXRATE = re.compile(
    r"(?:传输速率|发送速率|Transmit[ \t]+rate)" + _UNIT_OPT + _LBL
    + r"([\d.]+)" + _TAIL_OPT, re.I)

# 信号：Win11 还多一行 Rssi(dBm)，不要误当成百分比。
# 信号。netsh 会在标签前留空格/制表对齐，但英文版是行首，不加前视
# 的话「Signal」顶格时反而匹配不上；这里用 (?<![-\w]) 排除
# 「Connected Akm-cipher」这类含 signal 词根的标签。
_RE_WIFI_SIGNAL = re.compile(
    r"(?<![-\w])(?:信号|Signal)" + _LBL + r"(\d+)[ \t]*%", re.I)

# 加密：Win10 中文是「加密」，Win11 改成「密码」。
_RE_WIFI_ENC = re.compile(
    r"(?:加密|密码|(?<![-\w])Cipher|Encryption)" + _LBL + r"([^\r\n]+)", re.I)
# SSID：Windows 11 中文版是「SSID : 1」，注意值可能只有一个字符，
# 也可能是空（未连接）。
_RE_WIFI_SSID = re.compile(r"(?<![\u4e00-\u9fff])SSID" + _LBL + r"([^\r\n]*)", re.I)

# AP BSSID：Win11 中文版用这个标签。
_RE_WIFI_BSSID = re.compile(
    r"(?:AP[ \t]*BSSID|BSSID)" + _LBL + r"([0-9a-fA-F:.-]+)", re.I)


def _band_from_channel(ch: str) -> str:
    """按信道号反推频段。部分 Windows 版本的 netsh 不输出频段行，
    但信道号两边都有，所以拿它兜底。802.11 的信道划分是标准固定的：

    - 1-14    → 2.4 GHz
    - 15-177  → 5 GHz（含 DFS 段 52-144）
    - 178-233 → 6 GHz（WiFi 6E 起用的 5/6/7 号频段）
    """
    if not ch or not ch.isdigit():
        return ""
    n = int(ch)
    if 1 <= n <= 14:
        return "2.4GHz"
    if 15 <= n <= 177:
        return "5GHz"
    if 178 <= n <= 233:
        return "6GHz"
    return ""


def wifi_parse(text: str) -> dict[str, str]:
    """从 ``netsh wlan show interfaces`` 的输出里取出各字段。

    抽成纯函数是为了能脱离 netsh 单测——Windows 上跑一次 netsh 太慢，
    而且本机没有无线网卡时根本跑不出有效样本。

    缺项统一返回 "-"；``band`` 额外做了信道反推（部分系统版本的
    netsh 不输出频段行，而信道两边都有）。
    """
    def g(rx: "re.Pattern[str]", default: str = "-") -> str:
        # 值可能是空的（未连接时 SSID/BSSID 都是空），空值等同未命中，
        # 否则页面上会出现「SSID  BSSID :」这种把下一行吃进来的怪东西。
        m = rx.search(text)
        v = m.group(1).strip() if m else ""
        return v or default

    out = {
        "ssid":    g(_RE_WIFI_SSID, "(未连接)"),
        "bssid":   g(_RE_WIFI_BSSID, "-"),
        "signal":  g(_RE_WIFI_SIGNAL),
        "band":    g(_RE_WIFI_BAND),
        "channel": g(_RE_WIFI_CHANNEL),
        "rx":      g(_RE_WIFI_RXRATE),
        "tx":      g(_RE_WIFI_TXRATE),
        "auth":    g(_RE_WIFI_AUTH),
        "type":    g(_RE_WIFI_TYPE),
        "enc":     g(_RE_WIFI_ENC),
        "rssi":    _rssi(text),
    }
    if out["band"] == "-" and out["channel"] != "-":
        out["band"] = _band_from_channel(out["channel"]) or "-"
    return out


def _rssi(text: str) -> str:
    """取 Rssi(dBm)。Windows 11 的 netsh 才有这一行，数值比百分比
    更能反映实际链路质量（-50 极好，-70 已经很差）。"""
    m = re.search(r"Rssi" + _LBL + r"(-?\d+)", text, re.I)
    return m.group(1) if m else "-"


def grade_signal(pct: int) -> tuple[str, str]:
    """按信号百分比给评价。抽出来是为了能写单元测试。

    阈值参考经验值：-50dBm≈90%、-67dBm≈60%、-75dBm≈30%。
    """
    if pct >= 75:
        return "极好", "信号很强，吞吐应能跑满标称速率"
    if pct >= 60:
        return "良好", "日常使用没问题，高清视频可能偶发缓冲"
    if pct >= 45:
        return "一般", "已能感知卡顿，建议靠近路由或减少遮挡"
    if pct >= 30:
        return "较弱", "延迟和丢包明显上升，慎用视频与会议"
    return "很差", "基本不可用，建议换位置或加中继"

def wifi_info(post: Post) -> None:
    """当前 WiFi 连接质量。

    数据源是 ``netsh wlan show interfaces``——Windows 上唯一无需
    管理员权限就能拿到当前连接详情的途径。
    """
    if not IS_WIN:
        post(KIND_ERROR, "WiFi 详细信息目前仅支持 Windows")
        return

    wl: list[str] = []

    def on_wifi(s: str) -> None:
        wl.append(s)

    try:
        run(["netsh", "wlan", "show", "interfaces"], on_wifi)
    except Exception as e:                              # noqa: BLE001
        post(KIND_ERROR, f"无法调用 netsh：{e}")
        return

    text = "\n".join(wl)
    # netsh 在 WLAN 服务未运行时输出一句「...没有运行」，而不是
    # 空的接口列表。这两种情况要给出不同的提示——前者可以修复。
    if "没有运行" in text or "not running" in text.lower():
        post(KIND_ERROR, "Windows 的「WLAN 无线网络服务」未运行，"
                         "因此读不到 WiFi 信息。\n"
                         "    启用它：Win+R 输入 services.msc，找到 "
                         "WLAN AutoConfig（无线自动配置）设为「自动」并启动。")
        return
    if not text.strip() or "No interfaces" in text or "没有已配置" in text:
        post(KIND_ERROR, "未找到无线接口。常见原因：没有 WiFi 网卡、"
                         "驱动未安装、或当前用网线连接。")
        return

    f = wifi_parse(text)
    ssid, signal = f["ssid"], f["signal"]
    band, channel = f["band"], f["channel"]
    rx_rate, tx_rate = f["rx"], f["tx"]
    auth, rtype = f["auth"], f["type"]

    post(KIND_LINE, f"SSID        {ssid}")
    if f["bssid"] != "-":
        post(KIND_LINE, f"BSSID       {f['bssid']}")
    post(KIND_LINE, f"连接方式    {rtype}")
    post(KIND_LINE, f"频段        {band}    信道 {channel}")
    post(KIND_LINE, f"身份验证    {auth}")
    if f["enc"] != "-":
        post(KIND_LINE, f"加密        {f['enc']}")
    post(KIND_LINE, f"速率        下行 {rx_rate}   上行 {tx_rate}")
    post(KIND_LINE, "")
    # 协商速率必须发成结构化数据。协商速率是用户判断「为什么卡」最直接
    # 的数字——信号 90% 但下行只有 65 Mbps，就是典型的 2.4GHz 远距离
    # 场景。以前只打成一行文本，用户得在一堆等宽输出里自己找，而统计块
    # 那两格永远是「—」。
    post(KIND_STAT, ("协商下行", rx_rate or "—"))
    post(KIND_STAT, ("协商上行", tx_rate or "—"))

    if signal == "-":
        # 所有字段都是「-」说明 netsh 的输出格式和全部候选标签都不
        # 匹配。netsh 的用词在不同 Windows 版本上差异很大，光靠猜标签
        # 迟早还会漏，所以直接把原始输出摊开给用户看——他能自己看出
        # 那台机器的 netsh 到底长什么样，下次提 Issue 就能带上这段。
        # 限 12 行，避免刷屏。
        if channel == "-" and f["rx"] == "-" and f["tx"] == "-":
            post(KIND_LINE, "netsh 输出的格式和所有候选标签都不匹配，"
                            "可能是系统语言既非中文也非英文。")
            post(KIND_LINE, "以下为 netsh 原始输出（前 12 行）：")
            for ln in [x for x in text.splitlines() if x.strip()][:12]:
                post(KIND_LINE, "  " + ln.rstrip())
        else:
            post(KIND_LINE, "未读到信号强度")
        return

    sig = int(signal)
    post(KIND_STAT, ("信号强度", f"{sig}%"))
    # RSSI 是驱动给的真实接收功率(dBm)，比 netsh 换算过的百分比更准。
    # -50 极好，-67 良好，-75 及以下基本没法用。
    if f["rssi"] != "-":
        post(KIND_STAT, ("信号功率", f"{f['rssi']} dBm"))
    grade, hint = grade_signal(sig)
    post(KIND_STAT, ("信号评价", grade))
    post(KIND_LINE, f"  {signal}%  {hint}")
    if band and band != "-" and "2.4" in band:
        post(KIND_LINE, "  注意：2.4GHz 穿墙强但速率低、易被邻居干扰。"
                        "近距高速建议切 5GHz。")


#  局域网 / 邻居
# Windows 的 ipconfig 用点线填充对齐：
#   "   默认网关. . . . . . . . . . : 192.168.153.2"
# 标签和冒号之间是「点 + 空格」的混合序列，所以**不能用 \\s***——
# \\s 不匹配点号，正则会永远匹配不上。这是本项目实际踩过的坑。
# 同时兼容英文版 Windows 的标签（Default Gateway / DHCP Server）。
# 标签和冒号之间是「点 + 空格」的混合序列，所以**不能用 \s***
# —— \s 不匹配点号，正则会永远匹配不上。这是本项目实际踩过
# 的坑。同时兼容英文版 Windows 的标签。
_DOTS = r"[\s.．。·]*"
_RE_GATEWAY = re.compile(rf"默认网关{_DOTS}[:：]{_DOTS}([\d.]{{1,15}})")
_RE_GATEWAY_EN = re.compile(
    rf"Default\s+Gateway{_DOTS}[:：]{_DOTS}([\d.]{{1,15}})", re.I)
_RE_DHCP = re.compile(rf"DHCP\s*服务器{_DOTS}[:：]{_DOTS}([\d.]{{1,15}})")
_RE_DHCP_EN = re.compile(
    rf"DHCP\s*Server{_DOTS}[:：]{_DOTS}([\d.]{{1,15}})", re.I)
# ARP：只取 IP 和 MAC 两组，类型列（动态/静态）位置不稳定，
# 中英文版文案不同，不解析。
_RE_ARP_ENTRY = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})", re.M)


def _find_all(text: str, cn: "re.Pattern[str]",
              en: "re.Pattern[str]") -> list[str]:
    """中英双模式查找，保持出现顺序并去重。"""
    return list(dict.fromkeys(cn.findall(text) + en.findall(text)))


def _is_unicast_neighbor(ip: str) -> bool:
    """只保留单播地址。

    ``arp -a`` 会把组播条目也列出来（224.0.0.x 是 IGMP、
    239.255.255.250 是 SSMDP），它们不是「邻居」——不排除的话列表
    里大半是噪音。回环、本网络保留、以及 x.x.x.255 也要去掉。

    早期这段是 lan_info 里的闭包 _is_neighbor：没法复用、没法
    单元测试。ARP 列表页要显示完整邻居表，必须是模块级函数。
    """
    try:
        o = [int(x) for x in ip.split(".")]
    except ValueError:
        return False
    if len(o) != 4 or any(n > 255 for n in o):
        return False
    if o[0] == 0 or o[0] >= 224:          # 本网络保留 + 组播
        return False
    if o[0] == 127:                        # 回环
        return False
    return not ip.endswith(".255")


def parse_arp(text: str) -> list[tuple[str, str]]:
    """从 ``arp -a`` 输出里抽出 (IP, MAC) 邻居表。

    刻意**不解析类型列**（动态/静态）：中英文文案不同且列位置不稳，
    正则只抓 IP 和 MAC 两组，够用且不易错。
    """
    seen: dict[str, str] = {}
    for ip, mac in _RE_ARP_ENTRY.findall(text):
        if _is_unicast_neighbor(ip):
            seen.setdefault(ip, mac)       # 同一 IP 多条时取首条
    return list(seen.items())


def arp_list(post: Post) -> None:
    """ARP 邻居表。只做读取与呈现，不发任何包。"""
    if not IS_WIN:
        post(KIND_ERROR, "ARP 列表目前仅支持 Windows")
        return
    lines: list[str] = []

    def on_line(s: str) -> None:
        lines.append(s)

    try:
        run(["arp", "-a"], on_line)
    except Exception as e:                              # noqa: BLE001
        post(KIND_ERROR, f"无法调用 arp：{e}")
        return

    entries = parse_arp("\n".join(lines))
    post(KIND_STAT, ("邻居总数", f"{len(entries)} 个"))

    if not entries:
        post(KIND_LINE, "ARP 表里没有可解析的条目。")
        # Console 是 QPlainTextEdit，**不渲染 Markdown**。此处
        # 文案带 ** 强调标记，用户会看到两个星号。
        post(KIND_LINE, "ARP 表只在本机直接相连的网段内有效，"
                        "且需要先有通信才会产生条目——ping 一下网关试试。")
        return

    post(KIND_LINE, f"{'IP 地址':<20}{'MAC 地址':<20}")
    post(KIND_LINE, "─" * 40)
    for ip, mac in entries:
        post(KIND_LINE, f"{ip:<20}{mac:<20}")
    post(KIND_LINE, "")
    post(KIND_LINE, f"共 {len(entries)} 条。")
    post(KIND_LINE, "提示：MAC 末位相同的条目是同一个厂商批量生产的，"
                    "不是同一台设备。")


def lan_info(post: Post) -> None:
    """默认网关 / DHCP / ARP 邻居表。

    这三项是判断「问题在本地还是在运营商」的关键：默认网关都
    ping 不通，就完全不用找运营商了。
    """
    if not IS_WIN:
        post(KIND_ERROR, "局域网详情目前仅支持 Windows")
        return

    # ---- IP 配置 ----
    cfg: list[str] = []

    def on_cfg(s: str) -> None:
        cfg.append(s)

    try:
        run(["ipconfig", "/all"], on_cfg)
    except Exception as e:                              # noqa: BLE001
        post(KIND_ERROR, f"无法调用 ipconfig：{e}")
        return

    if True:
        itext = "\n".join(cfg)
        gateways = _find_all(itext, _RE_GATEWAY, _RE_GATEWAY_EN)
        dhcps = _find_all(itext, _RE_DHCP, _RE_DHCP_EN)
        if gateways:
            post(KIND_STAT, ("默认网关", gateways[0]))
            post(KIND_LINE, f"默认网关    {', '.join(dict.fromkeys(gateways))}")
        else:
            post(KIND_LINE, "默认网关    未配置（可能未联网）")
        if dhcps:
            post(KIND_STAT, ("DHCP 服务器", dhcps[0]))
            post(KIND_LINE, f"DHCP 服务器  {', '.join(dict.fromkeys(dhcps))}")
        post(KIND_LINE, "")

    # ---- ARP 邻居 ----
    arp: list[str] = []

    def on_arp(s: str) -> None:
        arp.append(s)

    try:
        run(["arp", "-a"], on_arp)
    except Exception:                                  # noqa: BLE001
        pass

    entries = parse_arp("\n".join(arp))
    post(KIND_STAT, ("ARP 邻居", f"{len(entries)} 个"))
    if entries:
        post(KIND_LINE, f"ARP 表共 {len(entries)} 条已解析的条目：")
        post(KIND_LINE, "")
        post(KIND_LINE, f"{'IP 地址':<18}{'MAC 地址'}")
        for ip, mac in entries[:24]:
            post(KIND_LINE, f"{ip:<18}{mac}")
        if len(entries) > 24:
            post(KIND_LINE, f"  … 另有 {len(entries) - 24} 条")
    else:
        post(KIND_LINE, "ARP 表暂无可解析条目。先 ping 一下网关，表里就会有它了。")
    post(KIND_LINE, "")

    # ---- 网关连通性 ----
    if gateways:
        gip = gateways[0]
        pl: list[str] = []

        def on_ping(s: str) -> None:
            pl.append(s)

        t0 = time.perf_counter()
        try:
            run(["ping", "-n", "2", "-w", "1200", gip], on_ping)
        except Exception:                              # noqa: BLE001
            pass
        ms = (time.perf_counter() - t0) * 1000
        if any("TTL=" in s for s in pl):
            post(KIND_STAT, ("网关连通", f"{ms:.0f} ms"))
            post(KIND_LINE, f"✓ 网关 {gip} 可达（2 次往返 {ms:.0f} ms）")
            post(KIND_LINE, "  网关通 = 本地链路正常，问题在上游或对端。")
        else:
            post(KIND_STAT, ("网关连通", "不通"))
            post(KIND_ERROR, f"网关 {gip} 不通 —— 问题在本地，"
                             f"不用联系运营商。\n"
                             f"    依次检查：网线/无线连接、网卡驱动、"
                             f"IP 配置、路由器电源")
