"""MTU 逐跳定位。

**现有的 MTU 页面只能告诉你「终点路径 MTU 是多少」，但真正要修
的问题往往需要知道「是哪一跳卡的」。** 举两个例子：

    路径 MTU = 1400                     哪一段降下来的？
    ├─ 1  192.168.1.1     MTU 1500      家里路由器
    ├─ 2  10.0.0.1        MTU 1400  ←  ISP 的 PPPoE 降在这里
    └─ 3  203.0.113.5     MTU 1400      之后全继承

知道了是哪一跳，就能判断该找谁：改自家路由器配置，还是投诉运营商。

**另一类情况更隐蔽**——某些中间设备根本不回 ICMP，tracert 显示
成一串 ``*``。这往往不是「那一跳挂了」，而是「那一跳不响应
traceroute 但转发正常」，或是「大包在它那里被丢了」。这两种
要靠对比探测区分，不能凭外观下结论。

**探测原理**：ping 带 ``-f``（设 DF 位，不允许分片）。发一个
N 字节的包，收到回应说明这一跳链路能承受 N+28；收到「需要分片」
说明上一跳链路的上限就在这里。逐跳做这个二分，累加得到每跳的
可用 MTU。
"""
from __future__ import annotations

import re
import socket
import time
from typing import Callable, NamedTuple

from .encoding import IS_WIN, run
from .probes_ext import _ping_df, grade_mtu
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post

#: 逐跳探测的 MTU 搜索下限。低于 576 的包在任何链路上都能过
#: （RFC 791 的最小重传单元），再往下试没有诊断价值。
_MIN_MTU = 576
#: 上限 1500 是标准以太网 MTU。超过它的路径极其罕见，测了也没用。
_MAX_MTU = 1500


class Hop(NamedTuple):
    """traceroute 路径上的一跳。

    跳号字段叫 ``hop_no`` 而不是 ``index``：NamedTuple 是 tuple 的
    子类，``index`` 会**覆盖 tuple.index() 方法**，让 ``h.hop_no(x)``
    从「找元素」变成「取跳号」，任何按值查找的代码都会静默拿到错
    的结果。这类覆盖不会报错，只会给出错误答案。
    """

    hop_no: int
    addr: str          # "*" 表示该跳无响应
    rtts: list[float]  # 毫秒；空表示全超时

    @property
    def alive(self) -> bool:
        return self.addr != "*" and bool(self.rtts)

    @property
    def best_rtt(self) -> float | None:
        return min(self.rtts) if self.rtts else None


class HopMTU(NamedTuple):
    """某一跳的 MTU 探测结果。"""

    hop: Hop
    mtu: int | None        # None = 探测未得到结论
    note: str

    @property
    def has_mtu(self) -> bool:
        return self.mtu is not None


#  Windows tracert 一行的形态：
#    "  1    <1 毫秒   <1 毫秒   <1 毫秒 192.168.153.2"
#    "  8     *        *        *     请求超时。"
#    "  3     5 ms     4 ms     6 ms  10.80.0.1"
#  时间可能是 "<1"、"61"、"1234"，也可能是 "*"；尾部可能是 IP，
#  也可能是「请求超时。」这类中文提示。
#
#  **刻意不写成一个整行正则。** 试过：三个时间列的宽度不定（有
#  "<1 毫秒" 和 "5 ms" 两种），星号行尾部还跟着中文，用一个大
#  正则要同时兼容的分支太多，反而在真实输出上匹配失败——实测
#  "  8     *        *        *     请求超时。" 就匹配不上。
#  改成「先认跳号，再各自扫 RTT 和 IP」，每一步都能独立验证。
_RE_HOP_NO = re.compile(r"^\s*(\d{1,2})\s+(.*)$")
_RE_MS = re.compile(r"(\d+)\s*(?:ms|毫秒)", re.I)
_RE_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
#  tracert 表头与结尾的说明行
_RE_NOISE = re.compile(
    r"(通过最多|跟踪完成|^Trace|^traceroute|跟踪到|跃点跟踪)", re.I)


def parse_tracert(text: str) -> list[Hop]:
    """解析 tracert 输出，返回跳表。

    刻意不解析主机名：Windows 上不加 ``-d`` 会引入反向 DNS 解析，
    每一跳都要等 DNS，最坏能拖几分钟。整行不匹配 ``_RE_IPV4``
    就当作无响应，不会从主机名里硬凑。
    """
    hops: dict[int, Hop] = {}
    for line in (text or "").splitlines():
        if not line.strip() or _RE_NOISE.search(line):
            continue
        m = _RE_HOP_NO.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        rest = m.group(2)
        rtts = [float(x) for x in _RE_MS.findall(rest)]
        # 尾部取最后一个像 IPv4 的片段。星号行没有 IP，ip() 返回 None
        # → addr 保持 "*"。
        ip = _RE_IPV4.search(rest)
        hops[idx] = Hop(idx, ip.group(0) if ip else "*", rtts)
    return [hops[k] for k in sorted(hops)]


def _resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


def probe_hop_mtu(ip: str, timeout: float = 1.2) -> tuple[int | None, str]:
    """二分探测到 ``ip`` 的路径 MTU。

    **注意这个 MTU 是「从本机到该跳」整条路径的上限，不是那一跳
    自己链路的 MTU。** 逐跳探测天然有这个含义——路径 MTU 是路径
    属性，不是节点属性。想区分只能看相邻跳 MTU 的**下降沿**，那里
    才是「上一段链路」被压低的位置。这正是本模块输出 MTU 曲线
    而不是「每跳设备型号」的原因。

    对不响应 ICMP 的地址返回 ``(None, ...)``：这类跳的 MTU 测
    不出来，硬报一个数字会造出假结论。
    """
    lo, hi = _MIN_MTU, _MAX_MTU
    best: int | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        ok, note = _ping_df(ip, mid, timeout)
        if ok:
            best = mid
            lo = mid + 1
        else:
            # 「超过路径 MTU」是有效信息：说明上限就在这一侧。
            # 「超时」说明这一跳不回 ICMP，二分没有意义。
            if "超时" in note:
                return best, "该跳不响应 ICMP，无法测出 MTU"
            hi = mid - 1
    if best is None:
        return None, "连最小 MTU 都发不出去"
    return best, ""


def hop_mtu_scan(post: Post, host: str, *,
                 max_hops: int = 15) -> None:
    """逐跳定位路径上的 MTU 瓶颈。"""
    if not IS_WIN:
        post(KIND_ERROR, "逐跳 MTU 探测目前仅支持 Windows")
        return
    if not host.strip():
        post(KIND_ERROR, "请填写目标主机")
        return

    name = host.strip()
    ip = _resolve(name)
    t0 = time.perf_counter()

    # ---- 第一步：拿到路径 ----
    post(KIND_LINE, f"▸ 追踪到 {name}（{ip}）的路径…")
    raw: list[str] = []
    cmd = ["tracert", "-d", "-w", "1000", "-h", str(max_hops), ip]
    try:
        run(cmd, raw.append)
    except Exception as e:                              # noqa: BLE001
        post(KIND_ERROR, f"tracert 调用失败：{e}")
        return
    hops = parse_tracert("\n".join(raw))
    if not hops:
        post(KIND_ERROR, f"没能从 tracert 输出里解析出任何跳。\n"
                         f"    原始输出：\n" +
                         "\n".join("      " + l for l in raw[:12]))
        return

    alive = [h for h in hops if h.alive]
    dead = [h for h in hops if not h.alive]
    post(KIND_STAT, ("路径跳数", f"{len(hops)} 跳"))
    post(KIND_STAT, ("应答跳", f"{len(alive)} 个"))
    if dead:
        post(KIND_STAT, ("无响应跳", f"{len(dead)} 个"))
    post(KIND_LINE, f"  路径共 {len(hops)} 跳，{len(alive)} 个有响应，"
                    f"{len(dead)} 个无响应。")
    post(KIND_LINE, "")

    if not alive:
        post(KIND_ERROR, "所有跳都不响应 ICMP，无法逐跳测 MTU。\n"
                         "    这本身可能就是问题所在：说明路径上有设备"
                         "屏蔽了 ICMP。\n"
                         "    先看「路由追踪」页确认连通性。")
        return

    # ---- 第二步：逐跳二分 ----
    post(KIND_LINE, f"▸ 逐跳二分探测 MTU（{len(alive)} 个有响应的跳，"
                    f"每跳约 9 次 ping）…")
    results: list[HopMTU] = []
    for n, h in enumerate(alive, 1):
        mtu, note = probe_hop_mtu(h.addr)
        results.append(HopMTU(h, mtu, note))
        post(KIND_STAT, {"kind": "hop_mtu_progress",
                         "done": n, "total": len(alive),
                         "addr": h.addr, "mtu": mtu})
        shown = f"{mtu}" if mtu else "—"
        post(KIND_LINE, f"  {h.hop_no:2d}. {h.addr:<16} MTU {shown}"
                        f"{'  ' + note if note else ''}")
        post(KIND_STAT, ("已探测跳数", f"{n} / {len(alive)}"))

    # ---- 第三步：找出瓶颈 ----
    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)

    # 关键分析：MTU 的**下降沿**。路径 MTU 是逐段取最小值，所以
    # 某个跳的 MTU 突然变小，说明上一段链路被压低了——是那一段的
    # 设备干的，不是这一跳自己。
    drops: list[tuple[HopMTU, HopMTU]] = []
    for prev, cur in zip(results, results[1:]):
        # 显式写出非 None 条件而不是靠 truthy 收窄——mtu=0 在语义上
        # 不可能，但类型系统不该靠「不可能发生」来做窄化
        if prev.mtu is None or cur.mtu is None:
            continue
        if cur.mtu < prev.mtu:
            drops.append((prev, cur))

    final_mtu = min((r.mtu for r in results if r.mtu), default=None)
    post(KIND_STAT, ("路径最小 MTU", f"{final_mtu} 字节"
                     if final_mtu else "未测出"))
    post(KIND_STAT, ("降 MTU 位置", f"{len(drops)} 处" if drops else "无"))

    post(KIND_LINE, "")
    post(KIND_LINE, "逐跳结果：")
    header = ("  跳  IP                MTU      延迟        判定")
    post(KIND_LINE, header)
    post(KIND_LINE, "  " + "─" * 60)
    for r in results:
        h = r.hop
        rtt = f"{h.best_rtt:.0f} ms" if h.best_rtt is not None else "—"
        if r.mtu is None:
            verdict = "无响应"
        elif r.mtu >= 1492:
            verdict = "标准"
        else:
            verdict = "已压缩"
        post(KIND_LINE, (f"  {h.hop_no:<3d} {h.addr:<17} "
                         f"{str(r.mtu or '—'):<9} {rtt:<12} {verdict}").rstrip())
    for h in dead:
        post(KIND_LINE, f"  {h.hop_no:<3d} {'*':<17} {'—':<9} "
                        f"{'—':<12} 不响应 ICMP")

    # ---- 结论 ----
    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)
    if final_mtu is None:
        post(KIND_LINE, "✗ 没能测出路径 MTU。")
        return

    grade, hint = grade_mtu(final_mtu)
    post(KIND_STAT, ("评价", grade))
    post(KIND_LINE, f"路径 MTU = {final_mtu} 字节　{grade} —— {hint}")
    post(KIND_LINE, "")

    if not drops:
        if final_mtu >= 1492:
            post(KIND_LINE, "✓ 每一跳的 MTU 都 ≥1492，路径上没有被压缩的"
                            "链路。")
            post(KIND_LINE, "  如果你有「网页能开但很卡 / 传大文件卡住」"
                            "的症状，原因不在 MTU。")
        else:
            post(KIND_LINE, f"每一跳测出来都是 {final_mtu}，没有下降沿。")
            post(KIND_LINE, "  说明压缩发生在**第 1 跳之前**——也就是"
                            "本机到第一个路由之间，")
            post(KIND_LINE, "  或者是本机自己的网卡 MTU 设置得太小。")
            post(KIND_LINE, "  在「系统网络状态」页对比一下本机各网卡的"
                            " MTU。")
        return

    post(KIND_LINE, f"⚠ 发现 {len(drops)} 处 MTU 下降，"
                    f"瓶颈在下面这些链路上：")
    post(KIND_LINE, "")
    for prev, cur in drops:
        post(KIND_LINE, f"  第 {prev.hop.hop_no} 跳 → 第 {cur.hop.hop_no} 跳")
        post(KIND_LINE, f"    {prev.hop.addr}（MTU {prev.mtu}）"
                        f"  →  {cur.hop.addr}（MTU {cur.mtu}）")
        post(KIND_LINE, f"    降幅 {prev.mtu - cur.mtu} 字节"
                        f"　——问题出在这一段链路上")
        post(KIND_LINE, "")

    # 谁该修：私有地址段是自家网络，公网地址段是运营商/上游
    private = _is_private(drops[0][1].hop.addr)
    if private:
        post(KIND_LINE, "  下降点落在私有地址段（10.x / 172.16-31.x / "
                        "192.168.x），")
        post(KIND_LINE, "  说明是**你自己家里的路由器或设备**在压 MTU——"
                        "常见于开了 QoS、家长控制、")
        post(KIND_LINE, "  或端口转发的路由器。改它的设置，或联系厂家。")
    else:
        post(KIND_LINE, "  下降点落在公网地址段，属于**运营商或上游网络**。")
        post(KIND_LINE, "  常见原因是 PPPoE（1492）、隧道封装（1400/1280）。")
        post(KIND_LINE, "  解决办法（按推荐顺序）：")
        post(KIND_LINE, f"    1. 把本机网卡 MTU 调到 {final_mtu}，"
                        f"这是最省事的（管理员终端执行 netsh）")
        post(KIND_LINE, "    2. 联系运营商说明情况，能改就改")
        post(KIND_LINE, "    3. 如果你在用 VPN，关掉它或改它的 MTU 设置")
    post(KIND_LINE, "")
    post(KIND_LINE, f"  总耗时 {time.perf_counter() - t0:.1f}s")


def _is_private(addr: str) -> bool:
    """判断是否私有/保留地址段。用于推断是谁该修。"""
    try:
        first = int(addr.split(".")[0])
        second = int(addr.split(".")[1]) if "." in addr else -1
    except (ValueError, IndexError):
        return False
    if first == 10:
        return True
    if first == 192 and second == 168:
        return True
    if first == 172 and 16 <= second <= 31:
        return True
    return False
