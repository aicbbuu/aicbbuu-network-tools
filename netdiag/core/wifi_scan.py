"""WiFi 无线网络扫描。

与 probes_ext 里的其他探测不同，这里把**解析**和**调用**拆开：

    parse_scan(text) -> list[AccessPoint]     纯函数，可离线测试
    wifi_scan(post)                            调 netsh 并呈现

拆开的原因是本机的 WLAN 服务通常没运行（虚拟机/服务器/有线环境
都是），没法真的跑一次扫描来验证格式。而 `netsh wlan show
networks` 的输出结构是稳定的官方格式，可以用样例数据把解析
彻底测掉——比「在有 WiFi 的机器上试一次」可靠得多。

netsh 的输出结构（简体中文，mode=bssid）：

    接口名称 : Intel(R) Wi-Fi 6E AX201 160MHz
    目前有 3 个网络可见。

    SSID 1 : MyWiFi
             网络类型            : infra
             身份验证            : WPA2-Personal
             加密                : CCMP
             BSSID 1              : a4:2b:b0:11:22:33
             信号               : 99%
             无线电类型         : 802.11ax
             频段               : 5 GHz
             信道               : 44
             基本速率(Mbps)      : 433.3
             其它速率(Mbps)      : 866.7
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple

from .encoding import IS_WIN, run
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post

# ---------------------------------------------------------------- #
#  正则
# ---------------------------------------------------------------- #
# netsh 的标签行**有前导空格**（缩进对齐到冒号），所以不能加行首锚点；
# 标签与冒号之间也有若干空格。_LABEL 统一处理「冒号 + 两侧空格」，
# 且允许半角/全角——中文 Windows 上 netsh 输出的是全角。
# 中文系统还用词不同：Win10 是「频段/信道/加密」，Win11 改成
# 「波段/通道/密码」，所以每个标签都列出全部见过的写法。
_LABEL = "[ \\t]*[:：][ \\t]*"

# 切块靠 SSID 头。序号（SSID 1 / SSID 2）在各版本上不总是存在，
# 所以序号可选；但 (?<!B) 必须留——BSSID 1 : aa:bb:... 里也含 "SSID"
# 子串，re.I 下不排除就会把每个 BSSID 行当成一个新 SSID 的开头，
# 结果一个 AP 被拆成好几条、字段互相串。
_RE_SSID_HEAD = re.compile("(?<!B)\\bSSID\\b[ \\t]*(?:\\d+[ \\t]*)?"
                           + _LABEL + "([^\\r\\n]*)",
                           re.I | re.M)
_RE_BSSID = re.compile("BSSID\\s+\\d+\\s*" + _LABEL + "([0-9a-f:.]+)\\s*$",
                       re.I | re.M)
_RE_SIG = re.compile("信号\\s*" + _LABEL + "(\\d+)\\s*%")
_RE_TYPE = re.compile("网络类型\\s*" + _LABEL + "([^\\r\\n]+)", re.M)
_RE_AUTH = re.compile("身份验证\\s*" + _LABEL + "([^\\r\\n]+)", re.M)
_RE_ENC = re.compile("(?:加密|密码)\\s*" + _LABEL + "([^\\r\\n]+)", re.M)
_RE_RADIO = re.compile("无线电类型\\s*" + _LABEL + "([^\\r\\n]+)", re.M)
_RE_BAND = re.compile("(?:频段|波段)\\s*" + _LABEL + "([^\\r\\n]+)", re.M)
_RE_CH = re.compile("(?:信道|通道)\\s*" + _LABEL + "(\\d+)\\s*$", re.M)
_RE_RATE = re.compile("(?:基本速率|其它速率)\\s*\\(Mbps\\)\\s*"
                      + _LABEL + "([\\d.]+)\\s*$", re.M)

# 英文系统的标签（netsh 输出语言跟随系统语言）
_RE_SIG_EN = re.compile("Signal\\s*" + _LABEL + "(\\d+)\\s*%", re.I)
_RE_TYPE_EN = re.compile("Network type\\s*" + _LABEL + "([^\\r\\n]+)", re.I | re.M)
_RE_AUTH_EN = re.compile("Authentication\\s*" + _LABEL + "([^\\r\\n]+)", re.I | re.M)
_RE_ENC_EN = re.compile("Encryption\\s*" + _LABEL + "([^\\r\\n]+)", re.I | re.M)
_RE_RADIO_EN = re.compile("Radio type\\s*" + _LABEL + "([^\\r\\n]+)", re.I | re.M)
_RE_BAND_EN = re.compile("Band\\s*" + _LABEL + "([^\\r\\n]+)", re.I | re.M)
_RE_CH_EN = re.compile("Channel\\s*" + _LABEL + "(\\d+)\\s*$", re.I | re.M)
_RE_RATE_EN = re.compile("(?:Basic rates|Other rates)\\s*\\(Mbps\\)\\s*"
                         + _LABEL + "([\\d.]+)\\s*$", re.I | re.M)

_RE_COUNT = re.compile(r"目前有\s*(\d+)\s*个网络|There\s+are\s+(\d+)\s+networks", re.I)


class AccessPoint(NamedTuple):
    """一个扫描到的接入点。"""

    ssid: str
    bssid: str
    signal: int              # 0-100
    band: str                # 2.4 GHz / 5 GHz / 6 GHz
    channel: int
    auth: str                # 身份验证
    enc: str                 # 加密
    radio: str               # 无线电类型，如 802.11ax
    network_type: str        # infra / ad-hoc
    base_rate: float         # 基本速率 Mbps
    other_rate: float        # 其它速率 Mbps

    @property
    def max_rate(self) -> float:
        return max(self.base_rate, self.other_rate)


def _pick(text: str, zh: re.Pattern, en: re.Pattern, default: str = "-") -> str:
    """中英文标签都试一遍。netsh 的输出语言跟随系统语言。"""
    for rx in (zh, en):
        m = rx.search(text)
        if m:
            return m.group(1).strip()
    return default


def classify_band(radio: str, channel: int, band_hint: str = "") -> str:
    """推断频段。

    **难点：6GHz 的信道号和 2.4GHz 重叠**（都是 1-233），光看信道
    分不出来。所以判定顺序必须是：

    1. netsh 自己给出的「频段」行（Windows 10 1903+ 的 Wi-Fi 6E
       驱动会输出「6 GHz」）——最权威，直接采信；
    2. 无线电类型含 6/6E ——Wi-Fi 6E 是 6GHz 专属，此时 1-233
       信道归 6GHz；
    3. 信道落在 2.4 或 5 的专属区间。

    早期版本先判 `1 <= ch <= 13 -> 2.4 GHz`，结果 6GHz 的信道 5
    被判成 2.4GHz——而 6GHz 用 802.11ax 是没错的，只看信道就
    永远分不出来。
    """
    hint = band_hint.strip().lower()
    if hint:
        if "6" in hint and "2.4" not in hint:
            return "6 GHz"
        if "5" in hint:
            return "5 GHz"
        if "2.4" in hint:
            return "2.4 GHz"

    radio_l = radio.lower()
    is_6e = ("6e" in radio_l or "wi-fi 6e" in radio_l
             or "802.11be" in radio_l)

    if 1 <= channel <= 233 and is_6e:
        return "6 GHz"
    if 1 <= channel <= 14:
        return "2.4 GHz"
    if 32 <= channel <= 177:
        return "5 GHz"
    return "未知"


def _parse_one(block: str) -> AccessPoint | list[AccessPoint] | None:
    """解析一个 SSID 块。

    一个 SSID 可能对应多个 BSSID（双频合一路由会同时开 2.4 和
    5GHz），而**每个 BSSID 的信号、信道、速率都是各自的**。必须
    按 BSSID 切块分别解析——早期版本整块只取第一个值，结果同一
    台路由器的 5GHz 和 2.4GHz 显示成完全相同的信号和信道（52% 被
    写成 95%），而对比这两者恰恰是用户最想做的事。
    """
    head = _RE_SSID_HEAD.search(block)
    if head is None:
        return None
    ssid = head.group(1).strip()
    # 空 SSID 是隐藏网络，netsh 仍会给它一个块。显示成空行没用，
    # 直接跳过——但它下面的 BSSID 也就一起丢了，这是可接受的取舍：
    # 隐藏网络本来就没有 SSID 可显示。
    if not ssid or ssid.lower() in ("ssid", "name", "名称"):
        return None

    # SSID 级字段（在第一个 BSSID 行之前），共享给所有 BSSID
    marks = list(_RE_BSSID.finditer(block))
    shared_src = block[:marks[0].start()] if marks else block
    shared = {
        "network_type": _pick(shared_src, _RE_TYPE, _RE_TYPE_EN),
        "auth": _pick(shared_src, _RE_AUTH, _RE_AUTH_EN),
        "enc": _pick(shared_src, _RE_ENC, _RE_ENC_EN),
    }

    if not marks:
        return _make_ap(ssid, "", block, shared)

    out: list[AccessPoint] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
        # 公共信息 + 这一个 BSSID 自己的行
        out.append(_make_ap(ssid, m.group(1).strip().lower(),
                            shared_src + block[m.start():end], shared))
    return out


def _make_ap(ssid: str, bssid: str, text: str,
             shared: dict[str, str]) -> AccessPoint:
    """从（SSID 公共信息 + 单个 BSSID 的行）构造一条记录。"""
    signal = _pick(text, _RE_SIG, _RE_SIG_EN, "0")
    channel = _pick(text, _RE_CH, _RE_CH_EN, "0")
    radio = _pick(text, _RE_RADIO, _RE_RADIO_EN)
    band_hint = _pick(text, _RE_BAND, _RE_BAND_EN, "")
    rates = [float(x) for x in _RE_RATE.findall(text)]
    rates += [float(x) for x in _RE_RATE_EN.findall(text)]
    ch = int(channel) if channel.isdigit() else 0
    return AccessPoint(
        ssid=ssid,
        bssid=bssid,
        signal=int(signal) if signal.isdigit() else 0,
        band=classify_band(radio, ch, band_hint),
        channel=ch,
        auth=shared.get("auth") if shared.get("auth") != "-"
             else _pick(text, _RE_AUTH, _RE_AUTH_EN),
        enc=shared.get("enc") if shared.get("enc") != "-"
            else _pick(text, _RE_ENC, _RE_ENC_EN),
        radio=radio,
        network_type=shared.get("network_type", "-"),
        base_rate=min(rates) if rates else 0.0,
        other_rate=max(rates) if rates else 0.0,
    )


def parse_scan(text: str) -> list[AccessPoint]:
    """解析 ``netsh wlan show networks mode=bssid`` 的输出。

    返回**每个 BSSID 一条**记录：同一个 SSID 可能有多个 AP
    （双频合一路由会同时开 2.4 和 5GHz），只按 SSID 合并会把
    两个不同频段、不同信号的 AP 混成一条，那正是用户最想看的
    对比信息。
    """
    if not text.strip():
        return []

    # 按 "SSID N :" 切块
    heads = list(_RE_SSID_HEAD.finditer(text))
    if not heads:
        return []

    aps: list[AccessPoint] = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        got = _parse_one(text[h.start():end])
        if got is None:
            continue
        # 一个 SSID 块可能返回多条（多 BSSID）
        aps.extend(got if isinstance(got, list) else [got])

    # 信号强的排前面；同强度按频段从高到低（6 > 5 > 2.4，高频更快）
    band_rank = {"6 GHz": 0, "5 GHz": 1, "2.4 GHz": 2, "未知": 3}
    aps.sort(key=lambda a: (-a.signal, band_rank.get(a.band, 9), a.ssid))
    return aps


# ---------------------------------------------------------------- #
#  呈现
# ---------------------------------------------------------------- #
def _grade(sig: int) -> tuple[str, str]:
    if sig >= 80:
        return "极好", "满速可用"
    if sig >= 65:
        return "良好", "日常无碍"
    if sig >= 50:
        return "一般", "高清视频可能卡"
    if sig >= 30:
        return "较弱", "延迟与丢包明显上升"
    return "很差", "基本不可用"


def _fmt_rate(mbps: float) -> str:
    if mbps <= 0:
        return "-"
    if mbps >= 1000:
        return f"{mbps / 1000:.1f} Gbps"
    return f"{mbps:.0f} Mbps"


def wifi_scan(post: Post) -> None:
    """扫描周围所有可见的无线网络。"""
    if not IS_WIN:
        post(KIND_ERROR, "WiFi 扫描目前仅支持 Windows")
        return

    lines: list[str] = []

    def on_line(s: str) -> None:
        lines.append(s)

    try:
        run(["netsh", "wlan", "show", "networks", "mode=bssid"], on_line)
    except Exception as e:                              # noqa: BLE001
        post(KIND_ERROR, f"无法调用 netsh：{e}")
        return

    text = "\n".join(lines)
    if "没有运行" in text or "not running" in text.lower():
        post(KIND_ERROR,
             "Windows 的「WLAN 无线网络服务」未运行，因此无法扫描。\n"
             "    启用它：Win+R 输入 services.msc，找到 "
             "WLAN AutoConfig（无线自动配置）设为「自动」并启动。\n"
             "    注意：本功能需要无线网卡。用网线或无 WiFi 硬件的"
             "机器无法扫描，这是硬件限制不是程序问题。")
        return

    aps = parse_scan(text)
    if not aps:
        # netsh 的标签用词各版本不同，猜不全时把原文摊开给用户看，
        # 限 15 行。报 Issue 时带上这段就能直接定位。
        head = [x for x in text.splitlines() if x.strip()][:15]
        if head and not any("SSID" in x or "名称" in x or "Name" in x
                            for x in head):
            post(KIND_ERROR,
                 "netsh 返回了内容，但里面没有可识别的网络条目。"
                 "netsh 的标签用词随 Windows 版本变化，"
                 "可能是本机版本不在已知范围内。")
            post(KIND_ERROR, "以下为原始输出（前 15 行）：")
            for ln in head:
                post(KIND_LINE, "  " + ln.rstrip())
            return
        post(KIND_ERROR,
             "没有扫描到任何无线网络。可能是：网卡被禁用、"
             "路由器关闭了广播、或当前在屏蔽 2.4/5GHz 的环境里。")
        return

    # 按频段统计
    by_band: dict[str, list[AccessPoint]] = {}
    for a in aps:
        by_band.setdefault(a.band, []).append(a)

    post(KIND_STAT, ("可见网络", f"{len(aps)} 个"))
    bands = " ".join(f"{b}:{len(v)}" for b, v in sorted(by_band.items()))
    post(KIND_STAT, ("频段分布", bands))
    post(KIND_STAT, ("最强信号", f"{aps[0].signal}%"))

    # 列宽按「最坏情况」给足：SSID 标准上限 32 字节（全 ASCII），
    # BSSID 固定 17 个十六进制字符加冒号。中文 SSID 在等宽字体里
    # 占两列，所以 _trunc 的阈值也要按显示宽度算，不能按字符数。
    # 刻意**不用** f-string 的 <32 对齐：它按 len(字符数) 算，
    # 而等宽字体里汉字占两列，中文 SSID 照样会撑破列宽。统一走
    # _pad()，它按显示宽度补空格。
    header = (_pad("SSID", 32) + "  " + _pad("BSSID", 19) + _pad("频段", 9)
              + "信道".rjust(4) + "  " + "信号".rjust(4) + "  "
              + _pad("加密", 18) + "速率".rjust(10))
    order = ("6 GHz", "5 GHz", "2.4 GHz", "未知")
    for band in order:
        group = by_band.get(band)
        if not group:
            continue
        # 必须在**分组内**再排一次：parse_scan 的排序是全局的
        # （信号优先），而输出是按频段分组的，直接用会出现
        # 「组内 78% 排在 52% 后面」这种看起来像乱序的情况。
        group = sorted(group, key=lambda a: (-a.signal, a.channel))
        post(KIND_LINE, f"\n═══ {band}　{len(group)} 个 ═══")
        post(KIND_LINE, header)
        for a in group:
            enc = a.enc if a.enc != "-" else a.auth
            post(KIND_LINE,
                 _pad(_trunc(a.ssid, 32), 32) + "  "
                 + _pad(a.bssid, 19) + _pad(a.band, 9)
                 + str(a.channel).rjust(4) + "  "
                 + f"{a.signal:>3}%" + "  "
                 + _pad(_trunc(enc, 18), 18)
                 + _fmt_rate(a.max_rate).rjust(10))

    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)
    post(KIND_STAT, ("加密情况", _enc_summary(aps)))

    # 判定「未加密」要覆盖三种写法：中文「开放」、英文 Open、
    # 以及解析失败时的缺省 "-"。写成一条 or 链比 and/or
    # 混用（优先级坑）清楚得多。
    def _is_open(a: AccessPoint) -> bool:
        t = a.auth.strip()
        return t in ("开放", "Open", "无", "None", "-")

    open_aps = [a for a in aps if _is_open(a)]
    if open_aps:
        post(KIND_STAT, ("开放网络", f"{len(open_aps)} 个"))
        post(KIND_LINE, f"⚠ 有 {len(open_aps)} 个网络未加密，"
                        f"任何人都能连上去："
                        + "、".join(_trunc(a.ssid, 18) for a in open_aps[:5]))
    else:
        post(KIND_STAT, ("开放网络", "0 个"))
        post(KIND_LINE, "✓ 所有网络都启用了加密")


def _trunc(s: str, n: int) -> str:
    """按**显示宽度**截断，不是按字符数。

    输出区是 Consolas 等宽字体，一个汉字占两列。按 len() 截断会让
    中文 SSID 撑破列宽，撞到右边那一列上去。
    """
    s = s or "-"
    if _width(s) <= n:
        return s
    out = ""
    w = 0
    for ch in s:
        cw = 2 if ord(ch) > 0x2000 else 1
        if w + cw > n - 1:
            break
        out += ch
        w += cw
    return out + "…"


def _width(s: str) -> int:
    """等宽字体下的显示宽度（汉字算 2 列）。"""
    return sum(2 if ord(ch) > 0x2000 else 1 for ch in s)


def _pad(s: str, n: int) -> str:
    """按显示宽度左对齐补空格。"""
    return s + " " * max(0, n - _width(s))


def _enc_summary(aps: list[AccessPoint]) -> str:
    kinds: dict[str, int] = {}
    for a in aps:
        k = a.auth if a.auth != "-" else a.enc
        kinds[k] = kinds.get(k, 0) + 1
    return " ".join(f"{k}×{v}" for k, v in
                    sorted(kinds.items(), key=lambda kv: -kv[1])[:3])
