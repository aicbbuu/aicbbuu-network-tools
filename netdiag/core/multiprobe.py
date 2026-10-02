"""多目标并发探测。

**单目标探测的局限**：说「网关 ping 不通」没用——可能是网关挂了，
也可能是去 A 站的路径断了。**一次测多个目标才能区分**：

    192.168.1.1   通   ← 本地没问题
    8.8.8.8       通   ← 出网正常
    223.5.5.5     通
    baidu.com     不通  ← 只这一个不通

最后一行是关键：**只有个别目标不通，说明问题在那些目标自己**；
**全部不通**，才是本地或链路的问题。这一页的价值就在这个对比。

**并发必须有界。** Windows 的 socket 和进程句柄都有上限，几百个
线程一起建连会触发限流，结果是「全都连不上」——比不测更误导。
这里默认 12 并发，用户可以调到 32，超过就不再加了。
"""
from __future__ import annotations

import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, NamedTuple

from .encoding import IS_WIN
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post

#: 默认并发。够快，又远低于系统限流阈值。
_DEFAULT_WORKERS = 12
_MAX_WORKERS = 32
#: 单目标超时。局域网毫秒级，公网 1.5s 足够区分「慢」和「不通」。
_TIMEOUT = 1.5


class Row(NamedTuple):
    """一个目标的探测结果。"""

    target: str
    ip: str
    ok: bool
    ms: float | None
    state: str      # ok / refused / filtered / dns / error

    @property
    def symbol(self) -> str:
        return {"ok": "✓", "refused": "◐", "filtered": "✗",
                "dns": "✗", "error": "✗"}[self.state]

    @property
    def label(self) -> str:
        return {"ok": "通", "refused": "拒绝连接", "filtered": "超时",
                "dns": "解析失败", "error": "出错"}[self.state]


def _default_gateway() -> str:
    """读本机的默认网关。读不到就退回常见默认值。"""
    try:
        from . import netsys
        _txt, _code = netsys._capture(["route", "print"])
        for r in netsys.parse_routes(_txt):
            if r.is_default and r.gateway:
                return r.gateway
    except Exception:                                  # noqa: BLE001
        pass
    return ""


def _layer_targets() -> list[str]:
    gw = _default_gateway()
    out = ["127.0.0.1"]
    if gw:
        out.append(gw)
    # 公网这一层用国内域名 163.com，而不是 8.8.8.8：国内链路到 8.8.8.8
    # 常被劣化或丢弃，拿它当判据会把「到境外不通」误报成「断网」。
    out += ["163.com", "223.5.5.5"]
    return out


#: 预置场景。每一组都回答一个具体的排障问题——用户不用自己想该测
#: 哪些目标，「该测什么」恰恰是最卡人的一步。
#:
#: 第三个元素是**函数**而不是固定列表：网关地址每台机器都不一样，
#: 硬编码 192.168.1.1 在别的网段上直接失效。运行时才算。
#: (key, 显示名, 说明, 取目标的函数, 应当使用的探测方式)
#:
#: **方式不是可有可无的字段。** 「分层定位」要问的是「主机在不在」，
#: 必须用 ping——用 TCP 443 去问你的网关「你 443 端口开吗」是个
#: 毫无意义的问题，答案必然是「不开」，然后用户以为自己坏了。
#: 「网站可达性」则相反：网站可能屏蔽 ICMP，只能问 443。
PRESETS: tuple[
    tuple[str, str, str, Callable[[], list[str]], str], ...] = (
    ("layer", "分层定位", "回环 → 网关 → 公网域名 → 公网 DNS",
     _layer_targets, "ping"),
    ("region", "国内外连通性", "国内与海外是否都能通",
     lambda: ["114.114.114.114", "223.5.5.5", "8.8.8.8", "1.1.1.1"],
     "ping"),
    ("site", "网站可达性", "各主流站点的 TCP 443",
     lambda: ["www.baidu.com", "www.qq.com", "www.taobao.com",
              "www.microsoft.com", "github.com"], "tcp"),
)


def resolve(target: str) -> tuple[str, str | None]:
    """返回 (IP, 错误原因)。解析失败时 IP 为 None。"""
    if not IS_WIN:
        return target, None
    try:
        return socket.gethostbyname(target), None
    except socket.gaierror as e:
        return target, e.strerror or "解析失败"
    except OSError as e:
        return target, str(e)


def _width(s: str) -> int:
    return sum(2 if ord(c) > 0x2000 else 1 for c in s)


def _pad(s: str, n: int) -> str:
    return s + " " * max(0, n - _width(s))


def probe_tcp(target: str, port: int = 443,
              timeout: float = _TIMEOUT) -> Row:
    """TCP 连一个目标的指定端口。"""
    ip, err = resolve(target)
    if err:
        return Row(target, ip, False, None, "dns")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    t0 = time.perf_counter()
    try:
        s.connect((ip, port))
        return Row(target, ip, True,
                   (time.perf_counter() - t0) * 1000, "ok")
    except ConnectionRefusedError:
        return Row(target, ip, False, None, "refused")
    except (socket.timeout, TimeoutError):
        return Row(target, ip, False, None, "filtered")
    except OSError:
        return Row(target, ip, False, None, "error")
    finally:
        s.close()


def probe_ping(target: str, count: int = 2,
               timeout_ms: int = 1200) -> Row:
    """ping 一个目标。"""
    from .encoding import run
    ip, err = resolve(target)
    if err:
        return Row(target, ip, False, None, "dns")

    flag = "-n" if IS_WIN else "-c"
    wait = ["-w", str(timeout_ms)] if IS_WIN else \
        ["-W", str(max(1, timeout_ms // 1000))]
    lines: list[str] = []
    try:
        run(["ping", flag, str(count), *wait, ip], lines.append)
    except Exception:                                  # noqa: BLE001
        return Row(target, ip, False, None, "error")
    text = "\n".join(lines)
    # Windows 输出 "= 23ms"，中文系统可能出 "= 23 毫秒"，
    # 两种都要取到，取不到才算不通。
    rtts = [float(x) for x in
            re.findall(r"(\d+)\s*(?:ms|毫秒)", text, re.I)]
    if not rtts:
        return Row(target, ip, False, None, "filtered")
    return Row(target, ip, True, sum(rtts) / len(rtts), "ok")


def parse_targets(text: str) -> list[str]:
    """从输入框解析目标列表。空行和注释行忽略。"""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 允许一行多个，用逗号或空格分开
        for part in re.split(r"[,\s;、，；]+", line):
            part = part.strip()
            if part:
                out.append(part)
    # 去重但保序——用户列的顺序本身有信息（先本地后公网）
    seen: set[str] = set()
    uniq = [t for t in out if not (t in seen or seen.add(t))]
    return uniq


def multi_probe(post: Post, targets: list[str], *, mode: str = "tcp",
                port: int = 443, workers: int = _DEFAULT_WORKERS) -> None:
    """并发探测多个目标，输出对比表格与结论。"""
    if not targets:
        post(KIND_ERROR, "请至少填写一个目标")
        return
    if len(targets) > 64:
        post(KIND_ERROR, f"目标有 {len(targets)} 个，太多了。\n"
                         f"    一次测 64 个已经要几十秒，超过就没法对比了。")
        return

    workers = max(1, min(workers, _MAX_WORKERS, len(targets)))
    probe = probe_tcp if mode == "tcp" else probe_ping
    what = f"TCP {port}" if mode == "tcp" else "ping"

    t0 = time.perf_counter()
    post(KIND_STAT, ("目标数", f"{len(targets)} 个"))
    post(KIND_STAT, ("并发数", f"{workers}"))
    post(KIND_STAT, ("探测方式", what))
    post(KIND_LINE, f"▸ 用{what}并发探测 {len(targets)} 个目标"
                    f"（并发 {workers}）…")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(probe, targets))

    ok_rows = [r for r in rows if r.ok]
    bad_rows = [r for r in rows if not r.ok]
    el = time.perf_counter() - t0

    post(KIND_STAT, ("可达", f"{len(ok_rows)} / {len(rows)}"))
    post(KIND_STAT, ("总耗时", f"{el:.1f}s"))

    # ---- 表格 ----
    post(KIND_LINE, "")
    # 状态列是「符号 + 文字」一整块，必须先拼好再算宽度——
    # 分成两个 _pad 会各自补空格，符号和文字之间就错位了。
    post(KIND_LINE, _pad("状态", 16) + _pad("目标", 26) + _pad("IP 地址", 18)
         + _pad("耗时", 12) + "说明")
    post(KIND_LINE, "─" * 76)
    for r in rows:
        cost = f"{r.ms:.0f} ms" if r.ms is not None else "—"
        note = {"ok": "", "refused": "主机在线，端口未开",
                "filtered": "被静默丢弃或不可达",
                "dns": "域名解析不了", "error": "连接出错"}[r.state]
        post(KIND_LINE, (_pad(f"{r.symbol} {r.label}", 16)
                         + _pad(r.target, 26) + _pad(r.ip, 18)
                         + _pad(cost, 12) + note).rstrip())

    # ---- 结论 ----
    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)
    if not bad_rows:
        avg = sum(r.ms for r in ok_rows if r.ms) / max(1, len(ok_rows))
        post(KIND_LINE, f"✓ {len(rows)} 个目标全部可达，"
                        f"平均 {avg:.0f} ms。")
        post(KIND_LINE, "  网络连通性没有问题。"
                        "如果某个应用仍然用不了，")
        post(KIND_LINE, "  那是应用自身的问题，不是网络。")
        return

    post(KIND_LINE, f"{len(ok_rows)} 个可达，{len(bad_rows)} 个不可达。")
    post(KIND_LINE, "")

    # 区分「个别不通」和「普遍不通」——这是这一页的核心判断
    if len(ok_rows) == 0:
        post(KIND_LINE, "✗ **所有目标都不可达。**")
        post(KIND_LINE, "  这说明问题不在目标本身，而在你的网络：")
        post(KIND_LINE, "  1. 先跑「故障诊断」页——它会分层定位到具体哪一层")
        post(KIND_LINE, "  2. 如果本机目标（127.0.0.1）都不通，问题在协议栈")
        post(KIND_LINE, "  3. 在「网络修复」页先试「清空 DNS 缓存」")
        return

    if len(ok_rows) == 1:
        post(KIND_LINE, f"✗ **只有 {ok_rows[0].target} 可达，其余全不通。**")
        post(KIND_LINE, "  这通常意味着：")
        if any(r.ip == "127.0.0.1" for r in ok_rows):
            post(KIND_LINE, "  本机通了但出不去——问题在网关或上游，"
                            "先看「故障诊断」")
        else:
            post(KIND_LINE, "  只有某一个特定目标能通——"
                            "像是被限制到特定线路，或 DNS 有问题")
        return

    ratio = len(ok_rows) / len(rows)
    if ratio >= 0.5:
        post(KIND_LINE, f"⚠ **多数可达（{len(ok_rows)}/{len(rows)}），"
                        f"少数不通。**")
        post(KIND_LINE, "  这更像是个别目标自身的问题，或它们屏蔽了"
                        f"你的探测方式。")
    else:
        post(KIND_LINE, f"⚠ **少数可达（{len(ok_rows)}/{len(rows)}），"
                        f"多数不通。**")
        post(KIND_LINE, "  看看通的那几个有什么共同点（比如都是国内、"
                        "都走同一条线路）。")

    post(KIND_LINE, "")
    post(KIND_LINE, "  不可达的目标：")
    for r in bad_rows:
        post(KIND_LINE, f"    {r.target:<24} {r.label}")
    post(KIND_LINE, "")
    if any(r.state == "dns" for r in bad_rows):
        post(KIND_LINE, "  其中有域名解析失败的——"
                        "这类问题看「DNS 解析」页。")
        post(KIND_LINE, "  其余可以改成 ping 模式再测一次："
                        "TCP 被封不代表主机不在线。")


def preset_targets(key: str) -> list[str]:
    """按预置 key 取目标列表。未知 key 返回空表。"""
    for k, _label, _desc, fn, _mode in PRESETS:
        if k == key:
            return fn()
    return []


def preset_mode(key: str) -> str:
    """取预置推荐的探测方式（ping / tcp）。"""
    for k, _label, _desc, _fn, mode in PRESETS:
        if k == key:
            return mode
    return "tcp"
