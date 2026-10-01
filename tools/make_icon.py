"""
aicbbuu network tools — 图标生成器 v8（蓝紫环 + 黑心 + 细橙弧 + 波形断点）

设计
----
沿用自家 MemoryBlackHole 的视觉语言：**深空天体**。

  蓝紫渐变环 → 纯黑圆心 → 中心暖光点 → 橙色细弧（吸积盘）

保持家族感，但中心图形换成「网络诊断」自己的东西——

  一道白色波形，中间断开，断点处一个橙色亮点。

波形正好被斜弧压断，「信号被切断 / 链路中断」的意思不用文字也读得出来。
这是纯几何叙事：**这个软件做的事，就是找出信号断在哪。**

小尺寸（≤32px）走简化解剖：波形退化成一道弧，斜弧减到一道。
16px 下任何装饰都会糊成一团，能留的只有「环 + 一点」这个剪影。

全部由几何与距离场计算生成，不使用任何第三方图形库、现成素材或字体
渲染，因此不涉及图标库授权，也不依赖任何字体是否存在。

输出：7 种尺寸的 PNG + 一个多尺寸 ICO
"""
from __future__ import annotations

import math
import os
import struct
import zlib
from pathlib import Path

OUT_DIR = str(Path(__file__).resolve().parent.parent / "netdiag" / "assets")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------- #
#  配色
# ---------------------------------------------------------------- #
#  环：外缘深蓝紫 → 内缘亮紫。深浅差拉出「厚度」的体积感。
#  心：近黑但带一点紫，不能纯黑——纯黑在深色背景上会和底色糊掉。
#  弧 / 点：暖橙，呼应 MemoryBlackHole 的吸积盘。
RING_OUT = (124, 92, 240)
RING_IN = (168, 132, 255)
CORE_HI = (28, 24, 48)
CORE = (12, 10, 22)
ACCENT = (253, 186, 116)
WHITE = (250, 246, 240)

R_OUT = 0.485     # 外圆半径（0.5 留出抗锯齿余量）
RING_W = 0.115    # 环的径向厚度

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _lerp(a, b, t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return (int(round(a[0] + (b[0] - a[0]) * t)),
            int(round(a[1] + (b[1] - a[1]) * t)),
            int(round(a[2] + (b[2] - a[2]) * t)))


# 圆形网的半径（占图标宽度的比例），留出与外圈的间距
GLOBE_R = 0.272
# 经线的经度（度）。0 是正对读者的经线，画成一条直线。
MERIDIANS = (0.0, 46.0, -46.0, 80.0, -80.0)
# 纬线的纬度（度）。0 是赤道，画成最宽的那条。
PARALLELS = (0.0, 34.0, -34.0, 62.0, -62.0)


def _globe(nx, ny, col, simple, accent, white):
    """在 (nx, ny) 处画一个圆形网，返回新的颜色。

    坐标系与 _render 一致：nx/ny 是 0..1 的像素位置，圆心在 (0.5, 0.5)。

    **不要用「解析曲线方程再算距离」那套**——经线是极扁的椭圆，
    rx 小到 0.2 时 |v-1|*rx*GLOBE_R 的换算误差会大过线宽，线就
    溢出成一条竖白带（我第一版就是这么废的）。

    改成参数化采样：沿曲线取 N 个点，找**离当前像素最近的那个点**
    的距离。点数够密（每 4 像素一个）就足够准，而且天然抗锯齿。
    """
    ex, ey = nx - 0.5, ny - 0.5
    d = math.hypot(ex, ey)

    # 球外：留出「轮廓线」的宽度，其余不动
    if d > GLOBE_R + 0.022:
        return col

    lw = 0.0130 if simple else 0.0092      # 线宽

    # ---- 球体填充：中心略亮、边缘暗，制造球感 ----
    if d <= GLOBE_R:
        shade = 1.0 - (d / GLOBE_R) ** 3
        col = _lerp((30, 36, 54), (58, 70, 98), shade)
    else:
        col = _lerp(col, accent, min(1.0, (0.022 - (d - GLOBE_R)) / 0.012))

    # ---- 线：解析求交，不用参数采样 ----
    #
    # 参数采样（每 4 像素取一点，再找最近点）对**扁椭圆**失效：
    # ry 越小，采样点越集中在两端，中间一大段没点，于是纬线看着
    # 只有上半截。而且每个像素都要遍历全部采样点，256px 图要跑
    # 五分钟。
    #
    # 这里直接解方程。纬线 ry 已知，纵坐标 ey 已知，横坐标就是
    # ex = ±rx*sqrt(1-(ey/ry)^2) —— 取绝对值，因为左右对称。
    # 经线是竖椭圆，同理 rx 已知，解出 ey。
    best = lw
    hit = None

    # 60°/52° 是折中：再往外（比如 74°/68°）线就贴到轮廓上，
    # 与橙环叠成一条脏边；再往里（比如 30°）球面太空、像靶心。
    lons = (0.0, 58.0, -58.0) if simple else (0.0, 40.0, -40.0, 62.0, -62.0)
    lats = (0.0,) if simple else (0.0, 34.0, -34.0, 52.0, -52.0)

    # 纬线：|ey| 必须落在该纬线的半高之内
    for lat in lats:
        ry = abs(math.sin(math.radians(lat))) * GLOBE_R
        if ry < 0.012 or abs(ey) > ry:
            continue
        u = ey / ry
        rx = GLOBE_R * math.sqrt(max(0.0, 1.0 - u * u))
        dd = abs(abs(ex) - rx)
        if dd < best:
            best, hit = dd, ("p", abs(lat) < 0.5)

    # 经线：|ex| 必须落在该经线的半宽之内
    for lon in lons:
        rx = abs(math.cos(math.radians(lon))) * GLOBE_R
        if rx < 0.012 or abs(ex) > rx:
            continue
        u = ex / rx
        ry = GLOBE_R * math.sqrt(max(0.0, 1.0 - u * u))
        dd = abs(abs(ey) - ry)
        if dd < best:
            best, hit = dd, ("m", abs(lon) < 0.5)

    # ---- 球的外轮廓 ----
    edge = abs(d - GLOBE_R)
    if edge < 0.011 and (hit is None or edge < best):
        col = _lerp(col, accent, min(1.0, edge / 0.011) * 0.78)

    # ---- 线本身 ----
    if hit is not None and best < lw:
        k = min(1.0, (lw - best) / (lw * 0.40))
        col = _lerp(col, accent if hit[1] else white, k * 0.88)

    # ---- 中心暖光点：焦点，也是「正在连接」的意思 ----
    if d < 0.038:
        col = _lerp(col, accent, 0.50 + 0.50 * (1.0 - (d / 0.038) ** 2))
    elif d < 0.066:
        k = 1.0 - (d / 0.066)
        col = _lerp(col, accent, 0.13 * k * k)
    return col


def _render(size: int, simple: bool) -> bytearray:
    """渲染一张 size×size 的 RGBA 图标。

    simple=True 走小尺寸简化路径（16/24/32）。
    """
    ss = 4                       # 4x4 超采样
    S = size * ss
    px = bytearray(S * S * 4)
    inv = 1.0 / S

    # 小尺寸：斜弧从 3 道减到 1 道
    arcs = ((0.36, 0.42, 0.125),) if simple else (
        (0.36, 0.42, 0.125), (-0.28, 0.26, 0.105), (0.10, 0.18, 0.150))

    for y in range(S):
        ny = (y + 0.5) * inv
        for x in range(S):
            nx = (x + 0.5) * inv
            i = (y * S + x) * 4

            dx, dy = nx - 0.5, ny - 0.5
            dist = math.hypot(dx, dy)
            if dist > R_OUT + 0.004:
                continue

            # ---- 底：环 + 心 ----
            t = (R_OUT - dist) / RING_W
            if t > 1.0:
                k = max(0.0, (dist - (R_OUT - RING_W)) / 0.05)
                col = _lerp(CORE_HI, CORE, min(1.0, k))
            else:
                col = _lerp(RING_OUT, RING_IN, t)
                # 左上高光：像光从那个方向照在环上
                hl = max(0.0, 1.0 - math.hypot(nx - 0.30, ny - 0.26) / 0.42)
                col = _lerp(col, (208, 190, 255), hl * 0.45)
                # 环的内外描边，边缘才立得住
                if t < 0.06:
                    col = _lerp(col, (76, 52, 168), 1.0 - t / 0.06)
                elif t > 0.94:
                    col = _lerp(col, (196, 168, 255), (t - 0.94) / 0.06)

            # ---- 中心：圆形网（寓意互联网）----
            #
            # 一个球面网格：三条经线（纵向椭圆）+ 两条纬线（横向椭圆），
            # 画成一个球。正对读者的经线是一条直线，左右两条随经度收窄
            # 成椭圆弧；纬线按投影压扁成椭圆。这样不画经纬交点也能读成
            # 「网」而不是「靶心」——交点太多在小尺寸下会糊成一团。
            #
            # 粗细随缩放收：16/24/32 只画 2 条纬线 + 1 组经线，
            # 否则线条挤在一起连不成形。
            col = _globe(nx, ny, col, simple, ACCENT, WHITE)

            px[i] = col[0]
            px[i + 1] = col[1]
            px[i + 2] = col[2]
            px[i + 3] = 255

    # 盒式降采样求均值
    out = bytearray(size * size * 4)
    n = ss * ss
    for y in range(size):
        for x in range(size):
            r_ = g_ = b_ = a_ = 0
            for sy in range(ss):
                base = ((y * ss + sy) * S + x * ss) * 4
                for sx in range(ss):
                    j = base + sx * 4
                    r_ += px[j]
                    g_ += px[j + 1]
                    b_ += px[j + 2]
                    a_ += px[j + 3]
            o = (y * size + x) * 4
            out[o] = r_ // n
            out[o + 1] = g_ // n
            out[o + 2] = b_ // n
            out[o + 3] = a_ // n
    return out


def _png(size: int, rgba) -> bytes:   # rgba: bytearray
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    stride = size * 4
    raw = bytearray()
    for y in range(size):
        raw.append(0)                      # 每行的 filter 字节
        raw += rgba[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR",
                    struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def _ico(images) -> bytes:
    """打包多尺寸 ICO。

    entries 里的 dwBytesInRes / dwImageOffset 都要按**文件偏移**算，
    不是相对段内偏移——写错了图标仍然能加载但尺寸全错。
    """
    n = len(images)

    def bmp_bytes(size, rgba):
        # 40 字节 BITMAPINFOHEADER（双倍高 = 含 AND 掩码）
        hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                          size * size * 4, 0, 0, 0, 0)
        body = bytearray()
        stride = size * 4
        for y in range(size - 1, -1, -1):        # BMP 自下而上
            row = rgba[y * stride:(y + 1) * stride]
            for x in range(size):
                o = x * 4
                body += bytes((row[o + 2], row[o + 1], row[o], row[o + 3]))
        # AND 掩码：BGRA32 时全 0 即可（alpha 通道已经给了透明度）
        rowbytes = ((size + 31) // 32) * 4
        return hdr + bytes(body) + b"\x00" * (rowbytes * size)

    blobs = [bmp_bytes(sz, rgba) for sz, rgba in images]
    off = 6 + 16 * n
    entries = b""
    for (sz, _), blob in zip(images, blobs):
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32,
                               len(blob), off)
        off += len(blob)
    return struct.pack("<HHH", 0, 1, n) + entries + b"".join(blobs)


def main() -> None:
    made = []
    for sz in SIZES:
        rgba = _render(sz, simple=(sz <= 32))
        path = os.path.join(OUT_DIR, f"icon_{sz}.png")
        with open(path, "wb") as f:
            f.write(_png(sz, rgba))
        made.append((os.path.basename(path), os.path.getsize(path)))

    # ICO 里放常用的 6 档（16/24/32/48/64/256）
    ico_sizes = [s for s in SIZES if s in (16, 24, 32, 48, 64, 256)]
    ic = _ico([(s, _render(s, simple=(s <= 32))) for s in ico_sizes])
    ico_path = os.path.join(OUT_DIR, "icon.ico")
    with open(ico_path, "wb") as f:
        f.write(ic)

    for name, size in made:
        print(f"  {name:<16} {size:>8,} bytes")
    print(f"  {'icon.ico':<16} {len(ic):>8,} bytes  "
          f"({len(ico_sizes)} sizes)")


if __name__ == "__main__":
    main()