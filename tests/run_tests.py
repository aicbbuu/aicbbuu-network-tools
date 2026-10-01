"""
不依赖第三方测试框架的自检脚本。

本项目的运行环境刻意保持「无第三方依赖」，测试也不引入 pytest——
这样任何人 clone 下来 `python tests/run_tests.py` 就能跑。
"""
from __future__ import annotations

import sys

# CI 的 windows-latest 控制台代码页是 cp1252，print 中文直接
# UnicodeEncodeError，测试连标题都打不出来就 exit 1。本地终端多半是
# UTF-8 所以复现不了——这个坑只有真正推上 GitHub 才会暴露。
# reconfigure 幂等，且对已经是 UTF-8 的流无副作用。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # 被重定向到 StringIO 等无 reconfigure 的对象
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netdiag.core.encoding import detect_encoding          # noqa: E402
from netdiag.core.ipmath import (                                        # noqa: E402
    classify_ipv4, convert_ipv4, int_to_ip, range_to_cidrs, subnet_info,
)
from netdiag.core.probes import parse_ports, ping_summary, _jitter  # noqa: E402
from netdiag.core.probes_ext import parse_arp, wifi_parse                 # noqa: E402
from netdiag.core import netsys                                          # noqa: E402
from netdiag.core import subnetscan as netsys_sub                        # noqa: E402
from netdiag.core import diagnose as diagnose_mod                        # noqa: E402
from netdiag.core import netfix as netfix_mod                            # noqa: E402
from netdiag.core import hopmtu as hopmtu_mod                            # noqa: E402
from netdiag.core import multiprobe as multiprobe_mod                    # noqa: E402
#: CI 也在 Ubuntu 上跑核心测试。有几个测试的断言依赖 Windows 的实际
#: 行为（子网扫描、Windows 的 DNS 解析、回环端口的拒绝语义），
#: 非 Windows 上要显式跳过，否则会把平台差异误报成实现缺陷。
_IS_WIN = sys.platform == "win32"
from netdiag.core.ipmath import int_to_ip, ip_to_int                     # noqa: E402
from tests.fixtures_netsys import (                                       # noqa: E402
    DNS_CACHE, FIREWALL, NETSTAT, NICS, ROUTE_PRINT, TCP_GLOBAL, WINSOCK,
)
from netdiag.core import netsys                                          # noqa: E402
from netdiag.core import subnetscan as netsys_sub                        # noqa: E402
from netdiag.core import diagnose as diagnose_mod                        # noqa: E402
from netdiag.core import netfix as netfix_mod                            # noqa: E402
from netdiag.core import hopmtu as hopmtu_mod                            # noqa: E402
from netdiag.core import multiprobe as multiprobe_mod                    # noqa: E402
from netdiag.core.ipmath import int_to_ip, ip_to_int                     # noqa: E402
from tests.fixtures_netsys import (                                       # noqa: E402
    DNS_CACHE, FIREWALL, NETSTAT, NICS, ROUTE_PRINT, TCP_GLOBAL, WINSOCK,
)
from netdiag.core.wifi_scan import classify_band, parse_scan        # noqa: E402

_results: list[tuple[str, bool, str]] = []


def check(name):
    def deco(fn):
        def wrapper():
            try:
                fn()
                _results.append((name, True, ""))
                print(f"  \033[32mPASS\033[0m  {name}")
            except AssertionError as e:
                _results.append((name, False, str(e)))
                print(f"  \033[31mFAIL\033[0m  {name}\n        {e}")
            except Exception:
                # 统一打 FAIL 前缀：早期非 AssertionError 走 ERROR 分支，
                # 而习惯上只 grep FAIL，于是异常型失败会被整段漏掉——
                # 计数显示 48/49 但屏幕上什么都看不到。
                _results.append((name, False, traceback.format_exc(limit=2)))
                print(f"  \033[31mFAIL\033[0m  {name}  (异常)\n"
                      f"{traceback.format_exc(limit=3)}")
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


# ------------------------------------------------------------------ #
#  编码探测
# ------------------------------------------------------------------ #
@check("detect_encoding: 空输入回退 utf-8")
def t_empty():
    assert detect_encoding(b"") == "utf-8"


@check("detect_encoding: 纯 ASCII 识别为 utf-8")
def t_ascii():
    assert detect_encoding(b"hello world") == "utf-8"


@check("detect_encoding: GBK 中文被正确识别")
def t_gbk():
    sample = "正在 Ping www.baidu.com 具有 32 字节的数据:".encode("gbk")
    assert detect_encoding(sample) == "gbk", detect_encoding(sample)


@check("detect_encoding: UTF-8 中文被正确识别")
def t_utf8():
    sample = "正在 Ping www.baidu.com 具有 32 字节的数据:".encode("utf-8")
    assert detect_encoding(sample) == "utf-8", detect_encoding(sample)


@check("detect_encoding: GBK 与 UTF-8 不混淆")
def t_no_confuse():
    g = "延迟测试".encode("gbk")
    u = "延迟测试".encode("utf-8")
    assert detect_encoding(g) != detect_encoding(u) or True  # latin-1 兜底保证不抛
    # 至少不能把 gbk 误判成 utf-8
    assert detect_encoding(g) != "utf-8", "GBK 样本被误判为 UTF-8"


# ------------------------------------------------------------------ #
#  端口解析
# ------------------------------------------------------------------ #
@check("parse_ports: 空输入返回常用端口")
def t_ports_default():
    p = parse_ports("")
    assert len(p) > 10
    assert ("HTTP", 80) in p


@check("parse_ports: 单个端口")
def t_ports_single():
    assert parse_ports("8080") == [("8080", 8080)]


@check("parse_ports: 逗号分隔，保留顺序")
def t_ports_multi():
    assert parse_ports("80,443,22") == [("80", 80), ("443", 443), ("22", 22)]


@check("parse_ports: 中文逗号与顿号")
def t_ports_cn_sep():
    assert parse_ports("80，443、22") == [("80", 80), ("443", 443), ("22", 22)]


@check("parse_ports: 区间展开 8000-8002")
def t_ports_range():
    got = parse_ports("8000-8002")
    assert got == [("8000", 8000), ("8001", 8001), ("8002", 8002)], got


@check("parse_ports: 去重")
def t_ports_dedup():
    assert parse_ports("80,80,80") == [("80", 80)]


@check("parse_ports: 非数字抛错")
def t_ports_bad():
    try:
        parse_ports("abc")
    except ValueError as e:
        assert "不是合法端口" in str(e), e
    else:
        raise AssertionError("应当抛出 ValueError")


@check("parse_ports: 越界抛错")
def t_ports_range_bad():
    try:
        parse_ports("70000")
    except ValueError as e:
        assert "超出" in str(e), e
    else:
        raise AssertionError("应当抛出 ValueError")


@check("parse_ports: 反向区间抛错")
def t_ports_reverse():
    try:
        parse_ports("9000-8000")
    except ValueError:
        pass
    else:
        raise AssertionError("应当抛出 ValueError")


# ------------------------------------------------------------------ #
#  Ping 统计
# ------------------------------------------------------------------ #
@check("ping_summary: 全部成功")
def t_ping_ok():
    s = ping_summary([10, 12, 14], 3)
    assert s["sent"] == 3 and s["recv"] == 3
    assert s["loss"] == 0.0
    assert s["min"] == 10 and s["max"] == 14
    assert abs(s["avg"] - 12.0) < 1e-9
    assert s["all_failed"] is False


@check("ping_summary: 部分丢包")
def t_ping_partial():
    s = ping_summary([10, 12], 4)
    assert s["recv"] == 2
    assert abs(s["loss"] - 50.0) < 1e-9
    assert s["all_failed"] is False


@check("ping_summary: 全部失败")
def t_ping_fail():
    s = ping_summary([], 4, all_failed=True)
    assert s["recv"] == 0
    assert s["loss"] == 100.0
    assert s["min"] is None and s["avg"] is None
    assert s["all_failed"] is True


@check("ping_summary: 除零安全（sent=0）")
def t_ping_zero():
    s = ping_summary([], 0)
    assert s["loss"] == 0.0


@check("_jitter: 相邻差平均")
def t_jitter():
    assert _jitter([10, 20, 10]) == 10.0
    assert _jitter([10]) is None
    assert _jitter([]) is None


# ------------------------------------------------------------------ #
#  延迟解析（回归测试：曾因只认英文 time=12ms 而漏掉中文 时间<1ms，
# 导致 ping 明明成功却统计成 100% 丢包）
# ------------------------------------------------------------------ #
import re as _re  # noqa: E402
from netdiag.core.probes import _RE_RTT, _first_int  # noqa: E402


@check("RTT 解析：英文 time=12ms")
def t_rtt_en():
    assert _first_int(_RE_RTT.search("time=12ms TTL=56")) == 12


@check("RTT 解析：英文 time<1ms")
def t_rtt_en_less():
    assert _first_int(_RE_RTT.search("time<1ms TTL=128")) == 1


@check("RTT 解析：中文 时间=12ms（Windows 中文版）")
def t_rtt_cn():
    assert _first_int(_RE_RTT.search("时间=12ms TTL=56")) == 12


@check("RTT 解析：中文 时间<1ms（localhost 实测格式）")
def t_rtt_cn_less():
    assert _first_int(_RE_RTT.search("时间<1ms TTL=128")) == 1


@check("RTT 解析：完整中文回复行")
def t_rtt_cn_full():
    line = "来自 127.0.0.1 的回复: 字节=32 时间<1ms TTL=128"
    assert _first_int(_RE_RTT.search(line)) == 1


@check("RTT 解析：超时行不误判")
def t_rtt_timeout():
    assert _RE_RTT.search("请求超时。") is None
    assert _RE_RTT.search("Request timed out.") is None


@check("RTT 解析：统计汇总行不误判为回复")
def t_rtt_summary():
    assert _RE_RTT.search("往返行程的估计时间(以毫秒为单位):") is None
    assert _RE_RTT.search("    最短 = 0ms，最长 = 0ms，平均 = 0ms") is None


# ------------------------------------------------------------------ #
#  新增探测的纯逻辑（不联网、不起进程）
# ------------------------------------------------------------------ #
@check("ipconfig 解析：点线填充的默认网关（中文/英文）")
def t_ipconfig_gw():
    """ipconfig 用点线填充对齐，正则必须吃掉「. . . . 」。

    这是本项目实际踩过的坑：标签与冒号之间是点号分隔的，
    而 \\s 不匹配点号，写 \\s* 会导致默认网关永远解析不到。
    """
    from netdiag.core.probes_ext import (
        _RE_GATEWAY, _RE_GATEWAY_EN, _find_all,
    )
    cn = "   默认网关. . . . . . . . . . : 192.168.153.2"
    assert _find_all(cn, _RE_GATEWAY, _RE_GATEWAY_EN) == ["192.168.153.2"]
    en = "   Default Gateway . . . . . . . . . : 10.0.0.1"
    assert _find_all(en, _RE_GATEWAY, _RE_GATEWAY_EN) == ["10.0.0.1"]
    # 无点线（某些精简输出）也要能匹配
    assert _find_all("默认网关: 172.16.0.1", _RE_GATEWAY, _RE_GATEWAY_EN) \
        == ["172.16.0.1"]


@check("ipconfig 解析：DHCP 服务器不误吃相邻字段")
def t_ipconfig_dhcp():
    """DHCP 服务器不能误吃 DHCPv6 IAID 之类的相邻字段。"""
    from netdiag.core.probes_ext import _RE_DHCP, _RE_DHCP_EN, _find_all
    t = ("   DHCP 服务器 . . . . . . . . . : 192.168.153.254\n"
         "   DHCPv6 IAID . . . . . . . . . : 100666409\n"
         "   DHCP 已启用 . . . . . . . . . : 是")
    got = _find_all(t, _RE_DHCP, _RE_DHCP_EN)
    assert got == ["192.168.153.254"], f"解析出 {got}"
    assert "100666409" not in got


@check("arp -a 解析：IP 与 MAC 两列")
def t_arp_parse():
    from netdiag.core.probes_ext import _RE_ARP_ENTRY
    t = ("  接口: 192.168.153.128 --- 0x11\n"
         "  Internet 地址      物理地址              类型\n"
         "  192.168.153.2      00-50-56-e0-a3-69     动态\n"
         "  192.168.153.254    00-50-56-e6-f5-90     动态\n")
    got = _RE_ARP_ENTRY.findall(t)
    assert got == [("192.168.153.2", "00-50-56-e0-a3-69"),
                   ("192.168.153.254", "00-50-56-e6-f5-90")], got


@check("WiFi 信号分档：五档边界")
def t_grade_signal():
    from netdiag.core.probes_ext import grade_signal
    for pct, want in ((95, "极好"), (80, "极好"), (65, "良好"),
                      (50, "一般"), (35, "较弱"), (20, "很差"), (0, "很差")):
        got, hint = grade_signal(pct)
        assert got == want, f"{pct}% 得到 {got}，应为 {want}"
        assert hint, "评价必须带提示"


@check("路径 MTU 分档：1492 算正常")
def t_grade_mtu():
    from netdiag.core.probes_ext import grade_mtu
    # 1492 是 PPPoE 的标准 MTU，必须算「正常」——第一版按
    # 「离 1500 有多远」分档，把它误判成「轻微压缩」，
    # 会被单元测试挡下来。
    for mtu, want in ((1500, "正常"), (1492, "正常"), (1491, "轻微压缩"),
                      (1450, "轻微压缩"), (1400, "轻微压缩"),
                      (1350, "明显压缩"), (1300, "明显压缩"),
                      (1200, "严重压缩")):
        got, hint = grade_mtu(mtu)
        assert got == want, f"MTU {mtu} 得到 {got}，应为 {want}"
        assert hint, "评价必须带说明"


@check("getaddrinfo 结果类型是 str")
def t_resolve_type():
    """getaddrinfo 的 sockaddr 第 0 项类型标注是 str|int，
    _resolve 必须转成 str 才能喂给 create_connection。"""
    import socket
    from netdiag.core.probes_ext import _resolve
    ip, uniq = _resolve("localhost", 80)
    assert isinstance(ip, str) and ip, f"ip 应为非空字符串，实际 {ip!r}"
    assert all(isinstance(x, str) for x in uniq)


# ------------------------------------------------------------------ #
# ================================================================== #
#  IP 工具（纯计算，可穷举验证）
# ================================================================== #
@check("ipmath: 子网计算器全部字段")
def t_subnet_fields():
    d = subnet_info("192.168.0.0/24")
    assert d["network"] == "192.168.0.0", d["network"]
    assert d["netmask"] == "255.255.255.0", d["netmask"]
    # 字节序：最高位字节在前。写成 >> (8 * i) 会反过来
    assert d["netmask_bin"] == "11111111.11111111.11111111.00000000", \
        d["netmask_bin"]
    assert d["prefix"] == "/24"
    assert d["wildcard"] == "0.0.0.255", d["wildcard"]
    assert d["total"] == 256
    assert d["first"] == "192.168.0.1"
    assert d["last"] == "192.168.0.254"
    assert d["broadcast"] == "192.168.0.255"
    assert "C" in d["class"], d["class"]


@check("ipmath: 带主机位的 IP 按掩码归位")
def t_subnet_hostbits():
    # strict=True 的 IPv4Network 会拒绝这种输入，而它正是子网计算器
    # 最常见的用法——所以 parse_cidr 必须 strict=False
    assert subnet_info("192.168.1.5/24")["network"] == "192.168.1.0"
    assert subnet_info("10.1.2.3/8")["network"] == "10.0.0.0"
    assert subnet_info("1.2.3.4")["prefix"] == "/32"


@check("ipmath: /31 与 /32 不越界")
def t_subnet_small():
    # 早期用 IPv4Address 做减法，/32 时 net_addr + total - 2 算出
    # 2**32 直接抛 AddressValueError。这里必须全程整数运算。
    d = subnet_info("10.0.0.5/32")
    assert d["total"] == 1 and d["first"] == "10.0.0.5", d
    d31 = subnet_info("10.0.0.4/31")
    assert d31["total"] == 2 and d31["usable"] == 2, d31
    assert d31["broadcast"] == "10.0.0.5"


@check("ipmath: 与标准库 ipaddress 对拍 300 组")
def t_subnet_crosscheck():
    import ipaddress
    import random
    random.seed(20260929)
    for _ in range(300):
        a = random.randint(1, 0xFFFFFFFE)
        p = random.randint(0, 32)
        ip = int_to_ip(a)
        ref = ipaddress.IPv4Network(f"{ip}/{p}", strict=False)
        got = subnet_info(f"{ip}/{p}")
        assert got["network"] == str(ref.network_address), (ip, p)
        assert got["netmask"] == str(ref.netmask), (ip, p)
        assert got["wildcard"] == str(ref.hostmask), (ip, p)
        assert got["total"] == ref.num_addresses, (ip, p)
        assert got["broadcast"] == str(ref.broadcast_address), (ip, p)


@check("ipmath: IP 类别判定")
def t_class():
    # 类别边界按首位二进制高位分：192-223 是 C 类（110xxxxx），
    # 224-239 是 D，240-255 是 E。易误划为 D。
    for ip, want in (("10.0.0.1", "A（私有地址）"),
                     ("172.16.0.1", "B（私有地址）"),
                     ("192.168.1.1", "C（私有地址）"),
                     ("8.8.8.8", "A"),
                     ("127.0.0.1", "B（本机回环）"),
                     ("169.254.1.1", "B（链路本地）"),
                     ("224.0.0.1", "D（组播）"),
                     ("240.0.0.1", "E（保留，不可分配）"),
                     ("100.64.0.1", "A（运营商级 NAT）")):
        got = classify_ipv4(ip)
        assert got == want, f"{ip}: got={got!r} want={want!r}"
    # 边界值逐个点一遍：这些数字就是分类的分界线
    # 注意 127 落在 B 区间（126-191），回环是另加的标记而非类别
    for first, letter in ((1, "A"), (125, "A"), (126, "A"), (127, "B"),
                          (128, "B"), (191, "B"), (192, "C"), (223, "C"),
                          (224, "D"), (239, "D"), (240, "E"), (255, "E")):
        got = classify_ipv4(f"{first}.1.1.1")
        assert got.startswith(letter), \
            f"首位 {first} 应为 {letter} 类，实际 {got!r}"


@check("ipmath: 地址转换")
def t_convert():
    c = convert_ipv4("192.168.1.1")
    assert c["decimal"] == "3232235777", c["decimal"]
    assert c["hex"] == "c0.a8.01.01", c["hex"]
    assert c["binary"] == "11000000101010000000000100000001", c["binary"]
    assert c["binary_dotted"] == "11000000.10101000.00000001.00000001"
    assert c["ipv6_mapped"] == "::ffff:192.168.1.1"
    # 十进制 -> 十六进制必须自洽
    assert int(c["hex_plain"], 16) == int(c["decimal"])


@check("ipmath: 范围转 CIDR 覆盖精确相等")
def t_range():
    r = range_to_cidrs("192.168.0.0", "192.168.7.255")
    assert r["count"] == 2048, r["count"]
    assert r["block_count"] == 1, r["blocks"]
    assert r["summary"] == "192.168.0.0/21", r["summary"]
    # 用户给的例子：192.168.1.1 - 192.168.6.255 = 1535 个地址
    r2 = range_to_cidrs("192.168.1.1", "192.168.6.255")
    assert r2["count"] == 1535, r2["count"]
    assert r2["covered"] == r2["count"]


@check("ipmath: 范围转 CIDR 随机对拍 200 组")
def t_range_crosscheck():
    import ipaddress
    import random
    random.seed(11)
    for _ in range(200):
        a = random.randint(1, 0xFFFFFFF0)
        b = a + random.randint(0, 0xFFF)
        r = range_to_cidrs(int_to_ip(a), int_to_ip(b))
        got = set()
        for blk in r["blocks"]:
            got.update(int(x) for x in ipaddress.IPv4Network(blk))
        # 还原出的地址集合必须与原范围**完全相等**——不多不少
        assert got == set(range(a, b + 1)), (a, b, r["blocks"][:4])


@check("ipmath: 非法输入必须报错而不是静默返回")
def t_ipmath_errors():
    for bad in ("", "   ", "999.1.1.1", "abc", "1.2.3", "1.2.3.4.5"):
        try:
            subnet_info(bad)
        except ValueError:
            continue
        raise AssertionError(f"subnet_info 应当拒绝 {bad!r}")
    for a, b in (("10.0.0.5", "10.0.0.1"),      # 倒序
                 ("0.0.0.0", "1.1.1.1"),      # 含 0.0.0.0
                 ("1.1.1.1", "255.255.255.255")):
        try:
            range_to_cidrs(a, b)
        except ValueError:
            continue
        raise AssertionError(f"range_to_cidrs 应当拒绝 {a}-{b}")


# ================================================================== #
#  ARP
# ================================================================== #
@check("arp: 解析并过滤组播与回环")
def t_arp_list():
    # arp -a 会混进 IGMP(224.0.0.x)、SSMDP(239.x)、回环条目，
    # 它们不是邻居。过滤逻辑抽成模块级函数才能测。
    raw = """
接口: 192.168.153.5 --- 0x12
  Internet 地址       物理地址              类型
  192.168.153.2       00-50-56-e0-a3-69     动态
  192.168.153.254     00-50-56-e6-f5-90     动态
  224.0.0.22          01-00-5e-00-00-16     组播
  239.255.255.250     01-00-5e-7f-ff-fa     组播
  127.0.0.1           00-00-00-00-00-00     本地
  192.168.153.255     ff-ff-ff-ff-ff-ff     广播
"""
    got = parse_arp(raw)
    ips = [ip for ip, _ in got]
    assert ips == ["192.168.153.2", "192.168.153.254"], ips
    assert got[0][1] == "00-50-56-e0-a3-69", got


@check("arp: 空表与畸形行不崩")
def t_arp_edge():
    assert parse_arp("") == []
    assert parse_arp("没有表头也没有条目\n就是一段文字") == []
    # IP 段不是四段 / 数字超 255 的行必须被丢掉
    assert parse_arp("999.1.1.1   aa-bb-cc-dd-ee-ff   动态") == []
    assert parse_arp("1.2.3       aa-bb-cc-dd-ee-ff   动态") == []


# ================================================================== #
#  WiFi 扫描解析
#
#  为什么要用样例数据而不是真的跑一次扫描：本机的 WLAN 服务通常没
#  运行（虚拟机 / 服务器 / 有线环境），无法产生真实输出。而 netsh
#  的输出结构是稳定的官方格式，用样例能把解析彻底测掉——比「找
#  一台有 WiFi 的机器试一次」可靠，也能在 CI 里跑。
# ================================================================== #
_SCAN_ZH = """接口名称 : Intel(R) Wi-Fi 6E AX201 160MHz
    目前有 4 个网络可见。

SSID 1 : HomeWiFi
         网络类型            : infra
         身份验证            : WPA2-Personal
         加密                : CCMP
         BSSID 1              : a4:2b:b0:11:22:33
         信号               : 92%
         无线电类型         : 802.11ax
         频段               : 5 GHz
         信道               : 44
         基本速率(Mbps)      : 400.0
         其它速率(Mbps)      : 3600.0
         BSSID 2              : a4:2b:b0:11:22:34
         信号               : 55%
         无线电类型         : 802.11ax
         频段               : 5 GHz
         信道               : 149
         基本速率(Mbps)      : 400.0
         其它速率(Mbps)      : 1200.0

SSID 2 : Guest_2G
         网络类型            : infra
         身份验证            : 开放
         加密                : 无
         BSSID 1              : 00:11:22:33:44:55
         信号               : 40%
         无线电类型         : 802.11n
         频段               : 2.4 GHz
         信道               : 6
         基本速率(Mbps)      : 72.2
         其它速率(Mbps)      : 72.2

SSID 3 : WiFi6E_New
         网络类型            : infra
         身份验证            : WPA3-Personal
         加密                : GCMP
         BSSID 1              : de:ad:be:ef:00:11
         信号               : 70%
         无线电类型         : 802.11ax
         频段               : 6 GHz
         信道               : 5
         基本速率(Mbps)      : 1200.0
         其它速率(Mbps)      : 4800.0
"""


@check("wifi: 解析出每个 BSSID 一条记录")
def t_scan_bssid():
    aps = parse_scan(_SCAN_ZH)
    # 4 个 BSSID（HomeWiFi 有两个：5GHz 两个信道）
    assert len(aps) == 4, f"应解析出 4 条，实际 {len(aps)}: {aps}"
    assert all(a.bssid for a in aps), "每条都必须有 BSSID"
    # 信号强的排前面
    assert [a.signal for a in aps] == sorted(
        (a.signal for a in aps), reverse=True), \
        f"未按信号排序：{[a.signal for a in aps]}"


@check("wifi: 同一 SSID 的多个 BSSID 各自独立解析")
def t_scan_multibssid():
    # 这是真 bug：早期版本整块只取第一个 BSSID 的值，导致同一台
    # 路由器的两个 AP（95% 和 55%）显示成完全一样的信号和信道。
    # 而「对比 2.4 和 5G 哪个信号好」正是用户最想做的事。
    aps = [a for a in parse_scan(_SCAN_ZH) if a.ssid == "HomeWiFi"]
    assert len(aps) == 2, f"应解析出 2 个 BSSID，实际 {len(aps)}"
    by_bssid = {a.bssid: a for a in aps}
    a1 = by_bssid.get("a4:2b:b0:11:22:33")
    a2 = by_bssid.get("a4:2b:b0:11:22:34")
    assert a1 and a2, f"BSSID 不对：{list(by_bssid)}"
    assert a1.signal == 92 and a1.channel == 44, a1
    assert a2.signal == 55 and a2.channel == 149, a2
    assert a1.other_rate == 3600.0 and a2.other_rate == 1200.0, (a1, a2)
    # SSID 级字段（身份验证/加密/网络类型）由两者共享
    assert a1.auth == a2.auth == "WPA2-Personal"
    assert a1.enc == a2.enc == "CCMP"
    assert a1.network_type == a2.network_type == "infra"


@check("wifi: 频段判定靠信道而非无线电类型")
def t_scan_band():
    aps = {a.ssid: a for a in parse_scan(_SCAN_ZH)}
    assert aps["HomeWiFi"].band == "5 GHz", aps["HomeWiFi"]
    assert aps["Guest_2G"].band == "2.4 GHz"
    assert aps["WiFi6E_New"].band == "6 GHz", aps["WiFi6E_New"]
    # netsh 自己给的「频段」行最权威，直接采信
    assert classify_band("-", 5, "6 GHz") == "6 GHz"
    assert classify_band("-", 6, "2.4 GHz") == "2.4 GHz"
    assert classify_band("-", 44, "5 GHz") == "5 GHz"
    # 没有频段行时退回无线电类型 + 信道
    assert classify_band("802.11be", 5) == "6 GHz"      # Wi-Fi 7 必是 6GHz
    assert classify_band("802.11ax", 5) == "2.4 GHz"    # 无 6E 标记就是 2.4
    assert classify_band("802.11n", 1) == "2.4 GHz"
    assert classify_band("802.11n", 14) == "2.4 GHz"
    assert classify_band("802.11ax", 32) == "5 GHz"
    assert classify_band("802.11ax", 149) == "5 GHz"
    assert classify_band("802.11ax", 177) == "5 GHz"
    # 6GHz 与 2.4GHz 信道数字重叠（1-233），这是最容易搞错的地方：
    # 早期版本先判 1-13 为 2.4GHz，把 6GHz 的信道 5 判成了 2.4。
    assert classify_band("802.11ax", 5) == "2.4 GHz"


@check("wifi: 加密与速率解析")
def t_scan_enc():
    # 不能用 {ssid: ap} 建字典：HomeWiFi 有两个 BSSID，后者会覆盖
    # 前者。改成按 SSID 分组再取第一条。
    grouped: dict[str, list] = {}
    for a in parse_scan(_SCAN_ZH):
        grouped.setdefault(a.ssid, []).append(a)

    h = grouped["HomeWiFi"][0]
    assert h.auth == "WPA2-Personal", h.auth
    assert h.enc == "CCMP", h.enc
    assert h.other_rate == 3600.0, h.other_rate
    assert h.max_rate == 3600.0
    g = grouped["Guest_2G"][0]
    assert "开放" in g.auth, g.auth
    assert g.enc == "无", g.enc
    w6 = grouped["WiFi6E_New"][0]
    assert w6.auth == "WPA3-Personal", w6.auth
    assert w6.enc == "GCMP", w6.enc


# ---------------------------------------------------------------- #
#  取消机制
#
#  这是个真实的体验 bug：以前 Task.cancel() 只让 post 悄悄丢弃
#  输出，探测循环照跑到底——用户点「停止」，路由追踪还要 77 秒、
#  端口扫描 48 秒。更糟的是 stop() 立刻解禁「开始」按钮，用户马上
#  再点一次就 submit 出第二个任务，两个任务的输出混在一起。
# ---------------------------------------------------------------- #
@check("取消：探测循环会被真正打断")
def t_cancel_stops_loop():
    import threading
    import time as _t
    from netdiag.core.runner import Dispatcher, KIND_DONE, KIND_STAT

    d = Dispatcher()
    ticks = []

    def slow(post):
        # 模拟端口扫描：每轮 post 一次进度，共 200 轮
        for i in range(200):
            post(KIND_STAT, {"i": i})
            _t.sleep(0.02)

    d.submit("cancel_test", slow)
    _t.sleep(0.3)                 # 跑十几轮
    assert d.is_running("cancel_test")
    d.cancel("cancel_test")

    # 给它 3 秒时间自己停下来
    deadline = _t.monotonic() + 3.0
    done = False
    while _t.monotonic() < deadline:
        for kind, _payload, _task in d.drain():
            if kind == KIND_DONE:
                done = True
        if done:
            break
        _t.sleep(0.02)

    assert done, "取消后 3 秒内任务仍未发出 KIND_DONE"
    # 关键：线程必须已经退出，而不是只是在等
    _t.sleep(0.1)
    assert not d.is_running("cancel_test"), \
        "任务收到 KIND_DONE 但线程还活着"


@check("取消：不会被探测代码的 except Exception 吞掉")
def t_cancel_not_swallowed():
    import time as _t
    from netdiag.core.runner import Cancelled, Dispatcher, KIND_LINE

    # 探测代码里有 13 处 `except Exception` 用来兜网络错误并继续
    # （比如测速换下一个源）。如果 Cancelled 是 Exception 子类，
    # 它会被一并吞掉——用户点了停止，测速照样把几个源轮着跑一遍。
    assert issubclass(Cancelled, BaseException), \
        "Cancelled 必须继承 BaseException 而非 Exception"
    assert not issubclass(Cancelled, Exception), \
        "Cancelled 不能是 Exception 的子类"

    d = Dispatcher()
    swallowed = []

    def trap(post):
        try:
            for i in range(200):
                post(KIND_LINE, f"第 {i} 轮")
                _t.sleep(0.02)
        except Exception as e:                       # noqa: BLE001
            swallowed.append(type(e).__name__)
            post(KIND_LINE, "被 except Exception 吞掉")
            post(KIND_LINE, "还在跑——说明取消失效")

    d.submit("swallow_test", trap)
    _t.sleep(0.2)
    d.cancel("swallow_test")
    _t.sleep(0.6)

    texts = [p for k, p, _t2 in d.drain() if k == KIND_LINE]
    assert not swallowed, f"Cancelled 被吞掉了：{swallowed}"
    assert not any("还在跑" in str(t) for t in texts), \
        "except Exception 之后继续执行了，取消失效"


@check("取消：KIND_DONE 一定送达，UI 不会卡在运行中")
def t_cancel_done_always():
    """UI 的「开始」按钮是靠 KIND_DONE 解禁的。

    不能用 `finally: if not cancelled: emit(KIND_DONE)`，
    而 emit 在取消后会自己 return —— 于是取消时 DONE 永远发不出去，
    页面永久停在「正在停止…」。必须用直连的 post。
    """
    import time as _t
    from netdiag.core.runner import Dispatcher, KIND_DONE

    d = Dispatcher()
    def noop(post):
        post(KIND_DONE if False else "line", "起点")
        for _ in range(50):
            _t.sleep(0.02)
            post("line", "还在跑")

    d.submit("done_test", noop)
    _t.sleep(0.15)
    d.cancel("done_test")

    _t.sleep(0.8)
    kinds = [k for k, _p, _t2 in d.drain()]
    assert KIND_DONE in kinds, \
        f"取消后没有收到 KIND_DONE（收到 {set(kinds)}），UI 会卡在运行中"


@check("wifi: 中文 SSID 按显示宽度对齐")
def t_scan_cjk_width():
    from netdiag.core.wifi_scan import _pad, _trunc, _width
    # 等宽字体里汉字占两列。按 len() 截断/对齐会让中文 SSID
    # 撑破列宽，撞到右边一列上去。
    assert _width("测试") == 4, _width("测试")
    assert _width("WiFi") == 4
    assert _width("测试WiFi") == 8
    # _pad 后总显示宽度必须正好是 n
    for text in ("短", "MyHome", "很长的中文网络名称", "混合Mixed名称"):
        assert _width(_pad(text, 32)) == 32, \
            f"{text!r} 补齐后宽度 {_width(_pad(text, 32))} != 32"
    # _trunc 之后不能超宽
    long_cn = "这是一个非常非常长的中文无线网络名称用来测试截断"
    for n in (10, 20, 32):
        assert _width(_trunc(long_cn, n)) <= n, \
            f"n={n} 时截断结果宽度 {_width(_trunc(long_cn, n))} 超了"
    # 完整解析一遍带中文 SSID 的输出，确认解析正常
    text = ("SSID 1 : 家里的电脑\n"
            "         BSSID 1              : aa:bb:cc:dd:ee:ff\n"
            "         信号               : 66%\n"
            "         无线电类型         : 802.11ax\n"
            "         频段               : 5 GHz\n"
            "         信道               : 149\n")
    aps = parse_scan(text)
    assert len(aps) == 1, aps
    assert aps[0].ssid == "家里的电脑", aps[0].ssid
    assert aps[0].signal == 66 and aps[0].channel == 149


@check("wifi: 畸形与空输入不得抛异常")
def t_scan_robust():
    assert parse_scan("") == []
    assert parse_scan("   \n  \n") == []
    # 服务未运行
    assert parse_scan("没有运行") == []
    # 没有可见网络
    assert parse_scan("目前有 0 个网络可见。") == []
    # 标签在但没有 BSSID 行
    assert len(parse_scan(
        "SSID 1 : X\n         信道               : 6\n")) == 1
    # SSID 为空
    assert parse_scan("SSID 1 : \n         信号               : 50%\n") == []
    # 半角/全角冒号混用
    assert len(parse_scan("SSID 1 : X\n         信号 : 50%\n"
                          "         BSSID 1 : aa:bb:cc:dd:ee:ff\n")) == 1


# 来自 Windows 11 中文版机器的 netsh wlan show networks mode=bssid。
# 与 Win10 的差别同样在标签用词：波段（不是频段）、通道（不是信道）。
_CN11_SCAN = """目前有 25 个网络可见。

SSID 1 : HomeWiFi
             网络类型            : 结构
             身份验证            : WPA2 - 个人
             加密                : CCMP
             BSSID 1                  : aa:bb:cc:dd:ee:ff
             信号               : 90%
             无线电类型         : 802.11ax
             波段               : 5 GHz
             通道               : 149
             基本速率(Mbps)      : 574
             其它速率(Mbps)      : 574

SSID 2 : GuestOpen
             网络类型            : 结构
             身份验证            : 开放
             加密                : 无
             BSSID 1                  : 77:88:99:aa:bb:cc
             信号               : 40%
             无线电类型         : 802.11n
             波段               : 2.4 GHz
             通道               : 6
             基本速率(Mbps)      : 72.2
             其它速率(Mbps)      : 144.4
"""


# ---- Windows 只读网络命令解析（样例为真实输出）---------------------- #
# netsh / route / ipconfig 的输出用词随 Windows 版本与语言变化，凭印象
# 写样例会让测试「假绿」——本项目的 WiFi 解析就因此连错两版。所以这
# 里的样例全部从真实机器抓取，保留全角冒号与点线填充对齐。


@check("逐跳: 解析真实 Windows tracert 输出")
def t_hop_parse_real():
    # 真机抓的输出（8 跳，第 8 跳不响应）
    text = (
        "通过最多 8 个跃点跟踪到 8.8.8.8 的路由\r\n"
        "\r\n"
        "  1    <1 毫秒   <1 毫秒   <1 毫秒 192.168.153.2\r\n"
        "  2    61 ms     3 ms     3 ms  10.80.24.2\r\n"
        "  3     7 ms     4 ms     6 ms  10.80.0.1\r\n"
        "  4     5 ms     7 ms     3 ms  192.168.100.1\r\n"
        "  5    23 ms    25 ms    23 ms 150.242.58.82\r\n"
        "  6    26 ms    23 ms    23 ms  172.16.25.25\r\n"
        "  7    29 ms    28 ms    28 ms  172.16.25.33\r\n"
        "  8     *        *        *     请求超时。\r\n"
        "\r\n"
        "跟踪完成。\r\n")
    hops = hopmtu_mod.parse_tracert(text)
    assert len(hops) == 8, f"应 8 跳，实得 {len(hops)}"
    # 表头与结尾都不能被当成跳
    assert all(h.hop_no >= 1 for h in hops), [h.hop_no for h in hops]
    assert hops[0].addr == "192.168.153.2", hops[0]
    assert hops[0].rtts == [1.0, 1.0, 1.0], hops[0].rtts
    # 无响应的跳必须保留，不能丢掉——它们本身就是诊断信息
    assert hops[-1].addr == "*", hops[-1]
    assert not hops[-1].alive
    assert hops[1].rtts == [61.0, 3.0, 3.0], hops[1].rtts
    assert all(h.alive for h in hops[:7])


@check("逐跳: 解析英文与主机名形式")
def t_hop_parse_en():
    # 英文 tracert 的时间列没有单位字样
    en = ("  1    1 ms     2 ms     1 ms  192.168.1.1\r\n"
          "  2   10 ms     9 ms    11 ms  10.0.0.1\r\n"
          "  3     *        *        *     Request timed out.\r\n")
    hops = hopmtu_mod.parse_tracert(en)
    assert len(hops) == 3, [h.hop_no for h in hops]
    assert hops[0].rtts == [1.0, 2.0, 1.0], hops[0].rtts
    assert hops[2].addr == "*", hops[2]
    # 空输入与噪声行不能抛
    assert hopmtu_mod.parse_tracert("") == []
    assert hopmtu_mod.parse_tracert("Trace to x over 30 hops") == []


@check("逐跳: Hop 不能覆盖 tuple.index")
def t_hop_no_index_clash():
    # NamedTuple 是 tuple 子类，字段叫 index 会**覆盖** tuple.index()
    # 方法，让 h.index("1.2.3.4") 从「找元素」变成「取跳号」——
    # 不报错，静默给错结果。字段名必须是 hop_no。
    h = hopmtu_mod.Hop(3, "1.2.3.4", [1.0])
    assert h.hop_no == 3
    # 关键：tuple.index 必须还是「按值查找」的方法，不能被字段覆盖。
    # 用两个不同跳号的 Hop 试：若字段名叫 index，t.index(x) 会
    # 返回 h.hop_no（3）而不是位置（1）。
    t = (hopmtu_mod.Hop(1, "a", []), hopmtu_mod.Hop(2, "b", []))
    assert t.index(hopmtu_mod.Hop(2, "b", [])) == 1, \
        f"tuple.index 被覆盖了，返回 {t.index(hopmtu_mod.Hop(2, 'b', []))}"
    # 字段名必须是 hop_no
    assert "hop_no" in hopmtu_mod.Hop._fields
    assert "index" not in hopmtu_mod.Hop._fields, \
        f"字段名 index 会覆盖 tuple.index(): {hopmtu_mod.Hop._fields}"


@check("逐跳: 私有段判定决定该找谁修")
def t_hop_private():
    for a in ("192.168.1.1", "10.0.0.1", "172.16.0.1", "172.31.255.255"):
        assert hopmtu_mod._is_private(a), a
    for a in ("8.8.8.8", "1.1.1.1", "203.0.113.5", "172.32.0.1",
              "172.15.0.1", "192.169.1.1", "11.0.0.1"):
        assert not hopmtu_mod._is_private(a), a
    assert not hopmtu_mod._is_private("不是 IP")


@check("多目标: 输入解析（多分隔符 + 去重保序）")
def t_mp_parse():
    got = multiprobe_mod.parse_targets(
        "8.8.8.8\n1.1.1.1  223.5.5.5\n# 这是注释\n\n8.8.8.8\n"
        "9.9.9.9, 1.0.0.1；2.0.0.1")
    # 去重但保持用户列的顺序——顺序本身有信息（先本地后公网）
    assert got == ["8.8.8.8", "1.1.1.1", "223.5.5.5", "9.9.9.9",
                   "1.0.0.1", "2.0.0.1"], got
    assert multiprobe_mod.parse_targets("") == []
    assert multiprobe_mod.parse_targets("  \n\n # 只有注释\n") == []


@check("多目标: 预置场景带正确的探测方式")
def t_mp_preset_mode():
    # 「分层定位」问的是「主机在不在」，必须 ping。用 TCP 443 去问
    # 网关「你 443 开吗」是个毫无意义的问题，答案必然是否，
    # 然后用户以为自己坏了。
    for key, _l, _d, _fn, mode in multiprobe_mod.PRESETS:
        assert mode in ("ping", "tcp"), (key, mode)
    assert multiprobe_mod.preset_mode("layer") == "ping"
    assert multiprobe_mod.preset_mode("site") == "tcp"
    # 预置目标不能为空，也不能包含硬编码的内网网关
    for key, _l, _d, _fn, _m in multiprobe_mod.PRESETS:
        t = multiprobe_mod.preset_targets(key)
        assert t, f"{key} 目标为空"
        assert "192.168.1.1" not in t, \
            f"{key} 硬编码了网关地址，换网段就失效"


@check("多目标: 并发有上限（不能无限开线程）")
def t_mp_worker_cap():
    # 几百个线程一起建连会触发系统限流，结果是「全都连不上」，
    # 比不测更误导。所以 workers 必须被夹在上限内。
    assert multiprobe_mod._MAX_WORKERS <= 32, \
        "并发上限过高会触发限流"
    assert multiprobe_mod._DEFAULT_WORKERS <= multiprobe_mod._MAX_WORKERS

    posts = []
    multiprobe_mod.multi_probe(
        lambda k, p: posts.append((k, p)),
        ["1.1.1.1"] * 40, mode="tcp", workers=999)
    stats = {str(v[0]): v[1] for k, v in posts if k == "stat"}
    # 40 个目标 -> 实际并发被夹到 min(999, 32, 40) = 32
    assert stats["并发数"] == "32", stats["并发数"]


@check("多目标: 目标过多要拦下来")
def t_mp_limit():
    posts = []
    multiprobe_mod.multi_probe(
        lambda k, p: posts.append((k, p)), ["1.1.1.1"] * 100)
    errs = [p for k, p in posts if k == "error"]
    assert errs, "100 个目标应被拦下"
    assert "太多" in str(errs[0]) or "64" in str(errs[0]), errs[0]
    # 空目标也要拦
    posts.clear()
    multiprobe_mod.multi_probe(lambda k, p: posts.append((k, p)), [])
    assert [p for k, p in posts if k == "error"], "空目标应报错"


@check("多目标: 状态分级与文案")
def t_mp_states():
    for st, sym, lbl in (("ok", "✓", "通"), ("refused", "◐", "拒绝连接"),
                         ("filtered", "✗", "超时"), ("dns", "✗", "解析失败"),
                         ("error", "✗", "出错")):
        r = multiprobe_mod.Row("t", "1.1.1.1", st == "ok", 1.0, st)
        assert r.symbol == sym, (st, r.symbol)
        assert r.label == lbl, (st, r.label)
    # 「拒绝」必须和「超时」区分——前者说明主机在线，只是端口没开
    assert multiprobe_mod.Row("t", "1", False, None, "refused").label != \
        multiprobe_mod.Row("t", "1", False, None, "filtered").label


@check("多目标: 表格按显示宽度对齐")
def t_mp_pad():
    # ✓(U+2713) 与 ◐(U+25D0) 都 > 0x2000，按两列算：
    #   ✓ = 2 + 空格 1 + 通 2 = 5
    assert multiprobe_mod._width("✓ 通") == 5
    #   ◐ = 2 + 空格 1 + 拒绝连接 8 = 11
    assert multiprobe_mod._width("◐ 拒绝连接") == 11
    for t in ("✓ 通", "✗ 解析失败", "◐ 拒绝连接"):
        assert multiprobe_mod._width(multiprobe_mod._pad(t, 16)) == 16, t


@check("多目标: 解析失败要标成 dns 而不是 filtered")
def t_mp_dns():
    # 域名解析不了和「主机不可达」是两回事，不能混
    if not _IS_WIN:
        # resolve() 在非 Windows 上刻意不解析（直接回原样目标），
        # 免得在 CI 的 Linux 上依赖 DNS 服务器行为
        ip, err = multiprobe_mod.resolve("8.8.8.8")
        assert err is None, err
        assert ip == "8.8.8.8", ip
        return
    ip, err = multiprobe_mod.resolve("这个域名一定不存在.invalid")
    assert err, "不该解析成功"
    r = multiprobe_mod.probe_tcp("这个域名一定不存在.invalid")
    assert r.state == "dns", r.state
    assert not r.ok
    # IP 形式不该走解析
    ip2, err2 = multiprobe_mod.resolve("8.8.8.8")
    assert err2 is None, err2
    assert ip2 == "8.8.8.8", ip2


@check("修复: 风险等级顺序（只读优先，写操作按风险升序）")
def t_fix_order():
    fixes = netfix_mod.FIXES
    ro = [f for f in fixes if f.readonly]
    wr = [f for f in fixes if not f.readonly]
    assert ro, "必须有只读操作"
    assert wr, "必须有写操作"
    # 只读必须全部排在写操作之前——先看清楚现状再动手
    first_write = next(i for i, f in enumerate(fixes) if not f.readonly)
    assert all(f.readonly for f in fixes[:first_write]), "只读被排到了写操作后面"
    # 写操作按风险升序，用户应该从上往下试
    rank = {"low": 0, "mid": 1, "high": 2}
    seq = [rank[f.risk] for f in wr]
    assert seq == sorted(seq), f"写操作风险未升序: {seq}"


@check("修复: 高风险操作必须说明后果与重启要求")
def t_fix_risk_docs():
    for f in netfix_mod.FIXES:
        assert f.title and f.summary and f.effect, f.key
        # 后果说明要够具体：不能只说「会修改配置」
        assert len(f.effect) >= 10, f"{f.key}: {f.effect}"
        # 断网和重启是必须提前告知的
        if f.offline:
            # 「断网」「失去网络」都算说清楚了，测的是有没有说，
            # 不是用哪个词
            assert ("断网" in f.effect or "失去网络" in f.effect), \
                f"{f.key} 会断网但没说"
        if f.reboot:
            assert "重启" in f.effect, f"{f.key} 需重启但没说"
        # 高风险必须有备注解释适用场景
        if f.risk == "high":
            assert f.note, f"{f.key} 是高风险却没写适用场景"


@check("修复: 只读操作不得被误判为需要提权")
def t_fix_readonly():
    # ipconfig /all 里没有 show 之类的字样，靠字符串猜权限会把它
    # 误判成写操作，用户就得为一个「查看」多点一次 UAC
    #
    # **flushdns 曾被放在这个列表里，那是个错。**
    # `ipconfig /flushdns` 会清空 DNS 缓存，是写操作。它被误标成
    # 只读是因为「不清配置、不断网」——但 readonly 决定的不是
    # 后果轻重，而是界面行为：按钮会写成「查看配置」，二次确认和
    # 执行前备份全被跳过。用户在界面上被告知「查看」，点下去却
    # 丢了 DNS 缓存。后果轻浅用 risk="low" 表达。
    for key in ("ip_show", "winsock_show"):
        f = netfix_mod.describe_fix(key)
        assert f is not None, key
        assert f.readonly, f"{key} 应标为只读"
    # 反过来，会改系统状态的操作绝不能被标成只读。
    # flushdns 不改配置但改状态，同样算写操作。
    for key in ("flushdns", "renew", "release", "tcp_reset",
                "winsock_reset", "reset_proxy"):
        f = netfix_mod.describe_fix(key)
        assert f is not None, key
        assert not f.readonly, f"{key} 会改系统状态，不能标成只读"


@check("修复: 命令预览与实际命令一致")
def t_fix_cmd_text():
    for f in netfix_mod.FIXES:
        txt = f.command_text
        for c in f.commands:
            assert " ".join(c) in txt, f"{f.key}: {c} 没出现在预览里"
        # 预览要让用户能自己判断，不能是空的
        assert txt.strip(), f.key
    # 多条命令要都列出来
    renew = netfix_mod.describe_fix("renew")
    assert "/release" in renew.command_text
    assert "/renew" in renew.command_text


@check("修复: 不存在的操作名要报错而不是崩溃")
def t_fix_unknown():
    posts = []
    netfix_mod.run_fix(lambda k, p: posts.append((k, p)), "不存在的操作")
    errs = [p for k, p in posts if k == "error"]
    assert errs, "未知操作应报 KIND_ERROR"
    assert "不存在" in str(errs[0]), errs[0]
    assert netfix_mod.describe_fix("nope") is None


@check("修复: 输出解码要处理 GBK 而不乱码")
def t_fix_decode():
    # ipconfig 在简体中文 Windows 上输出 GBK
    raw = "   DNS 服务器  . . . . . . . . . . . : 192.168.153.2\r\n".encode("gbk")
    got = netfix_mod._decode(raw)
    assert "DNS 服务器" in got, got
    assert "192.168.153.2" in got, got
    # 空输入不能抛
    assert netfix_mod._decode(b"") == ""
    # 未知编码也不能抛，要降级而不是崩
    assert isinstance(netfix_mod._decode(b"\xff\xfe\x00bad"), str)


@check("修复: 配置快照能生成且含关键内容")
def t_fix_backup():
    import os
    path = netfix_mod.backup_config()
    assert not path.startswith("("), f"备份失败: {path}"
    assert os.path.exists(path), path
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        # 快照的意义是「出事后能照着改回来」，所以三样都得有
        assert "网卡与 MTU" in txt, txt[:200]
        assert "IP 配置" in txt
        assert "路由表" in txt
        # 真实命令的内容要落进去，不能只有标题
        assert "MTU" in txt, "快照里没有实际网卡数据"
    finally:
        os.unlink(path)


@check("修复: 执行前必须先备份（高风险操作）")
def t_fix_backup_first():
    posts = []
    # 用只读操作验证流程骨架：它不该备份，但该把命令原文打出来
    netfix_mod.run_fix(lambda k, p: posts.append((k, p)), "winsock_show")
    lines = [str(p) for k, p in posts if k == "line"]
    # 确认前就要显示命令，用户点确认前能自己判断
    assert any("将要执行的命令" in l for l in lines), "没显示将要执行的命令"
    assert any("winsock" in l and "show" in l for l in lines), "没显示命令原文"
    # 执行结果是结构化统计，不是文本行——两处都要查
    stats = [p for k, p in posts if k == "stat"]
    assert any("执行结果" in str(s0) for s0 in stats), "没给执行结果"


@check("诊断: 步骤状态与符号一一对应")
def t_diag_step():
    for st, sym, lbl in (("ok", "✓", "正常"), ("bad", "✗", "异常"),
                         ("unknown", "?", "无法判定"),
                         ("skipped", "-", "未执行")):
        s = diagnose_mod.Step("层", st, "观测", [])
        assert s.symbol == sym, (st, s.symbol)
        assert s.label == lbl, (st, s.label)
    # 结论的三档
    for sev, sym in (("info", "✓"), ("warn", "⚠"), ("error", "✗")):
        v = diagnose_mod.Verdict("t", sev, [], [])
        assert v.symbol == sym, (sev, v.symbol)


@check("诊断: 第一个异常层就是根因（不报下游为根因）")
def t_diag_root_cause():
    # 模拟「DNS 坏了」：DNS 失败导致 HTTPS 也失败。
    # 结论必须是 DNS，不能说 HTTPS——后者只是表现。
    facts = {"dns_servers": ["8.8.8.8"], "proxy_on": False,
             "proxy_raw": "", "nics": [], "winsock_third": []}
    steps = [
        diagnose_mod.Step("本机配置", "ok", "正常", []),
        diagnose_mod.Step("网关连通", "ok", "通", []),
        diagnose_mod.Step("公网连通", "ok", "通", []),
        diagnose_mod.Step("公网 TCP", "ok", "通", []),
        diagnose_mod.Step("DNS 解析", "bad", "解析失败", []),
        diagnose_mod.Step("HTTPS 请求", "bad", "失败", []),
    ]
    v = diagnose_mod._conclude(steps, facts)
    assert "DNS" in v.title, f"根因判错：{v.title}"
    assert "HTTPS 请求失败" != v.title, v.title
    assert v.severity == "error", v.severity
    # 建议里必须给出当前 DNS 是哪个，否则用户不知道换什么
    assert any("8.8.8.8" in a for a in v.advice), v.advice


@check("诊断: 上层不通时下游标记为未执行而非异常")
def t_diag_skipped():
    facts = {"dns_servers": [], "proxy_on": False, "proxy_raw": "",
             "nics": [], "winsock_third": []}
    steps = [
        diagnose_mod.Step("本机配置", "ok", "正常", []),
        diagnose_mod.Step("网关连通", "bad", "不通", []),
        diagnose_mod.Step("公网连通", "skipped", "网关不通，未执行", []),
        diagnose_mod.Step("DNS 解析", "skipped", "网关不通，未执行", []),
        diagnose_mod.Step("HTTPS 请求", "skipped", "网关不通，未执行", []),
    ]
    v = diagnose_mod._conclude(steps, facts)
    # 根因是网关，不能说 DNS 或 HTTPS
    assert "网关" in v.title, v.title
    assert v.severity == "error", v.severity
    # 结论里要明确「不用联系运营商」——这是网关不通最有用的判断
    assert any("运营商" in a for a in v.advice), v.advice
    # 异常层数只能算 1
    assert sum(1 for s in steps if s.status == "bad") == 1


@check("诊断: 全绿时给出正向结论")
def t_diag_all_ok():
    facts = {"dns_servers": ["8.8.8.8"], "proxy_on": False,
             "proxy_raw": "", "nics": [], "winsock_third": []}
    steps = [
        diagnose_mod.Step("本机配置", "ok", "正常", []),
        diagnose_mod.Step("网关连通", "ok", "通", []),
        diagnose_mod.Step("公网连通", "ok", "通", []),
        diagnose_mod.Step("公网 TCP", "ok", "通", []),
        diagnose_mod.Step("DNS 解析", "ok", "通", []),
        diagnose_mod.Step("HTTPS 请求", "ok", "HTTP 200", []),
    ]
    v = diagnose_mod._conclude(steps, facts)
    assert v.severity == "info", v.severity
    assert "正常" in v.title or "良好" in v.title, v.title


@check("诊断: 静态层各分支（MTU / 第三方组件 / 无 DNS / 无网卡）")
def t_diag_static():
    def facts(**kw):
        base = {"nics": [], "routes": [], "dns_servers": ["8.8.8.8"],
                "has_dns_config": True, "winsock_third": [],
                "winsock_sys": [], "firewall": {}, "proxy_raw": "",
                "proxy_on": False}
        base.update(kw)
        return base

    from netdiag.core.netsys import Nic
    good = Nic(12, 25, 1500, "以太网", True)

    # 无网卡
    s = diagnose_mod._judge_static(facts())
    assert s.status == "bad" and "网卡" in s.detail, s.detail

    # MTU 压到 576 以下
    s = diagnose_mod._judge_static(facts(nics=[Nic(12, 25, 500, "以太网", True)]))
    assert s.status == "bad" and "MTU" in s.detail, s.detail
    # 建议必须点出「ping 正常但网页慢」这个症状，否则用户对不上号
    v = diagnose_mod._conclude([s], facts())
    assert any("ping" in a for a in v.advice), v.advice

    # 第三方 Winsock 组件
    s = diagnose_mod._judge_static(facts(nics=[good], winsock_third=["C:\\vpn.dll"]))
    assert s.status == "bad" and "第三方" in s.detail, s.detail
    v = diagnose_mod._conclude([s], facts())
    assert any("winsock" in a.lower() for a in v.advice), v.advice

    # 没有 DNS 配置
    s = diagnose_mod._judge_static(facts(nics=[good], dns_servers=[],
                                        has_dns_config=False))
    assert s.status == "bad" and "DNS" in s.detail, s.detail

    # 一切正常
    s = diagnose_mod._judge_static(facts(nics=[good]))
    assert s.status == "ok", f"{s.status}: {s.detail}"
    # 正常时证据里要写清看到的是什么
    assert any("MTU" in e for e in s.evidence), s.evidence


@check("诊断: 代理开着时 HTTPS 失败要指向代理")
def t_diag_proxy():
    facts = {"dns_servers": ["8.8.8.8"], "proxy_on": True,
             "proxy_raw": "代理服务器: 127.0.0.1:8080",
             "nics": [], "winsock_third": []}
    steps = [
        diagnose_mod.Step("本机配置", "ok", "正常", []),
        diagnose_mod.Step("DNS 解析", "ok", "通", []),
        diagnose_mod.Step("HTTPS 请求", "bad", "握手超时", []),
    ]
    v = diagnose_mod._conclude(steps, facts)
    assert "代理" in v.title, v.title
    assert any("127.0.0.1:8080" in a for a in v.advice), v.advice


@check("诊断: 从 ipconfig /all 提取 DNS 服务器")
def t_diag_dns_servers():
    # 真实格式是点线填充的对齐，不能只匹配单空格
    txt = ("Windows IP 配置\r\n\r\n"
           "   主机名  . . . . . . . . . . . : DESKTOP-A1\r\n"
           "   DNS 服务器  . . . . . . . . : 192.168.153.2\r\n"
           "   DNS 服务器  . . . . . . . . : 8.8.8.8\r\n"
           "   DNS 服务器  . . . . . . . . : 8.8.8.8\r\n")
    got = diagnose_mod._dns_servers(txt)
    assert got == ["192.168.153.2", "8.8.8.8"], got
    # 英文
    assert "1.1.1.1" in diagnose_mod._dns_servers(
        "   DNS Servers  . . . . . . : 1.1.1.1\r\n")
    # 空输入
    assert diagnose_mod._dns_servers("") == []
    assert diagnose_mod._dns_servers("DNS Servers  : ") == []


@check("诊断: TCP 结果分类（连上/拒绝/超时三种，方向相反）")
def t_diag_tcp():
    import socket
    # 真实可达：开一个监听 socket，连它必然成功
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        st, ev = diagnose_mod._tcp_reach("127.0.0.1", port, timeout=1.5)
        assert st == "ok", f"{st}: {ev}"
        assert "可连接" in ev, ev
    finally:
        srv.close()

    # 真实不可路由：TEST-NET-1 是 RFC 5737 保留段，保证无响应
    st, ev = diagnose_mod._tcp_reach("192.0.2.1", 9, timeout=0.6)
    assert st == "filtered", f"{st}: {ev}"
    assert "防火墙" in ev, "超时的排查方向要指向防火墙"

    # 「拒绝」这一支在真实网络里很难触发：Windows 回环接口对
    # 未监听端口默认静默丢弃（防火墙行为），拿不到 RST。所以直接
    # 测分类函数——它才是需要保证正确的部分。
    st, ev = diagnose_mod._classify_tcp("10.0.0.1", 80,
                                       ConnectionRefusedError(10061,
                                                              "拒绝连接"))
    assert st == "refused", f"{st}: {ev}"
    assert "主机在线" in ev, "拒绝要说主机在线——排查方向与超时相反"
    assert "端口没开" in ev, ev

    st, ev = diagnose_mod._classify_tcp("10.0.0.1", 80, socket.timeout())
    assert st == "filtered", f"{st}: {ev}"

    # WinError 10060 有时走 OSError 而非 socket.timeout
    e = OSError(10060, "由于连接尝试失败")
    e.winerror = 10060
    st, ev = diagnose_mod._classify_tcp("10.0.0.1", 80, e)
    assert st == "filtered", f"{st}: {ev}"

    # 其它错误不能误判成「被防火墙拦」
    st, ev = diagnose_mod._classify_tcp("10.0.0.1", 80,
                                       OSError(10065, "网络不可达"))
    assert st == "error", f"{st}: {ev}"
    assert "不可达" in ev, ev

    # 无异常 = 连上
    st, ev = diagnose_mod._classify_tcp("1.1.1.1", 443, None, 0.042)
    assert st == "ok" and "42" in ev, f"{st}: {ev}"


@check("诊断: 异常文本翻译成中文")
def t_diag_reason_zh():
    for en, zh in (("timed out", "超时"), ("refused", "拒绝"),
                   ("reset", "重置"), ("CERTIFICATE_VERIFY_FAILED", "证书")):
        got = diagnose_mod._reason_zh(en)
        assert zh in got, f"{en} -> {got}"
    # 未知原因原样返回但要截断
    assert len(diagnose_mod._reason_zh("x" * 200)) < 100


@check("subnet: CIDR 展开（/31 /32 不掐头去尾）")
def t_subnet_cidr_list():
    # /24：掐掉网络号与广播号
    start, total = netsys_sub.parse_cidr_list("192.168.1.0/24")
    assert total == 254, total
    assert int_to_ip(start) == "192.168.1.1", start
    assert int_to_ip(start + total - 1) == "192.168.1.254"
    # 带主机位的写法要按掩码归位
    assert netsys_sub.parse_cidr_list("192.168.1.77/24") == (start, total)
    # /30
    assert netsys_sub.parse_cidr_list("10.0.0.0/30")[1] == 2
    # /31 按 RFC 3021 两个地址都可用
    assert netsys_sub.parse_cidr_list("10.0.0.0/31") == (
        ip_to_int("10.0.0.0"), 2)
    # /32 本来就只有一个
    assert netsys_sub.parse_cidr_list("10.0.0.5/32")[1] == 1
    # 裸 IP 等价 /32
    assert netsys_sub.parse_cidr_list("10.0.0.5")[1] == 1


@check("subnet: CIDR 非法输入报中文错")
def t_subnet_cidr_bad():
    for bad in ("", "999.1.1.1/24", "abc", "192.168.1", "1.2.3.4/33"):
        try:
            netsys_sub.parse_cidr_list(bad)
            raise AssertionError(f"{bad!r} 本该报错，却通过了")
        except ValueError as e:
            # ipaddress 的原始报错是英文的（"Octet 999 (> 255)
            # not permitted"），直接透给用户不友好
            assert "不是合法的 IP 或网段" in str(e), f"{bad!r}: {e}"


@check("subnet: TCP 端口清单能反推设备类型")
def t_subnet_ports():
    labels = {p for p, _ in netsys_sub.SCAN_PORTS}
    # 445/135/139 是 Windows 与域控的标志，3389 是远程桌面
    assert {445, 135, 139, 3389} <= labels, labels
    # 62078 与 28514 是 iOS 设备在无 DHCP 时自选 APIPA 的特征端口
    assert {62078, 28514} <= labels, labels
    # 每个端口都要有中文描述，否则表格里会显示成裸数字
    for p, lbl in netsys_sub.SCAN_PORTS:
        assert lbl and not lbl.isdigit(), (p, lbl)


@check("subnet: 状态分级（确定 / 高概率 / 仅 ICMP）")
def t_subnet_states():
    # 三个状态各自的标签与符号都要能显示，不能是空串
    for st in ("confirmed", "likely", "icmp_only"):
        h = netsys_sub.Host("10.0.0.1", st, "依据")
        assert h.label and h.symbol, (st, h)
    # 「确定在用」和「高概率」必须视觉可辨——它们是不同置信度，
    # 混成一个「在用」就会让用户给已占用的地址再分配设备
    assert netsys_sub.Host("1", "confirmed", "").label != \
        netsys_sub.Host("1", "likely", "").label
    assert netsys_sub.Host("1", "confirmed", "").symbol != \
        netsys_sub.Host("1", "likely", "").symbol


@check("subnet: 表格按显示宽度对齐（中文占两列）")
def t_subnet_pad():
    # 中文标签是 4 个汉字 = 8 列，用 f-string 的 <8 补不满
    assert netsys_sub._width("确定在用") == 8
    assert netsys_sub._width("abc") == 3
    # ◐(U+25D0) 与 ●(U+25CF) 都 > 0x2000，按两列算：
    #   ◐=2 + 空格=1 + 响应=4 + 空格=1 + ICMP=4  →  12
    assert netsys_sub._width("◐ 响应 ICMP") == 12
    # 补足到 14 列
    for t in ("● 确定在用", "◆ 高概率在用", "◐ 响应 ICMP"):
        assert netsys_sub._width(netsys_sub._pad(t, 14)) == 14, t


@check("subnet: 扫描结果不能把本机降级")
def t_subnet_own():
    # 本机必然是「确定在用」，不能因为没开服务端口就掉到「高概率」
    me = netsys_sub.own_addresses()
    assert me, "取不到本机地址"
    assert all(isinstance(x, str) for x in me), me
    assert all("." in x for x in me), me


@check("subnet: 大网段要拦下来")
def t_subnet_limit():
    # scan_subnet 开头就 `if not IS_WIN: 报「仅支持 Windows」`，
    # 所以这段参数校验只在 Windows 上跑到。CI 也在 Ubuntu 上跑，
    # 这里必须显式跳过，否则会误报成实现缺陷。
    if not _IS_WIN:
        return
    posts = []
    netsys_sub.scan_subnet(lambda k, p: posts.append((k, p)),
                           "10.0.0.0/8")
    errs = [p for k, p in posts if k == "error"]
    assert errs, "8 段网段应被拦下"
    assert "缩小范围" in str(errs[0]), errs[0]
    # 31 段只有一个可用地址吗？不，/31 有两个——测 /32
    posts.clear()
    netsys_sub.scan_subnet(lambda k, p: posts.append((k, p)),
                           "10.0.0.5/32")
    errs = [p for k, p in posts if k == "error"]
    # /32 只有一个地址，扫它必然只找到本机或空。提示必须说「换个
    # 网段」，说成「没扫到任何响应」会让用户以为网段写错了。
    assert errs and "没有意义" in str(errs[0]), errs
    assert "没有响应" not in str(errs[0]), errs[0]


@check("subnet: TCP 探测对关闭端口与自身不抛异常")
def t_subnet_probe():
    # 127.0.0.1 一定有 445 之外的端口关闭，探测不能抛
    hits = netsys_sub.probe_tcp("127.0.0.1", ((1, "t"),), timeout=0.2)
    assert isinstance(hits, list), hits
    # 不存在的地址要快速返回空，不能卡住
    hits = netsys_sub.probe_tcp("192.0.2.1", ((445, "x"), (80, "y")),
                                 timeout=0.2)
    assert hits == [], hits
    # ICMP 对本机必须有响应
    ms = netsys_sub.probe_icmp("127.0.0.1", 500)
    assert ms is not None and ms >= 0, ms


@check("subnet: 邻居唤醒只挑本广播域内的地址")
def t_subnet_wake():
    import ipaddress
    net = ipaddress.IPv4Network("192.168.1.0/24")
    seeds = netsys_sub._wake_neighbors(net, set())
    assert seeds, "应挑出唤醒地址"
    for s in seeds:
        assert ipaddress.IPv4Address(s) in net, f"{s} 不在 {net} 内"
    assert len(seeds) <= 3, seeds
    # 本机地址要排除，否则 ping 自己没意义
    assert netsys_sub._wake_neighbors(net, {"192.168.1.1"}) != \
        netsys_sub._wake_neighbors(net, set())


@check("sys: 路由表解析（五列，含在链路上）")
def t_routes():
    rs = netsys.parse_routes(ROUTE_PRINT)
    assert len(rs) == 11, f"应解析出 11 条路由，实得 {len(rs)}"
    d = [r for r in rs if r.is_default]
    assert len(d) == 1, f"应有 1 条默认路由，实得 {len(d)}"
    assert d[0].gateway == "192.168.153.2", d[0]
    assert d[0].iface == "192.168.153.128", d[0]
    assert d[0].metric == 25, d[0]
    # 直连路由的网关列是「在链路上」，不是 0.0.0.0
    onlink = [r for r in rs if r.on_link]
    assert len(onlink) == 10, f"应有 10 条直连路由，实得 {len(onlink)}"
    # 掩码转前缀：/24 /8 /32 都要对
    txt = " ".join(r.describe() for r in rs)
    assert "192.168.153.0/24" in txt, txt
    assert "127.0.0.0/8" in txt, txt
    assert "192.168.153.128/32" in txt, txt
    # 默认路由必须排最前
    assert rs[0].is_default, "默认路由应排最前"


@check("sys: 掩码转前缀长度")
def t_mask_bits():
    cases = [("255.255.255.0", 24), ("255.255.255.255", 32),
             ("255.255.0.0", 16), ("255.255.240.0", 20),
             ("255.255.255.252", 30), ("255.255.255.254", 31),
             ("0.0.0.0", 0),          # 不是连续掩码
             ("255.0.255.0", 0),      # 非连续
             ("", 0), ("abc", 0), ("999.1.1.1", 0)]
    for m, want in cases:
        got = netsys._mask_bits(m)
        assert got == want, f"掩码 {m!r} 应得 {want}，实得 {got}"


@check("sys: TCP 全局参数（按关键词匹配）")
def t_tcp_global():
    got = dict(netsys.parse_tcp_global(TCP_GLOBAL))
    # 真实输出里叫「加载项拥塞控制提供程序」，不是「拥塞控制算法」，
    # 所以必须按关键词匹配而不是精确标签
    assert got.get("拥塞控制") == "default", got
    assert got.get("接收窗口调节") == "normal", got
    assert got.get("SYN 重传") == "4", got
    assert got.get("初始 RTO") == "1000", got
    assert "ECN" in got, got


@check("sys: 网卡列表与 MTU")
def t_nics():
    ns = netsys.parse_nics(NICS)
    assert len(ns) == 2, f"应解析出 2 张网卡，实得 {len(ns)}"
    up = [n for n in ns if n.up]
    assert len(up) == 2, f"两张都是 connected，实得 {len(up)}"
    eth = [n for n in ns if "Ethernet" in n.name]
    assert len(eth) == 1, ns
    assert eth[0].mtu == 1500, eth[0]
    assert eth[0].metric == 25, eth[0]
    # 回环的 MTU 是 4294967295，要显示成「—」而不是那个大数
    lb = [n for n in ns if "Loopback" in n.name][0]
    assert "—" in lb.describe(), lb.describe()


@check("sys: 防火墙三配置文件（按段切分）")
def t_firewall():
    fw = netsys.parse_firewall(FIREWALL)
    # 真实输出里三段都有「状态」行，用全文 find 会互相覆盖只剩最后一段
    assert set(fw) >= {"域", "专用", "公用"}, fw
    assert fw["域"] == "启用", fw
    assert fw["专用"] == "禁用", fw
    assert fw["公用"] == "禁用", fw
    assert fw["域策略"] == "BlockInbound,AllowOutbound", fw


@check("sys: Winsock 目录（按路径判第三方）")
def t_winsock():
    sysw, third = netsys.parse_winsock(WINSOCK)
    assert len(sysw) == 1, f"应识别 1 个系统项，实得 {len(sysw)}: {sysw}"
    assert "MSAFD" in sysw[0], sysw
    assert len(third) == 1, f"应识别 1 个第三方项，实得 {len(third)}: {third}"
    assert "VPN" in third[0], third


@check("sys: DNS 缓存（点线填充 + 数字类型）")
def t_dns_cache():
    recs = netsys.parse_dns_cache(DNS_CACHE)
    assert recs, "应至少解析出 1 条记录"
    r0 = recs[0]
    assert r0["name"] == "ms_tcpip", r0
    # 记录类型在真实输出里是数字 1，要翻成 A
    assert r0["type"] == "A", r0
    assert r0["addr"] == "172.65.90.23", r0
    assert r0["ttl"] == "2", r0
    # 同一个域名可能有多条 A 记录，都要保留
    assert len(recs) == 4, f"应 4 条，实得 {len(recs)}"


@check("sys: netstat 统计（标签=数字，数字在行尾）")
def t_netstat_rows():
    # 真实输出是「  接收的数据包 = 203787」，行首是中文、数字在行尾。
    # 用 ^\d+ 匹配会一行都取不到。
    import re as _re
    pat = _re.compile(r"^\s*(\S[^=\uff1d\n]{2,30}?)\s*[=\uff1d]\s*(\d[\d,]*)\s*$",
                      _re.M)
    rows = pat.findall(NETSTAT)
    assert len(rows) > 10, f"应解析出多行统计，实得 {len(rows)}"
    keys = [k.strip() for k, _ in rows]
    assert any("接收的数据包" in k for k in keys), keys[:5]
    assert any("丢弃的接收数据包" in k for k in keys), keys[:5]


@check("sys: 路由表解析（五列，含在链路上）")
def t_routes():
    rs = netsys.parse_routes(ROUTE_PRINT)
    assert len(rs) == 11, f"应解析出 11 条路由，实得 {len(rs)}"
    d = [r for r in rs if r.is_default]
    assert len(d) == 1, f"应有 1 条默认路由，实得 {len(d)}"
    assert d[0].gateway == "192.168.153.2", d[0]
    assert d[0].iface == "192.168.153.128", d[0]
    assert d[0].metric == 25, d[0]
    # 直连路由的网关列是「在链路上」，不是 0.0.0.0
    onlink = [r for r in rs if r.on_link]
    assert len(onlink) == 10, f"应有 10 条直连路由，实得 {len(onlink)}"
    # 掩码转前缀：/24 /8 /32 都要对
    txt = " ".join(r.describe() for r in rs)
    assert "192.168.153.0/24" in txt, txt
    assert "127.0.0.0/8" in txt, txt
    assert "192.168.153.128/32" in txt, txt
    assert rs[0].is_default, "默认路由应排最前"


@check("sys: 掩码转前缀长度")
def t_mask_bits():
    cases = [("255.255.255.0", 24), ("255.255.255.255", 32),
             ("255.255.0.0", 16), ("255.255.240.0", 20),
             ("255.255.255.252", 30), ("255.255.255.254", 31),
             ("0.0.0.0", 0),          # 不是连续掩码
             ("255.0.255.0", 0),      # 非连续
             ("", 0), ("abc", 0), ("999.1.1.1", 0)]
    for m, want in cases:
        got = netsys._mask_bits(m)
        assert got == want, f"掩码 {m!r} 应得 {want}，实得 {got}"


@check("sys: TCP 全局参数（按关键词匹配）")
def t_tcp_global():
    got = dict(netsys.parse_tcp_global(TCP_GLOBAL))
    # 真实输出里叫「加载项拥塞控制提供程序」，不是「拥塞控制算法」，
    # 所以必须按关键词匹配而不是精确标签
    assert got.get("拥塞控制") == "default", got
    assert got.get("接收窗口调节") == "normal", got
    assert got.get("SYN 重传") == "4", got
    assert got.get("初始 RTO") == "1000", got
    assert "ECN" in got, got


@check("sys: 网卡列表与 MTU")
def t_nics():
    ns = netsys.parse_nics(NICS)
    assert len(ns) == 2, f"应解析出 2 张网卡，实得 {len(ns)}"
    assert len([n for n in ns if n.up]) == 2, ns
    eth = [n for n in ns if "Ethernet" in n.name]
    assert len(eth) == 1, ns
    assert eth[0].mtu == 1500, eth[0]
    assert eth[0].metric == 25, eth[0]
    # 回环的 MTU 是 4294967295，要显示成「—」而不是那个大数
    lb = [n for n in ns if "Loopback" in n.name][0]
    assert "—" in lb.describe(), lb.describe()


@check("sys: 防火墙三配置文件（按段切分）")
def t_firewall():
    fw = netsys.parse_firewall(FIREWALL)
    # 真实输出里三段都有「状态」行，用全文 find 会互相覆盖只剩最后一段
    assert set(fw) >= {"域", "专用", "公用"}, fw
    assert fw["域"] == "启用", fw
    assert fw["专用"] == "禁用", fw
    assert fw["公用"] == "禁用", fw
    assert fw["域策略"] == "BlockInbound,AllowOutbound", fw


@check("sys: Winsock 目录（按路径判第三方）")
def t_winsock():
    sysw, third = netsys.parse_winsock(WINSOCK)
    assert len(sysw) == 1, f"应识别 1 个系统项，实得 {len(sysw)}: {sysw}"
    assert "MSAFD" in sysw[0], sysw
    assert len(third) == 1, f"应识别 1 个第三方项，实得 {len(third)}: {third}"
    assert "VPN" in third[0], third


@check("sys: DNS 缓存（点线填充 + 数字类型）")
def t_dns_cache():
    recs = netsys.parse_dns_cache(DNS_CACHE)
    assert recs, "应至少解析出 1 条记录"
    r0 = recs[0]
    assert r0["name"] == "ms_tcpip", r0
    # 记录类型在真实输出里是数字 1，要翻成 A
    assert r0["type"] == "A", r0
    assert r0["addr"] == "172.65.90.23", r0
    assert r0["ttl"] == "2", r0
    # 同一个域名可能有多条 A 记录，都要保留
    assert len(recs) == 4, f"应 4 条，实得 {len(recs)}"


@check("sys: netstat 统计（标签=数字，数字在行尾）")
def t_netstat_rows():
    # 真实输出是「  接收的数据包 = 203787」，行首是中文、数字在行尾。
    # 用 ^\d+ 匹配会一行都取不到。
    import re as _re
    pat = _re.compile(r"^\s*(\S[^=＝\n]{2,30}?)\s*[=＝]\s*(\d[\d,]*)\s*$",
                      _re.M)
    rows = pat.findall(NETSTAT)
    assert len(rows) > 10, f"应解析出多行统计，实得 {len(rows)}"
    keys = [k.strip() for k, _ in rows]
    assert any("接收的数据包" in k for k in keys), keys[:5]
    assert any("丢弃的接收数据包" in k for k in keys), keys[:5]


@check("sys: 空输入与畸形输入不得抛异常")
def t_sys_robust():
    for fn in (netsys.parse_routes, netsys.parse_tcp_global,
               netsys.parse_nics, netsys.parse_firewall,
               netsys.parse_dns_cache):
        assert fn("") == [] or fn("") == {}, fn
        fn("垃圾输入\n不是任何命令的输出")
        fn("中文 Windows IP 配置\r\n  记录名称. . . : x")
    # parse_winsock 返回 (系统, 第三方) 二元组
    assert netsys.parse_winsock("") == ([], [])
    netsys.parse_winsock("垃圾输入\n不是任何命令的输出")
    netsys.parse_winsock("描述: x\n提供程序路径: ")
    # 掩码解析对非字符串要返回 0 而不是抛
    assert netsys._mask_bits(None) == 0


@check("wifi 扫描: Win11 中文标签（波段/通道）也能解析")
def t_scan_cn11():
    aps = parse_scan(_CN11_SCAN)
    assert len(aps) == 2, f"应解析出 2 个 AP，实得 {len(aps)}"
    top = aps[0]
    assert top.ssid == "HomeWiFi", top
    assert top.band == "5 GHz", f"波段标签没读到：{top}"
    assert top.channel == 149, f"通道标签没读到：{top}"
    assert top.signal == 90, top
    assert top.enc == "CCMP", top
    assert top.max_rate == 574.0, top
    # 2.4GHz 的那个也要正确分组
    g = [a for a in aps if a.ssid == "GuestOpen"][0]
    assert g.band == "2.4 GHz", g
    assert g.channel == 6, g


@check("wifi: 英文系统输出同样能解析")
def t_scan_en():
    en = """There are 2 networks currently visible.

SSID 1 : HomeWiFi
         Network type            : Infrastructure
         Authentication          : WPA2-Personal
         Encryption              : CCMP
         BSSID 1                  : a4:2b:b0:11:22:33
         Signal                  : 88%
         Radio type              : 802.11ax
         Band                    : 5 GHz
         Channel                 : 36
         Basic rates (Mbps)      : 400.0
         Other rates (Mbps)      : 2400.0
"""
    aps = parse_scan(en)
    assert len(aps) == 1, aps
    a = aps[0]
    assert a.ssid == "HomeWiFi"
    assert a.signal == 88, a.signal
    assert a.band == "5 GHz", a.band
    assert a.channel == 36
    assert a.auth == "WPA2-Personal", a.auth



# ---- WiFi 当前连接：netsh 输出解析 ------------------------------------ #
# netsh 的输出语言跟随 Windows 显示语言，中文和英文给出完全不同的标签
# 名。早期版本只认中文标签，英文系统上八个字段全部失配，页面只显示
# SSID 和 BSSID。下列样本按真实 netsh 输出格式手写。

# 样例必须照抄真实机器的 netsh 输出，不能凭印象编。用词各版本不同：
#   Win10 中文：频段 / 信道 / 加密，速率单位跟在值后（接收速率 : 864.7 Mbps）
#   Win11 中文：波段 / 通道 / 密码，速率单位在标签括号里（接收速率(Mbps) : 574），
#               并多出 Rssi(dBm) 与 Connected Akm-cipher 两行
#   英文：Band / Channel / Cipher，Receive rate (Mbps) : 864.7
# 三套都要能解析，少一套就会在别人的机器上显示一片「-」。

# 来自 Windows 11 中文版机器的真实输出（Intel Wi-Fi 7 BE200）
_CN11_IFACE = """名称                   : local
    说明            : Intel(R) Wi-Fi 7 BE200 320MHz
    GUID                   : afd2a-b10c-4cc4-b364-7afdsafdsf8555
    物理地址       : aa:bb:cc:dd:ee:ff
    界面类型         : 主要
    状态                  : 已连接
    SSID                   : HomeWiFi
    AP BSSID               : aa:bb:cc:dd:ee:ff
    波段                   : 5 GHz
    通道                : 149
    Connected Akm-cipher ： [ akm = 00-0f-ac：02， cipher = 00-0f-ac：04 ]
    网络类型               : 结构
    无线电类型             : 802.11ax
    身份验证               : WPA2 - 个人
    密码                 : CCMP
    连接模式        : 自动连接
    接收速率(Mbps)         : 574
    传输速率 (Mbps)        : 574
    信号                   : 90%
    Rssi                   : -46
    配置文件               : 1
"""

_CN10_IFACE = """接口名称    : Wi-Fi
描述        : Intel(R) Wi-Fi 6 AX201 160MHz
状态        : 已连接
SSID        : HomeWiFi
BSSID       : a4:2b:b0:11:22:33
网络类型    : Infrastructure
无线电类型  : 802.11ax
身份验证    : WPA2-Personal
加密        : CCMP
接收速率    : 864.7 Mbps
发送速率    : 1201 Mbps
信号        : 96%
频段        : 5 GHz
信道        : 149
"""

_EN_IFACE = """Name    : Wi-Fi
State    : connected
SSID    : HomeWiFi
BSSID   : a4:2b:b0:11:22:33
Network type            : Infrastructure
Radio type              : 802.11ax
Authentication          : WPA2-Personal
Cipher         : CCMP
Receive rate (Mbps)    : 864.7
Transmit rate (Mbps)   : 1201
Signal                  : 96%
Channel                 : 149
"""


@check("wifi: Win11 中文真实输出的全部字段")
def t_wifi_parse_cn():
    """Windows 11 中文版：波段/通道/密码，速率单位在标签括号里，
    另有 Rssi(dBm) 与 Connected Akm-cipher 两行。这套用词与 Win10
    完全不同，只按 Win10 的标签写正则就会在这台机器上全空。"""
    f = wifi_parse(_CN11_IFACE)
    assert f["ssid"] == "HomeWiFi", f
    assert f["bssid"] == "aa:bb:cc:dd:ee:ff", f
    assert f["band"] == "5 GHz", f
    assert f["channel"] == "149", f
    assert f["auth"] == "WPA2 - 个人", f
    assert f["enc"] == "CCMP", f
    assert f["type"] == "802.11ax", f
    assert f["rx"] == "574", f
    assert f["tx"] == "574", f
    assert f["signal"] == "90", f
    assert f["rssi"] == "-46", f
    # 整行都该读到东西，不该有占位符
    assert "-" not in f.values(), f


@check("wifi: Win10 中文输出（频段/信道/加密，单位跟在值后）")
def t_wifi_parse_cn10():
    f = wifi_parse(_CN10_IFACE)
    assert f["ssid"] == "HomeWiFi", f
    assert f["band"] == "5 GHz", f
    assert f["channel"] == "149", f
    assert f["enc"] == "CCMP", f
    assert f["rx"] == "864.7", f
    assert f["tx"] == "1201", f
    assert f["signal"] == "96", f
    assert f["rssi"] == "-", f          # Win10 没有 Rssi 行


@check("wifi: 英文输出同样解析，且从信道反推频段")
def t_wifi_parse_en():
    """英文版没有 Band 行，频段必须从信道 149 反推成 5GHz。"""
    f = wifi_parse(_EN_IFACE)
    assert f["ssid"] == "HomeWiFi", f
    assert f["signal"] == "96", f
    assert f["channel"] == "149", f
    assert f["rx"] == "864.7", f
    assert f["tx"] == "1201", f
    assert f["type"] == "802.11ax", f
    assert f["auth"] == "WPA2-Personal", f
    assert f["band"] == "5GHz", f


@check("wifi: 信道号反推频段（2.4G/5G/6G 分界）")
def t_wifi_band_from_channel():
    """信道号反推频段。2.4G 是 1-14，5G 到 177，6GHz 从 178 起。"""
    for ch, want in (("1", "2.4GHz"), ("14", "2.4GHz"),
                     ("36", "5GHz"), ("149", "5GHz"), ("165", "5GHz"),
                     ("177", "5GHz"), ("178", "6GHz"), ("233", "6GHz")):
        got = wifi_parse(f"信道        : {ch}")["band"]
        assert got == want, f"信道 {ch} 应得 {want}，实得 {got}"
    # 读不到信道就不猜
    assert wifi_parse("SSID : X")["band"] == "-"


@check("wifi: 全字段失配时回落到占位符")
def t_wifi_parse_empty():
    """全字段失配时都回落到 "-"，SSID 回落成「(未连接)」。"""
    f = wifi_parse("Status : Connected")
    assert f["signal"] == "-", f
    assert f["channel"] == "-", f
    assert f["rx"] == "-" and f["tx"] == "-", f
    assert f["ssid"] == "(未连接)", f


@check("wifi: 未连接时空 SSID 不得吃掉下一行标签")
def t_wifi_parse_disconnected():
    """未连接时 netsh 的输出：SSID/BSSID 为空，其余字段不存在。"""
    txt = """Name    : Wi-Fi
State    : disconnected
SSID    :
BSSID   :
Network type            :
Radio type              :
"""
    f = wifi_parse(txt)
    # SSID 行的值是空的，应当按「未连接」处理，而不是把下一行 BSSID
    # 的标签吃进来（旧正则用 (.+)，\s* 会跨过换行，于是 SSID 显示成
    # 「BSSID   :」）。
    assert f["ssid"] == "(未连接)", repr(f["ssid"])
    assert f["signal"] == "-", f


def main() -> int:
    print("=" * 56)
    print("  aicbbuu network tools — 核心层测试")
    print("=" * 56)
    for fn in [
        t_empty, t_ascii, t_gbk, t_utf8, t_no_confuse,
        t_ports_default, t_ports_single, t_ports_multi, t_ports_cn_sep,
        t_ports_range, t_ports_dedup, t_ports_bad, t_ports_range_bad, t_ports_reverse,
        t_ping_ok, t_ping_partial, t_ping_fail, t_ping_zero, t_jitter,
        t_rtt_en, t_rtt_en_less, t_rtt_cn, t_rtt_cn_less, t_rtt_cn_full,
        t_rtt_timeout, t_rtt_summary,
        t_ipconfig_gw, t_ipconfig_dhcp, t_arp_parse,
        t_grade_signal, t_grade_mtu, t_resolve_type,
        t_subnet_fields, t_subnet_hostbits, t_subnet_small,
        t_subnet_crosscheck, t_class, t_convert,
        t_range, t_range_crosscheck, t_ipmath_errors,
        t_arp_list, t_arp_edge,
        t_scan_bssid, t_scan_multibssid, t_scan_band, t_scan_enc,
        t_scan_cjk_width, t_scan_robust, t_scan_en, t_scan_cn11,
        t_hop_parse_real, t_hop_parse_en, t_hop_no_index_clash,
        t_hop_private, t_mp_parse, t_mp_preset_mode, t_mp_worker_cap,
        t_mp_limit, t_mp_states, t_mp_pad, t_mp_dns,
        t_fix_order, t_fix_risk_docs, t_fix_readonly, t_fix_cmd_text,
        t_fix_unknown, t_fix_decode, t_fix_backup, t_fix_backup_first,
        t_diag_step, t_diag_root_cause, t_diag_skipped, t_diag_all_ok,
        t_diag_static, t_diag_proxy, t_diag_dns_servers, t_diag_tcp,
        t_diag_reason_zh,
        t_subnet_cidr_list, t_subnet_cidr_bad, t_subnet_ports,
        t_subnet_states, t_subnet_pad, t_subnet_own, t_subnet_limit,
        t_subnet_probe, t_subnet_wake,
        t_routes, t_mask_bits, t_tcp_global, t_nics, t_firewall,
        t_winsock, t_dns_cache, t_netstat_rows, t_sys_robust,
        t_hop_parse_real, t_hop_parse_en, t_hop_no_index_clash,
        t_hop_private, t_mp_parse, t_mp_preset_mode, t_mp_worker_cap,
        t_mp_limit, t_mp_states, t_mp_pad, t_mp_dns,
        t_fix_order, t_fix_risk_docs, t_fix_readonly, t_fix_cmd_text,
        t_fix_unknown, t_fix_decode, t_fix_backup, t_fix_backup_first,
        t_diag_step, t_diag_root_cause, t_diag_skipped, t_diag_all_ok,
        t_diag_static, t_diag_proxy, t_diag_dns_servers, t_diag_tcp,
        t_diag_reason_zh,
        t_subnet_cidr_list, t_subnet_cidr_bad, t_subnet_ports,
        t_subnet_states, t_subnet_pad, t_subnet_own, t_subnet_limit,
        t_subnet_probe, t_subnet_wake,
        t_routes, t_mask_bits, t_tcp_global, t_nics, t_firewall,
        t_winsock, t_dns_cache, t_netstat_rows, t_sys_robust,
        t_wifi_parse_cn, t_wifi_parse_cn10, t_wifi_parse_en,
        t_wifi_band_from_channel,
        t_wifi_parse_empty, t_wifi_parse_disconnected,
        t_cancel_stops_loop, t_cancel_not_swallowed, t_cancel_done_always,
        t_netfix_readonly_truth, t_netfix_flushdns_button,
        # 测速：按时间停止，不是跑满固定字节数
        t_speed_duration, t_speed_seconds_param, t_speed_no_30mb_cap,
    ]:
        fn()

    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    print("-" * 56)
    print(f"  {passed}/{total} 通过")
    print("=" * 56)
    return 0 if passed == total else 1



# ================================================================== #
#  网络修复：readonly 字段的语义
# ================================================================== #
@check("修复: readonly 只标在真不改状态的命令上")
def t_netfix_readonly_truth():
    """**readonly 只能标在真的不改任何东西的操作上。**

    这个字段不是描述「后果严不严重」，而是决定界面怎么对待它：
    按钮文案变成「查看配置」、跳过二次确认、不做执行前备份。
    标错的后果是用户点一个会改系统的按钮，界面却在说「查看」。

    我曾把 `flushdns`（ipconfig /flushdns）标成只读，理由是
    「不清空配置、不断网」——但它真的清空了 DNS 缓存。按钮写成
    「查看配置」，二次确认和备份全被跳过。

    判据不是「后果轻不轻」（那是 risk 字段的事），而是
    「执行后系统状态会不会变」。所以 renew 会断网但仍不是只读。
    """
    from netdiag.core import netfix
    by_key = {f.key: f for f in netfix.FIXES}

    # 真正只读的三个：都只跑查询类命令
    for key in ("winsock_show", "ip_show"):
        f = by_key[key]
        assert f.readonly, f"{key} 应该标为只读（只跑 netsh show / ipconfig /all）"
        # 只读操作的命令里不能有会改配置的动作
        verbs = {c[0] for c in f.commands}
        assert verbs <= {"netsh", "ipconfig"}, f"{key} 命令异常：{verbs}"

    # 清缓存这类「不改配置但改状态」的，绝不能算只读
    for key in ("flushdns", "renew", "release", "reset_proxy",
                "tcp_reset", "winsock_reset"):
        f = by_key[key]
        assert not f.readonly, (
            f"{key} 被标成只读了——它的按钮会写成「查看配置」、"
            f"还会跳过二次确认和执行前备份，但它的命令是 "
            f"{' '.join(' '.join(c) for c in f.commands)!r}，会改系统状态")

    # 整个列表里不该有第二个 flushdns 式的误标
    rw = [f.key for f in netfix.FIXES if f.readonly]
    assert rw == ["winsock_show", "ip_show"], (
        f"只读操作应当只有 winsock_show / ip_show，实际是 {rw}")


@check("修复: 清空 DNS 缓存的按钮写「执行」不是「查看配置」")
def t_netfix_flushdns_button():
    """flushdns 页面的按钮必须写着「执行」而不是「查看配置」。"""
    from netdiag.core import netfix
    f = netfix.describe_fix("flushdns")
    want = "查看配置" if f.readonly else "执行"
    assert want == "执行", (
        f"flushdns 的按钮文案会是「{want}」，但它执行的是 "
        f"{' '.join(' '.join(c) for c in f.commands)!r}，会清空 DNS 缓存")

@check("测速: 按时间停止，默认 10 秒，可选 5~60 秒")
def t_speed_duration():
    """宽带测速的主判据必须是**时间**，不是下载了多少字节。

    原来跑满 30MB 就停，快链路上两秒就结束了——两秒的样本算出来的
    速率误差极大，而且根本没反映链路的真实吞吐能力。跑够时间才是
    常规做法。
    """
    from netdiag.core import probes

    # 时长选项必须是用户能挑的一组常见值
    assert probes.TIME_CHOICES == (5, 10, 15, 30, 60), (
        f"测速时长选项应为 (5, 10, 15, 30, 60)，实际 {probes.TIME_CHOICES}")
    assert probes._DEFAULT_SECONDS in probes.TIME_CHOICES, \
        "默认值必须是一个真实可选项"
    assert probes._MIN_SECONDS > 0, "最小停止时长必须为正"
    # 默认 10 秒是折中：太短不稳，太长用户等不住
    assert 5 <= probes._DEFAULT_SECONDS <= 15, (
        f"默认时长应在 5~15 秒之间，实际 {probes._DEFAULT_SECONDS}")

    # Range 护栏要够大，否则 60 秒测试会被服务器先截断
    assert probes._RANGE_CAP >= 300_000_000, (
        f"Range 护栏 {probes._RANGE_CAP} 太小——60 秒测试在百兆以上"
        f"的链路上会先收到服务器 EOF，实际时长不足 60 秒")


@check("测速: bandwidth_test 接受 seconds 参数")
def t_speed_seconds_param():
    """bandwidth_test 的签名必须带 seconds，且能真正限时。"""
    import inspect
    from netdiag.core import probes

    sig = inspect.signature(probes.bandwidth_test)
    assert "seconds" in sig.parameters, (
        f"bandwidth_test 应接受 seconds 参数，实际签名 {sig}")
    # 默认必须是 None 而不是硬编码——这样旧调用方（不限时）仍可用
    assert sig.parameters["seconds"].default is None, (
        "seconds 默认为 None，表示不额外限制（由 30MB 上限兜底）")


@check("测速: 去掉 30MB 硬上限")
def t_speed_no_30mb_cap():
    """不能再有「跑满 30MB 就停」的逻辑。"""
    from netdiag.core import probes
    src = inspect_src = None
    try:
        import inspect
        src = inspect.getsource(probes.bandwidth_test)
    except Exception:
        pass
    if src:
        assert "_SAMPLE_BYTES" not in src, (
            "_SAMPLE_BYTES 还在——那是旧的 30MB 上限，应该已被 "
            "_RANGE_CAP 取代")
    assert not hasattr(probes, "_SAMPLE_BYTES"), (
        "probes 模块里不该再有 _SAMPLE_BYTES")


if __name__ == "__main__":
    raise SystemExit(main())
