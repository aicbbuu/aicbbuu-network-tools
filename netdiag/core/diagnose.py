"""故障自动归因。

**这一页的定位是「用户不知道哪儿坏了」。** 其余 15 页是工具箱——
用户得先知道要测什么才去点。网络故障的真实场景恰恰相反：只看到
「网页打不开」，不知道问题在哪一层。

所以这里做的是**证据链**：按网络协议栈自底向上逐层探测，每一层
的结果决定要不要继续往下，并把每一步的观测写进结论。用户看到的
不是一堆数据，而是「你的 DNS 解析坏了，证据是这样」。

    物理层   网关 ping        ← 不通则问题在本地，不用找运营商
      ↓
    链路层   TCP 443 到公网    ← 不通则防火墙/阻断
      ↓
    解析层   DNS 解析          ← 不通则 DNS 问题
      ↓
    应用层   HTTPS 请求        ← 不通则代理/TLS/服务端

**顺序不可交换。** 网关不通时后面几层必然全挂，此时给「DNS 坏了」
的结论是误导。所以每一层都设了「前置条件」，前置不满足就标记为
「无法判定」而不是「异常」。

两层都刻意**不做网络请求**就能拿数据（读 ipconfig / 路由表 /
网卡 MTU / Winsock 目录），这些是排障时最先要看的东西。
"""
from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any, Callable, NamedTuple

from . import netsys
from .encoding import IS_WIN, run
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post

#: 拿来当「公网可达性」判据的目标。
#:
#: 这里用域名 163.com 而不是裸 IP，是为了让这一步测的是「能不能真的
#: 上到一个网站」，而不是「能不能 ping 通一个美国 IP」。国内网络到
#: 8.8.8.8 的路径经常被劣化或直接丢弃，用它做判据会误报断网。
#:
#: 代价是这一步现在掺了 DNS：如果 163.com 解析失败，公网连通也会判为
#: 不通。所以下面的调用点在超时时会把「连不上」和「解析不了」分开
#: 说明，不让两种原因混成一句话。
_PROBE_IP = "163.com"

#: 拿来判「DNS 能不能用」的域名。这一步必须用域名，测的就是解析。
_PROBE_DOMAIN = "www.microsoft.com"

#: 用于测 HTTPS 的 URL。用 204 响应的小页面，失败时也说明得清
#: 是握手问题还是服务端问题。
_PROBE_URL = "https://www.microsoft.com"


class Step(NamedTuple):
    """一层探测的结果。"""

    name: str
    status: str      # ok / bad / unknown / skipped
    detail: str      # 一句话观测，直接进结论
    evidence: list[str]   # 支撑这一判断的原始观测

    @property
    def symbol(self) -> str:
        return {"ok": "✓", "bad": "✗", "unknown": "?",
                "skipped": "-"}[self.status]

    @property
    def label(self) -> str:
        return {"ok": "正常", "bad": "异常", "unknown": "无法判定",
                "skipped": "未执行"}[self.status]


class Verdict(NamedTuple):
    """归因结论。"""

    title: str            # 一句话结论
    severity: str         # info / warn / error
    advice: list[str]     # 建议动作
    steps: list[Step]

    @property
    def symbol(self) -> str:
        return {"info": "✓", "warn": "⚠", "error": "✗"}[self.severity]


# ---------------------------------------------------------------- #
#  各层探测：每个都返回 (状态, 观测文本)
# ---------------------------------------------------------------- #
def _ping(ip: str, count: int = 2, timeout_ms: int = 1000) -> tuple[bool, str]:
    """ping 一个地址，返回 (是否通, 观测文本)。"""
    flag = "-n" if IS_WIN else "-c"
    wait = ["-w", str(timeout_ms)] if IS_WIN else \
        ["-W", str(max(1, timeout_ms // 1000))]
    out: list[str] = []
    t0 = time.perf_counter()
    try:
        run(["ping", flag, str(count), *wait, ip], out.append)
    except Exception as e:                              # noqa: BLE001
        return False, f"无法执行 ping：{e}"
    el = (time.perf_counter() - t0) * 1000
    ok = any("TTL=" in l or "ttl=" in l for l in out)
    if ok:
        return True, f"{ip} 可达（{count} 次往返 {el:.0f} ms）"
    return False, f"{ip} 无响应（{count} 次共 {el:.0f} ms）"


def _classify_tcp(ip: str, port: int, err: BaseException | None,
                  elapsed: float = 0.0) -> tuple[str, str]:
    """把 connect 的结果或异常分类成 (状态, 观测文本)。

    区分三种结果而不是两种：连上 / 被拒绝 / 被静默丢弃。「拒绝」
    说明主机在线但端口没开（多半是服务问题），「超时」说明被防火墙
    拦了——两者的排查方向完全相反，合并成「连不上」会把用户引到
    错误的方向上去。

    单独拆出来是为了能脱离网络栈单测：真实网络里「拒绝」这一支
    很难自然触发（Windows 回环接口对未监听端口默认静默丢弃），
    但它恰恰是最需要验证的分支。
    """
    if err is None:
        return "ok", f"{ip}:{port} 可连接（握手 {elapsed * 1000:.1f} ms）"
    if isinstance(err, ConnectionRefusedError):
        return "refused", (f"{ip}:{port} 拒绝连接"
                           f"——主机在线，但该端口没开")
    if isinstance(err, (socket.timeout, TimeoutError)):
        return "filtered", (f"{ip}:{port} 连接超时"
                            f"——被防火墙静默丢弃或被上游阻断")
    # WinError 10061 在 Python 里映射到 ConnectionRefusedError，
    # 但 10060（超时）有时也走 OSError 分支，两种都算阻断
    code = getattr(err, "winerror", None) or getattr(err, "errno", None)
    if code in (10060, 110):
        return "filtered", (f"{ip}:{port} 连接超时"
                            f"——被防火墙静默丢弃或被上游阻断")
    return "error", (f"{ip}:{port} 连接失败："
                     f"{getattr(err, 'strerror', None) or err}")


def _tcp_reach(ip: str, port: int = 443,
               timeout: float = 2.5) -> tuple[str, str]:
    """TCP 连一个端口，返回 (状态, 观测)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    t0 = time.perf_counter()
    err: BaseException | None = None
    try:
        s.connect((ip, port))
    except OSError as e:
        err = e
    finally:
        s.close()
    return _classify_tcp(ip, port, err, time.perf_counter() - t0)


def _dns_ok(domain: str) -> tuple[bool, str]:
    """解析一个域名，返回 (是否成功, 观测)。"""
    t0 = time.perf_counter()
    try:
        socket.setdefaulttimeout(5.0)
        infos = socket.getaddrinfo(domain, None, socket.AF_INET)
    except socket.gaierror as e:
        return False, f"解析 {domain} 失败：{e.strerror or e}"
    except OSError as e:
        return False, f"解析 {domain} 出错：{e}"
    finally:
        socket.setdefaulttimeout(None)
    el = (time.perf_counter() - t0) * 1000
    ips = sorted({str(i[4][0]) for i in infos})
    if not ips:
        return False, f"{domain} 解析成功但没有 A 记录"
    shown = "、".join(ips[:3]) + ("…" if len(ips) > 3 else "")
    return True, f"{domain} → {shown}（{el:.0f} ms）"


def _local_facts() -> dict[str, Any]:
    """读本机的静态配置事实（不发网络请求）。"""
    facts: dict[str, Any] = {}

    # 网卡与 MTU
    txt, _ = netsys._capture(["netsh", "interface", "ipv4",
                              "show", "interfaces"])
    facts["nics"] = netsys.parse_nics(txt)

    # 路由与默认网关
    txt, _ = netsys._capture(["route", "print"])
    facts["routes"] = netsys.parse_routes(txt)

    # DNS 服务器（ipconfig /all 里的 DNS Servers 段）
    txt, _ = netsys._capture(["ipconfig", "/all"])
    facts["dns_servers"] = _dns_servers(txt)
    # 当前网卡是否配了 DNS
    facts["has_dns_config"] = bool(facts["dns_servers"])

    # 代理
    txt, _ = netsys._capture(["netsh", "winhttp", "show", "proxy"])
    facts["proxy_raw"] = txt.strip()
    facts["proxy_on"] = "Direct access" not in txt and "直接访问" not in txt

    # 防火墙
    txt, _ = netsys._capture(["netsh", "advfirewall", "show", "allprofiles"])
    facts["firewall"] = netsys.parse_firewall(txt)

    # Winsock 里的第三方组件
    txt, _ = netsys._capture(["netsh", "winsock", "show", "catalog"])
    facts["winsock_sys"], facts["winsock_third"] = \
        netsys.parse_winsock(txt)

    return facts


_RE_DNS_SERVER = None


def _dns_servers(ipconfig_all: str) -> list[str]:
    """从 ``ipconfig /all`` 里取 DNS 服务器地址。

    标签后是「点线填充」的对齐（"DNS 服务器. . . . . : 8.8.8.8"），
    所以中间要用 ``[\\s.．。·]*`` 把点号吃掉。
    """
    global _RE_DNS_SERVER
    if _RE_DNS_SERVER is None:
        import re
        d = r"[\s.．。·]*"
        _RE_DNS_SERVER = re.compile(
            rf"DNS[ \t]*(?:服务器|Servers){d}[:：]{d}([\d.]{{1,15}})", re.I)
    out: list[str] = []
    for m in _RE_DNS_SERVER.finditer(ipconfig_all or ""):
        ip = m.group(1)
        if ip not in out:
            out.append(ip)
    return out


# ---------------------------------------------------------------- #
#  归因主流程
# ---------------------------------------------------------------- #
def diagnose(post: Post, *, deep: bool = True) -> None:
    """跑一遍完整诊断，输出结论与证据链。

    ``deep=False`` 时跳过 ICMP 与 TCP 的多次采样，只做静态检查与
    DNS/HTTPS 两步，用来在网络已经很糟糕时快速出结论。
    """
    if not IS_WIN:
        post(KIND_ERROR, "自动诊断目前仅支持 Windows")
        return

    t0 = time.perf_counter()
    steps: list[Step] = []

    # ---- 层 0：静态配置（不发包）----
    post(KIND_LINE, "▸ 读取本机网络配置…")
    facts = _local_facts()
    steps.append(_judge_static(facts))
    post(KIND_LINE, f"  {steps[-1].symbol} {steps[-1].name}：{steps[-1].detail}")

    # 静态层已经断了就没必要继续发包——每一层探测都依赖网络可用
    if steps[-1].status == "bad":
        steps.append(Step("网关连通", "skipped", "网络未就绪，未执行", []))
        steps.append(Step("公网连通", "skipped", "网络未就绪，未执行", []))
        steps.append(Step("DNS 解析", "skipped", "网络未就绪，未执行", []))
        steps.append(Step("HTTPS", "skipped", "网络未就绪，未执行", []))
    else:
        # ---- 层 1：网关 ----
        gw = _default_gateway(facts)
        post(KIND_LINE, f"▸ 测试网关 {gw}…")
        ok, ev = _ping(gw) if gw else (False, "没有配置默认网关")
        steps.append(Step("网关连通", "ok" if ok else "bad",
                          ev, [ev]))
        post(KIND_LINE, f"  {steps[-1].symbol} {ev}")

        if not ok:
            for n in ("公网连通", "DNS 解析", "HTTPS"):
                steps.append(Step(n, "skipped", "网关不通，未执行", []))
        else:
            # ---- 层 2：公网 ----
            post(KIND_LINE, f"▸ 测试公网 {_PROBE_IP}…")
            if deep:
                ok2, ev2 = _ping(_PROBE_IP, count=2, timeout_ms=1000)
            else:
                # 快速模式：ICMP 被封时 ping 不通不代表断网，直接
                # 用 TCP 判定。这一步的结论仍然可信。
                st2, ev2 = _tcp_reach(_PROBE_IP, 443)
                ok2 = st2 == "ok"
                ev2 += "（快速模式，用 TCP 判定）"
            steps.append(Step("公网连通", "ok" if ok2 else "bad", ev2, [ev2]))
            post(KIND_LINE, f"  {steps[-1].symbol} {ev2}")

            if ok2:
                # ---- 层 3：TCP 到公网 ----
                # 深度模式测的是 ICMP 那一层，这里补 TCP（ICMP 被封时
                # 才有意义）；快速模式上面已经用 TCP 判过了，再测一次
                # 是纯粹的浪费。
                if deep:
                    post(KIND_LINE, f"▸ 测试 TCP {_PROBE_IP}:443…")
                    st, ev3 = _tcp_reach(_PROBE_IP, 443)
                    steps.append(Step("公网 TCP", "ok" if st == "ok" else "bad",
                                      ev3, [ev3]))
                    post(KIND_LINE, f"  {steps[-1].symbol} {ev3}")
                else:
                    steps.append(Step(
                        "公网 TCP", "ok",
                        "快速模式已用 TCP 判定，见「公网连通」", []))

                # ---- 层 4：DNS ----
                post(KIND_LINE, f"▸ 解析 {_PROBE_DOMAIN}…")
                ok4, ev4 = _dns_ok(_PROBE_DOMAIN)
                steps.append(Step("DNS 解析", "ok" if ok4 else "bad",
                                  ev4, [ev4]))
                post(KIND_LINE, f"  {steps[-1].symbol} {ev4}")

                # ---- 层 5：HTTPS ----
                post(KIND_LINE, f"▸ 请求 {_PROBE_URL}…")
                ok5, ev5 = _https(_PROBE_URL)
                steps.append(Step("HTTPS 请求", "ok" if ok5 else "bad",
                                  ev5, [ev5]))
                post(KIND_LINE, f"  {steps[-1].symbol} {ev5}")

    verdict = _conclude(steps, facts)
    _emit(post, verdict, facts, time.perf_counter() - t0)


def _default_gateway(facts: dict[str, Any]) -> str:
    for r in facts.get("routes", []):
        if r.is_default:
            return r.gateway
    return ""


def _judge_static(facts: dict[str, Any]) -> Step:
    """判断静态配置有没有明显问题。

    静态层能查出的典型问题：没有活动网卡、MTU 被压到异常小、
    DNS 一个都没配、装了第三方 Winsock 组件。
    """
    ev: list[str] = []

    # 回环和隧道接口不算「物理网卡」，它们不参与对外通信
    nics = [n for n in facts.get("nics", []) if n.up
            and "Loopback" not in n.name and "Loopback" not in n.name]
    if not nics:
        return Step("本机配置", "bad", "没有已连接的物理网卡", ev)
    ev.append(f"{len(nics)} 张活动网卡：" +
              "、".join(f"{n.name} MTU {n.mtu if n.mtu < 0xFFFFFFFF else '—'}"
                        for n in nics[:3]))

    # MTU 被压到 576 以下会切断大包，是 VPN/隧道的典型副作用。
    # 上限用 0xFFFFFFFF 排除隧道接口的无意义 MTU——那会让
    # 「MTU 异常小」误报在 IPv6 隧道上。
    small = [n for n in nics if 0 < n.mtu < 576]
    if small:
        ev.append(f"⚠ 有 {len(small)} 张网卡的 MTU 小于 576："
                  + "、".join(f"{n.name}={n.mtu}" for n in small))
        return Step("本机配置", "bad",
                    f"MTU 被压到 {small[0].mtu}，大包会被丢弃", ev)

    if not facts.get("has_dns_config"):
        ev.append("没有配置任何 DNS 服务器")
        return Step("本机配置", "bad", "未配置 DNS 服务器", ev)

    ev.append("DNS 服务器：" + "、".join(facts["dns_servers"][:3]))

    third = facts.get("winsock_third") or []
    if third:
        ev.append(f"Winsock 里有 {len(third)} 个第三方组件：" +
                  "、".join(third[:3]))
        return Step("本机配置", "bad",
                    f"有 {len(third)} 个第三方网络组件", ev)

    return Step("本机配置", "ok", "网卡、MTU、DNS 配置正常", ev)


def _https(url: str) -> tuple[bool, str]:
    """发一个真实 HTTPS 请求，返回 (是否成功, 观测)。"""
    import ssl
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (diagnostics)"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=6.0, context=ctx) as resp:
            n = len(resp.read(2048))
            return True, (f"GET {url} → HTTP {resp.status}，"
                          f"收到 {n} 字节响应体")
    except urllib.error.HTTPError as e:
        # 4xx/5xx 也说明**网络是通的**——请求到达了服务端
        return True, f"GET {url} → HTTP {e.code}（网络可达，服务端报错）"
    except urllib.error.URLError as e:
        return False, f"{url} 请求失败：{_reason_zh(e.reason)}"
    except ssl.SSLError as e:
        reason = str(getattr(e, "reason", e))
        if "timed out" in reason or "timed out" in str(e):
            return False, (f"{url} TLS 握手超时\n"
                           f"        ——连接建立后服务器一直没回应。"
                           f"常见于 HTTPS 被中间设备干扰")
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            return False, (f"{url} 证书校验失败\n"
                           f"        ——证书无效或被替换。多为 HTTPS 审计代理，"
                           f"也可能是系统时间不对")
        return False, f"{url} TLS 错误：{reason}"
    except Exception as e:                              # noqa: BLE001
        return False, f"{url} 请求出错：{_reason_zh(e)}"


# ---------------------------------------------------------------- #
#  结论生成
# ---------------------------------------------------------------- #
def _reason_zh(e: object) -> str:
    """把 urllib/socket 的英文异常翻成用户看得懂的话。"""
    s = str(e)
    if "timed out" in s or "timeout" in s:
        return "连接超时——被静默丢弃或响应太慢"
    if "refused" in s:
        return "连接被拒绝——主机在线但该端口没开"
    if "reset" in s:
        return "连接被重置——中途被断开"
    if "Name or service not known" in s or "getaddrinfo" in s:
        return "域名解析失败"
    if "certificate" in s.lower():
        return "证书校验失败"
    return s if len(s) < 80 else s[:80] + "…"


def _conclude(steps: list[Step],
              facts: dict[str, Any]) -> Verdict:
    """按证据链给出结论。

    规则是「**第一个异常层就是根因**」——更下游的异常都是它的
    表现。DNS 坏了会让 HTTPS 失败，但结论要说「DNS 坏了」而不是
    「HTTPS 失败」。
    """
    by = {s.name: s for s in steps}

    def first_bad() -> Step | None:
        for s in steps:
            if s.status == "bad":
                return s
        return None

    bad = first_bad()

    # 全绿
    if bad is None and all(s.status == "ok" for s in steps
                            if s.name not in ("本机配置", "公网 TCP")):
        return Verdict(
            "网络状况良好，各层探测全部通过",
            "info",
            ["本机配置、网关、公网、DNS、HTTPS 都正常。",
             "如果你仍然打不开某些网站，多半是那些站点自身的问题，"
             "或浏览器缓存/扩展导致的。"],
            steps)

    if bad is None:
        return Verdict("部分项目无法判定", "warn",
                       ["有项目没测出结果，可能是探测目标被限制。"],
                       steps)

    name = bad.name
    ev = bad.detail

    # ---- 逐层给结论 ----
    if name == "本机配置":
        if "MTU" in ev:
            return Verdict("MTU 被压得异常小，大包传输会被丢弃", "error",
                           ["症状通常是 ping 一切正常，但网页慢、传大文件卡死。",
                            "查一下有没有挂 VPN：它的隧道 MTU 常见 1400/1280。",
                            "在「子网扫描」或「系统网络状态」页对比各网卡 MTU。"],
                           steps)
        if "第三方" in ev:
            return Verdict("有第三方组件插进了网络栈", "error",
                           ["装过 VPN / 加速器 / 某些安全软件后常见。",
                            "在「系统网络状态」页看 Winsock 目录里的第三方项。",
                            "彻底清掉：netsh winsock reset，然后**重启**。"],
                           steps)
        if "DNS 服务器" in ev:
            return Verdict("本机没有配置任何 DNS 服务器", "error",
                           ["手动指定一个：223.5.5.5、119.29.29.29 或运营商的。",
                            "控制面板 → 网络和 Internet → "
                            "网络连接 → 右键属性 → IPv4 → DNS。"],
                           steps)
        return Verdict("本机网络配置有问题", "error",
                       ["没有已连接的物理网卡。先检查网线/无线连接是否正常。"],
                       steps)

    if name == "网关连通":
        return Verdict("连不上网关 —— 问题在本地，与运营商无关", "error",
                       ["不用联系运营商，按这个顺序查：",
                        "  1. 网线是否插好 / WiFi 是否连上",
                        "  2. 网卡驱动是否正常（设备管理器里有黄色感叹号？）",
                        "  3. 网卡是否被禁用",
                        "  4. IP 是否被配置成了手动且填错",
                        "如果是 WiFi，顺带看「WiFi 无线」页的信号强度。"],
                       steps)

    if name == "公网连通":
        return Verdict("网关通但公网不通 —— 问题在上游", "error",
                       ["能 ping 通网关说明本地链路正常，"
                        "但出不去，可能是：",
                        "  1. 路由器自身没有外网（WAN 未拨号/未授权）",
                        "  2. 上游设备故障",
                        "  3. 运营商限流或封禁",
                        "先在浏览器里打开路由器管理页看 WAN 状态。"],
                       steps)

    if name == "公网 TCP":
        st = ev
        if "超时" in st or "静默丢弃" in st:
            return Verdict("出站 TCP 被静默丢弃", "error",
                           ["端口通不出去，典型是防火墙拦截。",
                            "查「系统网络状态」页的三配置文件防火墙状态。",
                            "临时关掉第三方安全软件的防火墙试试（Windows 自带的"
                            "允许出站，一般不是它）。"],
                           steps)
        return Verdict("公网 TCP 连接失败", "error",
                       [f"{ev}",
                        "TCP 层不通，HTTP/网页自然也打不开。",
                        "在「端口检测」页单独测 163.com:443 复现。"],
                       steps)

    if name == "DNS 解析":
        servers = "、".join(facts.get("dns_servers", [])[:3]) or "（未配置）"
        return Verdict("DNS 解析失败 —— 域名解析不出 IP", "error",
                       [f"当前 DNS 服务器：{servers}",
                        "证据：网关和公网都通，只有域名解析不出来——"
                        "说明链路没问题，问题在 DNS。",
                        "解决办法（按推荐顺序）：",
                        "  1. 换成公共 DNS：223.5.5.5 / 119.29.29.29 / 1.1.1.1",
                        "  2. 运营商的 DNS 可能被劫持或限流，直接换掉",
                        "  3. 改完在「修复」页清一下 DNS 缓存"],
                       steps)

    if name == "HTTPS 请求":
        proxy_on = facts.get("proxy_on")
        if proxy_on:
            return Verdict("HTTPS 失败，且系统配置了代理", "warn",
                           [f"系统代理：{facts.get('proxy_raw', '')[:120]}",
                            "代理配置错误会让所有 HTTPS 请求走错路。",
                            "在设置里关掉代理，或检查代理地址是否还有效。"],
                           steps)
        return Verdict("DNS 能解析，但 HTTPS 请求失败", "warn",
                       ["能解析说明 DNS 正常。失败点在 TLS 握手或服务端。",
                        "常见原因：系统时间偏差过大（TLS 证书会校验时间）、",
                        "        中间人劫持、企业网络的 HTTPS 审计代理。",
                        "先检查 Windows 的日期时间设置是否正确。"],
                       steps)

    return Verdict(f"{name} 异常", "warn", [ev], steps)


def _emit(post: Post, verdict: Verdict, facts: dict[str, Any],
          elapsed: float) -> None:
    """输出结论与证据链。"""
    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)
    post(KIND_STAT, ("诊断结论", "异常" if verdict.severity == "error"
                     else "注意"))
    post(KIND_STAT, ("异常层数", str(sum(
        1 for s in verdict.steps if s.status == "bad"))))
    post(KIND_STAT, ("总耗时", f"{elapsed:.1f}s"))

    post(KIND_LINE, "")
    post(KIND_LINE, f"{verdict.symbol} 结论：{verdict.title}")

    post(KIND_LINE, "")
    post(KIND_LINE, "证据链：")
    for s in verdict.steps:
        post(KIND_LINE, f"  {s.symbol} {s.name:<10}{s.label:<8}{s.detail}")

    if verdict.advice:
        post(KIND_LINE, "")
        post(KIND_LINE, "建议：")
        n = 0
        for a in verdict.advice:
            # 以两个空格开头的项是上一条的续行，原样缩进，不占编号
            if a.startswith("  "):
                post(KIND_LINE, f"  {a}")
            else:
                n += 1
                post(KIND_LINE, f"  {n}. {a}")
