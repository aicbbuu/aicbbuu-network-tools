"""纯计算型 IP 工具。

与 probes_ext 分开：这里的函数**不碰系统、不发网络请求**，
纯粹是四则运算式的位运算，因此可以毫无副作用地被直接调用、
被单元测试穷举验证。UI 侧同步算完就显示，不需要走 Dispatcher
后台线程（那样反而慢，还要处理线程安全）。
"""
from __future__ import annotations

import ipaddress
from typing import Any

__all__ = [
    "parse_cidr", "subnet_info", "classify_ipv4",
    "convert_ipv4", "range_to_cidrs", "ip_to_int", "int_to_ip",
]


#: 旧式 IP 类别上界（首位字节），从高到低。表驱动而不是散落的
#: if/elif：手写区间已经因为边界手滑错了三次。
_CLASS_TABLE: tuple[tuple[int, str], ...] = (
    (126, "A"), (191, "B"), (223, "C"), (239, "D"), (255, "E"),
)


# ---------------------------------------------------------------- #
#  基础
# ---------------------------------------------------------------- #
def ip_to_int(ip: str) -> int:
    return int(ipaddress.IPv4Address(ip.strip()))


def int_to_ip(n: int) -> str:
    return str(ipaddress.IPv4Address(n))


def parse_cidr(text: str) -> ipaddress.IPv4Network:
    """解析 ``192.168.0.0/24`` 或裸 IP（隐含 /32）。

    刻意**不**直接 ``IPv4Network(text)``：那个类默认
    ``strict=True``，输入 ``192.168.1.5/24`` 会抛异常，而用户
    在子网计算器里填 IP 时十有八九带主机位——那正是要算的东西。
    """
    t = text.strip()
    if not t:
        raise ValueError("请填写 IP 或网段")
    if "/" not in t:
        t += "/32"
    return ipaddress.IPv4Network(t, strict=False)


# ---------------------------------------------------------------- #
#  1. IPv4 子网计算器
# ---------------------------------------------------------------- #
def subnet_info(text: str) -> dict[str, Any]:
    """算出一个网段的所有派生信息。

    全程用**整数**算，不用 IPv4Address 做加减——后者带边界检查，
    算 /32 那种情况时会因为越界直接抛 AddressValueError
    （net_addr + total - 2 在 /32 下算出 2**32）。
    """
    net = parse_cidr(text)
    prefix = net.prefixlen

    base = int(net.network_address)
    mask = int(net.netmask)
    wild = int(net.hostmask)
    total = 1 << (32 - prefix)          # 网络规模 = 主机位数的补

    # 网络规模 vs 可用主机数：/31 没有网络号和广播号之分（RFC 3021
    # 的点到点链路），/32 只有一个地址。
    usable = total if prefix >= 31 else total - 2

    # /32 只有 1 个地址，既没有网络号也没有广播号之分；/31 按 RFC 3021
    # 两个地址都可用。只有 /30 及更常规的掩码才需要掐头去尾。
    #
    # 早期写成 `if usable > 0`，/32 时 usable=1 也会走 +1 / -2 分支，
    # 于是 10.0.0.5/32 报出 first=10.0.0.6、last=10.0.0.4——首尾
    # 倒置，比不给还糟。
    if prefix == 32:
        first = last = base
    elif prefix == 31:
        first, last = base, base + 1
    else:
        first, last = base + 1, base + total - 2

    return {
        "cidr": f"{int_to_ip(base)}/{prefix}",
        "network": int_to_ip(base),
        "netmask": str(net.netmask),
        # i=0 对应最高位字节。写成 >> (8 * i) 会把字节序反过来，
        # 255.255.255.0 会输出成 00000000.11111111.11111111.11111111。
        "netmask_bin": ".".join(
            format((mask >> (8 * (3 - i))) & 0xFF, "08b") for i in range(4)),
        "prefix": f"/{prefix}",
        "wildcard": str(net.hostmask),
        "wildcard_bin": ".".join(
            format((wild >> (8 * (3 - i))) & 0xFF, "08b") for i in range(4)),
        "total": total,
        "usable": usable,
        "first": int_to_ip(first),
        "last": int_to_ip(last),
        "broadcast": int_to_ip(base + total - 1),
        "class": classify_ipv4(int_to_ip(base)),
        "is_private": net.is_private,
    }


def classify_ipv4(ip: str) -> str:
    """IP 类别（A-E）与私有性说明。

    传统 A/B/C 类分类在 1980 年代后已被 CIDR 取代，保留它只是
    便于对照老教材；同时给出更有实用价值的私有/保留标记。
    """
    n = ip_to_int(ip)
    first = n >> 24
    second = (n >> 16) & 0xFF

    # 类别：按首位区间分（这是旧式 A/B/C/D 分类的标准做法）。
    # 早期版本写的是 tuple[first-1] 索引，first=192 直接越界；
    # 后来改成 min(first, 4)，又把所有 4-223 全压成 D。
    # 旧式 A/B/C/D/E 分类按**首位二进制的高位前缀**划分：
    #   0xxxxxxx  (1-126)  → A
    #   10xxxxxx  (128-191)→ B
    #   110xxxxx  (192-223)→ C
    #   1110xxxx  (224-239)→ D
    #   1111xxxx  (240-255)→ E
    #
    # 这里踩过两次：先写 tuple[first-1]（192 直接越界），改成
    # min(first,4) 后又把 4-223 全压成 D，最后 <128/<224 两段
    # 又把 128-191 错划成 C。改成表驱动后不会再有边界手滑。
    cls = next(
        (letter for hi, letter in _CLASS_TABLE if first <= hi), "E")

    # 私有与保留标记比旧式分类实用得多，所以两者一起给。
    if first == 10:
        return f"{cls}（私有地址）"
    if 172 <= first <= 192 and 16 <= second <= 31:
        return f"{cls}（私有地址）"
    if first == 192 and second == 168:
        return f"{cls}（私有地址）"
    if first == 100 and 64 <= second <= 127:
        return f"{cls}（运营商级 NAT）"
    if first == 127:
        return f"{cls}（本机回环）"
    if first == 169 and second == 254:
        return f"{cls}（链路本地）"
    if 224 <= first <= 239:
        return f"{cls}（组播）"
    if first >= 240:
        return f"{cls}（保留，不可分配）"
    return cls


# ---------------------------------------------------------------- #
#  2. IPv4 地址转换器
# ---------------------------------------------------------------- #
def convert_ipv4(ip: str) -> dict[str, str]:
    """十进制 / 二进制 / 十六进制 / IPv6 四种表示。"""
    addr = ipaddress.IPv4Address(ip.strip())
    n = int(addr)

    b32 = f"{n:032b}"
    bin_dotted = ".".join(b32[i:i + 8] for i in range(0, 32, 8))
    hex_dotted = ".".join(f"{x:02x}" for x in addr.packed)

    # 映射成 IPv6：::ffff:a.b.c.d（IPv4-mapped），这是最常被问到的
    # 「这个 IPv4 用 IPv6 怎么写」。同时给 ::a.b.c.d 和纯十六进制。
    v6_mapped = f"::ffff:{ip.strip()}"
    v6_short = f"::{ip.strip()}"
    v6_hex = f"{n:032x}"
    v6_full = ":".join(v6_hex[i:i + 4] for i in range(0, 32, 4))

    return {
        "ip": str(addr),
        "decimal": str(n),
        "binary": b32,
        "binary_dotted": bin_dotted,
        "hex": hex_dotted,
        "hex_plain": f"0x{n:08x}",
        "ipv6": v6_full,
        "ipv6_mapped": v6_mapped,
        "ipv6_short": v6_short,
    }


# ---------------------------------------------------------------- #
#  3. IPv4 范围扩展器
# ---------------------------------------------------------------- #
def range_to_cidrs(start: str, end: str) -> dict[str, Any]:
    """把一段地址范围压成最少的 CIDR 块。

    「最少」很关键：192.168.0.0-192.168.7.255 可以用一个 /21 表示，
    但也能拆成 16 个 /24。返回 CIDR 列表让用户看到汇总写法。

    算法：Greedy——从 start 找最大的、对齐的、能装进剩余范围的块，
    反复取直到覆盖完。这是标准的 RFC 1518 类问题，贪心即最优。
    """
    s = ip_to_int(start)
    e = ip_to_int(end)
    if s > e:
        raise ValueError("起始地址不能大于终端地址")
    if s == 0 or e == 0xFFFFFFFF:
        raise ValueError("范围不能包含 0.0.0.0 或 255.255.255.255")

    blocks: list[str] = []
    cur = s
    while cur <= e:
        # 1) 受起始地址对齐限制：cur 的对齐粒度
        # 2) 受剩余容量限制
        max_size = 1
        while cur % (max_size * 2) == 0 and cur + max_size * 2 - 1 <= e:
            max_size *= 2
        size = max_size
        while cur + size - 1 > e:
            size //= 2
        # 3) 块大小不能越过 /0 上界
        while size > 1 and (cur + size - 1) > 0xFFFFFFFF:
            size //= 2

        end_of_block = cur + size - 1
        prefix = 32 - (size.bit_length() - 1)
        blocks.append(f"{int_to_ip(cur)}/{prefix}")
        cur = end_of_block + 1

    total = e - s + 1
    covered = sum(
        1 << (32 - int(b.split("/")[1])) for b in blocks)

    return {
        "start": int_to_ip(s),
        "end": int_to_ip(e),
        "count": total,
        "blocks": blocks,
        "block_count": len(blocks),
        # 汇总成一个块时给出它，否则说明「压不成一个」
        "summary": blocks[0] if len(blocks) == 1 else
                   f"{len(blocks)} 个 CIDR 块（无法用一个表示）",
        "covered": covered,
        "exact": covered == total,
    }
