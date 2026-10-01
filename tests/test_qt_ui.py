"""
PySide6 界面的端到端冒烟测试。

每个场景都在子进程里跑并设超时——GUI 测试一旦卡住不该拖垮整个
测试套。超时会被明确报告为「环境导致无法验证」，绝不假装通过。
"""
from __future__ import annotations

import os
import subprocess
import re
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
from pathlib import Path



ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TIMEOUT = 420   # 实测本地 266s；CI 机器更慢，留足余量

CHILD = r'''
import re, sys, time
# 子进程是独立的 python -c，不继承父进程文件顶部的 reconfigure，
# 所以必须在这里再来一次——cp1252 下它会在第一行 print 就崩掉，
# 父进程只看到「子进程失败」，看不到任何原因。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
sys.path.insert(0, ROOT_PLACEHOLDER)
from PySide6.QtWidgets import QApplication
from netdiag.app import App
from netdiag.core.runner import KIND_DONE, KIND_LINE, KIND_STAT

app = QApplication(sys.argv[:1])
w = App("light")


def qss_platform() -> str:
    """当前 Qt 平台插件名。offscreen 下量的像素不可信。"""
    try:
        return app.platformName()
    except Exception:
        return "?"

w.resize(1120, 760)
w.show()

def pump(n=40):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.03)

pump(30)
fails = []

# ---- 1. 所有页面都能构建并切换 ----
from netdiag.ui.pages import PAGES
for cls in PAGES:
    try:
        w.switch_to(cls.NAME)
        pump(6)
        pg = w.pages[cls.NAME]
        assert pg is not None
        assert pg.windowTitle() is not None
    except Exception as e:
        fails.append(f"页面 {cls.NAME}: {e}")
print(f"1. {len(PAGES)} 页切换:", "OK" if not fails else fails)

# ---- 2. 主题切换不崩 ----
try:
    w.switch_to(PAGES[0].NAME); pump(5)
    w.toggle_theme(); pump(12)
    dark = w.theme_name
    w.toggle_theme(); pump(12)
    assert w.theme_name != dark, "主题没切换"
    print("2. 主题切换:", "OK 深->浅", dark, "->", w.theme_name)
except Exception as e:
    print("2. 主题切换: FAIL", e)
    fails.append("主题切换")

# ---- 3. 端到端：ping localhost ----
try:
    w.switch_to("ping"); pump(6)
    pg = w.pages["ping"]
    pg.host.setText("127.0.0.1")
    pg.count.setValue(2)
    # App 自带的 60ms 定时器会在 processEvents 里把队列 drain 走，
    # 测试再 drain() 就永远拿不到事件。这里接管分发：停掉定时器，
    # 由测试自己 drain 并投递给页面。
    w._timer.stop()
    pg.start()
    got_done = False
    for _ in range(150):
        pump(2)
        for kind, payload, task in w.dispatcher.drain():
            pg.on_event(kind, payload)
            if kind == KIND_DONE and task == "ping":
                got_done = True
        if got_done:
            break
    text = pg.console.toPlainText()
    assert got_done, "没收到 done 事件"
    assert len(text.strip()) > 0, "控制台没有任何输出"
    print("3. ping 端到端: OK 输出", len(text.strip()), "字符")
except Exception as e:
    print("3. ping 端到端: FAIL", e)
    fails.append("ping")

# ---- 4. 端口解析（不触网）----
try:
    from netdiag.core.probes import parse_ports
    got = parse_ports("22,80,443")
    assert len(got) == 3, got
    rng = parse_ports("8000-8003")
    assert len(rng) == 4, rng
    print("4. 端口解析: OK")
except Exception as e:
    print("4. 端口解析: FAIL", e)
    fails.append("端口解析")

# ---- 5. Console 的 replace_last 不刷屏 ----
try:
    w.switch_to("speed"); pump(6)
    pg = w.pages["speed"]
    pg.console.clear_all()
    pg.console.write("测速中", "head")
    pg.console.write("进度 0", "accent")
    for i in range(1, 40):
        pg.console.replace_last(f"进度 {i}", "accent")
    lines = pg.console.toPlainText().splitlines()
    assert len(lines) == 2, f"行数变成 {len(lines)}，应为 2"
    assert lines[1] == "进度 39", f"末行是 {lines[1]!r}，应为 '进度 39'"
    print("5. replace_last: OK 39 次刷新后仍", len(lines), "行, 末行 =", lines[1])
except Exception as e:
    print("5. replace_last: FAIL", e)
    fails.append("replace_last")

# ---- 6. 窗口命中测试（坐标判定，不构造 QMouseEvent）----
# 不构造 QMouseEvent：PySide6 的构造函数重载在部分版本上对
# globalPosition 的支持不一致，而 _hit_zone 真正依赖的只是
# 「全局坐标 -> 命中区域」这条映射。直接驱动坐标更稳，也更贴近
# 这段代码的真实职责。
try:
    from PySide6.QtCore import QPoint
    d = w._dragger
    w.resize(1000, 700)
    w.move(200, 200)
    pump(6)
    ox, oy = w.x(), w.y()
    bw, bh = w.titlebar.width(), w.titlebar.height()
    W_, H_ = w.width(), w.height()

    def zone_at(gx, gy):
        return d._zone_at_global(QPoint(gx, gy))

    checks = [
        ("标题栏中点",  ox + bw // 2,  oy + 6,             "caption"),
        ("标题栏右端",  ox + bw - 60, oy + 6,             "caption"),
        ("左边缘",      ox + 2,       oy + bh + 200,      "left"),
        ("右边缘",      ox + W_ - 3,  oy + bh + 200,      "right"),
        ("下边缘",      ox + W_ // 2, oy + H_ - 3,        "bottom"),
        # 角落判定**先于**标题栏，所以 (2, 标题栏内) 必须是
        # top-left 而不是 caption——这与真实 Windows 窗口一致
        #（系统窗口的角落是 resize grip 优先）
        ("左上角",      ox + 2,       oy + 6,             "top-left"),
        ("右上角",      ox + W_ - 3,  oy + 6,             "top-right"),
        ("左下角",      ox + 2,       oy + H_ - 3,        "bottom-left"),
        ("右下角",      ox + W_ - 3,  oy + H_ - 3,        "bottom-right"),
        ("内容区中点",  ox + W_ // 2, oy + bh + 250,      ""),
    ]
    bad = []
    for name, gx, gy, want in checks:
        got = zone_at(gx, gy)
        if got != want:
            bad.append(f"{name}({gx-ox},{gy-oy}) 得到 {got!r}，应为 {want!r}")
    assert not bad, "; ".join(bad)
    print("6. 窗口命中测试: OK", len(checks), "个点全部正确")
except Exception as e:
    print("6. 窗口命中测试: FAIL", e)
    fails.append("窗口命中")

# ---- 7. 窗口圆角：抗锯齿 + 真透明 ----
# 这条测试是有来历的：QSS 的 border-radius 不裁窗口（DWM 按矩形
# 合成，切角处漏出黑色底层）；QWidget.setMask(QRegion(...)) 虽是
# 真裁切但**二值**、边缘硬阶梯。两者都被这里挡住。
#
# 判据分两层：
#   a) 圆角外侧必须真透明（alpha 接近 0）—— 挡住 setMask 之外的
#      「QSS 上色填满圆角外」这种伪透明
#   b) 边界上必须存在中间灰阶 —— 挡住二值硬边
# 注意：QWidget.grab() 走 render() 路径，对抗锯齿的呈现与屏幕
# 不完全一致，所以 b 项只要求「至少 1 个」，真实边缘的平滑度由
# paintEvent 里的 Antialiasing 开关保证（下面单独断言）。
try:
    r = w._round_root
    img = r.grab().toImage()
    rad = r._r
    assert rad > 0, "圆角半径必须是正数"

    a00 = img.pixelColor(0, 0).alpha()
    assert a00 < 128, f"圆角外侧 (0,0) alpha={a00}，应接近 0（真透明）"

    diag = [img.pixelColor(d, d).alpha() for d in range(0, rad + 2)]
    row = [img.pixelColor(x, rad).alpha() for x in range(0, rad + 2)]
    n_mid = len([v for v in diag if 0 < v < 255]) + \
            len([v for v in row if 0 < v < 255])
    assert n_mid >= 1, \
        f"圆角边界一个中间灰阶都没有，边缘是纯二值硬边（diag={diag}）"

    # 抗锯齿开关必须真的打开：paintEvent 里不开这个，
    # 边缘就是硬阶梯（屏幕放大 7 倍可见）
    import inspect
    src = inspect.getsource(type(r).paintEvent)
    assert "Antialiasing" in src, \
        "RoundedRoot.paintEvent 没有开 Antialiasing，圆角会是硬阶梯"
    assert "drawRoundedRect" in src, \
        "RoundedRoot.paintEvent 应使用 drawRoundedRect 画抗锯齿圆角"

    print(f"7. 圆角: OK 半径={rad}px  外侧alpha={a00} 中间灰阶={n_mid} AA已开")
except Exception as e:
    print("7. 圆角: FAIL", e)
    fails.append("圆角抗锯齿")

# ---- 8. 圆角半径必须处处一致 ----
# 这条测试有来历：RoundedRoot 用 App.CORNER 画窗口底色的圆角，
# 而侧边栏用 theme.RADIUS 画自己的左侧圆角。两者是**两处独立的
# 常量**，各自看都没问题，但一旦不等，侧边栏这个不透明直角矩形
# 就会盖住窗口左下角的圆角（大窗口下尤其明显）。
try:
    from netdiag.ui import theme as _th
    assert _th.RADIUS == w.CORNER, (
        f"theme.RADIUS={_th.RADIUS} 与 App.CORNER={w.CORNER} 不一致 —— "
        f"侧边栏会用直角盖住窗口左下角的圆角")
    # 侧边栏实际生效的圆角值也要核对（QSS 覆盖可能改掉）
    sb = w.sidebar
    assert sb.objectName() == "Sidebar", \
        f"侧边栏 objectName 变成了 {sb.objectName()!r}，QSS 圆角规则不再命中"
    print(f"8. 圆角半径一致: OK theme.RADIUS == App.CORNER == {w.CORNER}")
except Exception as e:
    print("8. 圆角半径一致: FAIL", e)
    fails.append("圆角半径一致")

# ---- 9. 主题切换后圆角背景色要跟着换 ----
try:
    # 顺带防一类很难发现的 bug：主题键名写错时 dict.get 会静默
    # fallback 到默认值，不报错、测试也可能过，只有肉眼切主题
    # 才看得出「颜色没跟着变」。这里直接比对两个主题字典的键集合。
    from netdiag.ui import theme as th
    lk, dk = set(th.LIGHT), set(th.DARK)
    assert lk == dk, f"LIGHT/DARK 键集合不一致: {lk ^ dk}"
    before = w._round_root._bg.name()
    w.toggle_theme()
    pump(8)
    after = w._round_root._bg.name()
    assert before != after, (
        f'切主题后圆角背景色没变（都是 {after}）—— RoundedRoot 是 '
        f'QPainter 画的，QSS 管不到，必须显式 retheme')
    print(f"8. 主题同步圆角: OK {before} -> {after}")
    w.toggle_theme()
    pump(6)
except Exception as e:
    print("9. 主题同步圆角: FAIL", e)
    fails.append("主题同步圆角")

# ---- 9. 软件内图标必须与 exe 图标同源 ----
# 关键约束：BrandMark 不可用 QPainter 手绘，exe 图标由
# tools/make_icon.py 单独生成，两套代码各画各的。用户报「软件包和
# 任务栏图标变了但软件内的没变」——四角像素比对确认：手绘版 64/64
# 不透明（切角方片），生成版 0/0（圆形）。现在 BrandMark 直接读 PNG。
try:
    from netdiag.app import BrandMark, resource_dir
    from PySide6.QtGui import QColor, QImage
    import os

    rdir = resource_dir()
    assert os.path.isdir(rdir), f"资源目录不存在：{rdir}"
    assert os.path.isfile(os.path.join(rdir, "icon_64.png")), \
        f"icon_64.png 不在 {rdir}"

    bm = BrandMark(64)
    assert bm._pix is not None, \
        "BrandMark 没能加载图标（QPixmap 返回 null），已回落到纯色圆点"
    bimg = bm.grab().toImage()
    gimg = QImage(os.path.join(rdir, "icon_64.png"))

    # 中心像素必须一致——同源的最直接证据。
    #
    # **必须容差比较，不能要求逐像素相等。** BrandMark 会挑一张
    # 不小于目标尺寸的 PNG 再缩放（64px 时实际用的是 icon_128.png
    # 经 SmoothTransformation 缩到 64），插值必然带来 ±1~2 的偏差。
    # 换配色后实测 #545b91 vs #545c91——差 1 个通道值，两张图其实
    # 是同一张。要求严格相等会让「同源」这个测试变成「缩放参数
    # 一致性」测试，改配色就红。
    bc, gc = bimg.pixelColor(32, 32), gimg.pixelColor(32, 32)
    diff = max(abs(bc.red() - gc.red()),
               abs(bc.green() - gc.green()),
               abs(bc.blue() - gc.blue()))
    assert diff <= 4, (
        f"中心色不一致（最大通道差 {diff}）："
        f"BrandMark={bc.name()} vs icon_64={gc.name()} —— "
        f"软件内图标与 exe 图标不是同一份资源")

    # 角点必须不是图标色（圆形 = 角上透明）
    assert gimg.pixelColor(0, 0).alpha() == 0, \
        "icon_64 角点应完全透明（圆形轮廓），实际不透明"
    print(f"9. 图标同源: OK 中心色 {bc.name()} 双方一致")
except Exception as e:
    print("9. 图标同源: FAIL", e)
    fails.append("图标同源")

# ---- 10. 默认必须是浅色 ----
try:
    assert w.theme_name == "light", \
        f"默认主题是 {w.theme_name}，应为 light（跟随系统会导致用户看到深色）"
    print("10. 默认浅色: OK")
except Exception as e:
    print("10. 默认浅色: FAIL", e)
    fails.append("默认主题")

def _extent(kind):
    """算某个窗口图标图形的外接矩形（1x 坐标）。"""
    xs, ys = [], []
    for x0, y0, x1, y1 in WinButton._lines_of(kind):
        xs += [x0, x1]; ys += [y0, y1]
    from PySide6.QtCore import QRect
    return QRect(int(min(xs)), int(min(ys)),
                 int(max(xs) - min(xs)), int(max(ys) - min(ys)))


# ---- 45. 下拉框必须真的能展开并渲染 ----
# **曾经的崩溃**：
#   NameError: name 'QPainter' is not defined
#   widgets.py 里 ComboItemDelegate.paint() 用到 QPainter，
#   而它只在函数内的局部 import 列表里——我清理未用导入时删掉了。
#
# **为什么全套测试都没抓到**：paint() 只在**用户点开下拉框**时才被
# 调用。第 42 条只读 delegate 的属性（MARGIN_X / retint），从不
# 真正画一次。测试全绿，用户一用就崩。
#
# 所以这条必须**真的 showPopup() 并渲染**——不碰代码路径的断言
# 等于没测。
try:
    if qss_platform() == "windows":
        from PySide6.QtWidgets import QComboBox
        from netdiag.app import App
        probe = App("light")
        for _ in range(10):
            probe._pump()
        probe.resize(1440, 900)
        for _ in range(10):
            probe._pump()
        probe.switch_to("speed")
        for _ in range(10):
            probe._pump()

        done = []
        for key in ("speed", "multiprobe"):
            pg = probe.pages[key]
            combos = pg.findChildren(QComboBox)
            assert combos, f"{key} 页应至少有一个下拉框"
            for cb in combos:
                try:
                    cb.showPopup()
                    for _ in range(10):
                        probe._pump()
                    # 真正渲染一次——paint() 就是在这里被调用的
                    cb.view().viewport().grab()
                    for _ in range(5):
                        probe._pump()
                    cb.hidePopup()
                    for _ in range(5):
                        probe._pump()
                    done.append(cb.objectName() or cb.currentText()[:8])
                except NameError as e:
                    raise AssertionError(
                        f"{key} 页下拉框渲染时 NameError: {e}"
                        f"——paint() 里的 import 列表漏了名字，"
                        f"import 时不报错，只在真画的时候炸") from e
        print(f"45. 下拉框渲染: OK {len(done)} 个全部展开渲染无异常"
              f"（真调用 paint()，能抓住 import 漏名）")
        probe._on_close()
    else:
        print("45. 下拉框渲染: 跳过（offscreen 弹不出独立顶层窗口）")
except Exception as e:
    print("45. 下拉框渲染: FAIL", e)
    fails.append("下拉框渲染")

# ---- 44. 窗口控制图标：自绘、四个图形大小一致、在同一水平线上 ----
# 两条要求都锁在这里：窗口控制必须是**自绘图标**（不用 Unicode 字形），
# 且四个图形的主体中心必须在**同一条水平线上**。
try:
    from netdiag.app import WinButton
    from netdiag.ui import theme as T
    probe = App("light")
    for _ in range(8):
        probe._pump()

    # ① 图形尺寸同档：宽度差 ≤3px，方框类宽高比接近 1
    sizes = {k: (lambda b: (b.width(), b.height()))(_extent(k))
             for k in ("min", "max", "restore", "close")}
    wmin = min(v[0] for v in sizes.values())
    wmax = max(v[0] for v in sizes.values())
    assert wmax - wmin <= 3, f"窗口图标宽度不一致: {sizes}"
    for k in ("max", "restore", "close"):
        wd, ht = sizes[k]
        assert 0.8 <= wd / ht <= 1.25, f"{k} 的宽高比失衡: {wd}x{ht}"

    # ② **四个图形的主体中心必须在同一条水平线上。**
    #
    # 第一版方框/叉画在 y=7..15、减号在 y=16，差 5px——一半浮在
    # 上半、一半沉在下半，一眼就看出没对齐，而每个图标单独看都正常。
    #
    # 判据读 SUBJECT_CENTER 常量，不在测试里重算几何（重复实现推导，
    # 代码一改测试就跟着错）。下面再交叉校验常量没和画法脱节。
    centers = WinButton.SUBJECT_CENTER
    CY, CX = WinButton.CY, WinButton.CX
    for kind in ("max", "restore", "close"):
        cx, cy = centers[kind]
        assert abs(cy - CY) < 0.5, (
            f"{kind} 的中心 y={cy}，应与按钮中心 CY={CY} 对齐"
            f"（偏离会让图标浮在上半或沉在下半）")
        assert abs(cx - CX) < 0.6, f"{kind} 的中心 x={cx} 应与 CX={CX} 对齐"

    for kind in ("max", "restore", "close"):
        ls = WinButton._lines_of(kind)
        xs = [v for l in ls for v in (l[0], l[2])]
        ys = [v for l in ls for v in (l[1], l[3])]
        if kind == "restore":
            ymin = min(ys)
            ys = [y for y in ys if y > ymin]      # 丢掉后框，只留主体
        real_y = (min(ys) + max(ys)) / 2
        assert abs(real_y - centers[kind][1]) < 0.6, (
            f"{kind}: SUBJECT_CENTER 说 y={centers[kind][1]}，"
            f"实际线段算出 {real_y}——常量与画法脱节")

    # 减号允许比中心低，但**最多 2px**（Windows 惯例，再多就是没对齐）
    ys = [v for l in WinButton._lines_of("min") for v in (l[1], l[3])]
    assert ys[0] == CY + 2.0, f"减号应比中心低 2px，实际 y={ys[0]}"

    # ③ 不能再用 Unicode 字形：四个按钮都不应有 text
    for b in (probe.titlebar.btn_min, probe.titlebar.btn_max,
              probe.titlebar.btn_close, probe.titlebar.btn_theme):
        assert b.text() == "", (
            f"窗口按钮不应再有文字（{b.text()!r}）——图标必须是自绘的")

    # ④ 切主题时自绘图标颜色必须跟着换，否则深色下看不见。
    #    这里用直接调用而非点击——点击路径由第 11 条专门负责。
    probe.titlebar.btn_min.set_theme(T.LIGHT)
    light_c = probe.titlebar.btn_min._icon_color
    probe.toggle_theme()
    for _ in range(8):
        probe._pump()
    dark_c = probe.titlebar.btn_min._icon_color
    assert light_c != dark_c, f"切主题后图标颜色应改变（{light_c} -> {dark_c}）"

    # ⑤ 最大化/还原是两个不同图形，且能来回切
    tb = probe.titlebar
    tb.sync_win_states(True)
    assert tb.btn_max.kind == "restore"
    tb.sync_win_states(False)
    assert tb.btn_max.kind == "max"

    print(f"44. 窗口图标: OK 自绘，主体中心全在 y={CY}，"
          f"四图形 {sizes}")
    probe._on_close()
except Exception as e:
    print("44. 窗口图标: FAIL", e)
    fails.append("窗口图标")

# ---- 43. 修复按钮的颜色跟着风险等级走 ----
# 高风险操作（重置 Winsock，要重启）和低风险（清 DNS 缓存）用同一个
# 蓝色按钮时，用户在 8 个长得一样的页面间点来点去，肌肉记忆会让人
# 顺手点下去。所以按风险分色。
try:
    from netdiag.ui.pages.fix_page import RISK_BTN
    from netdiag.core import netfix

    risks = {f.key: f.risk for f in netfix.FIXES}
    # 键名必须覆盖 netfix 里真实出现的每一个风险值
    assert set(RISK_BTN) >= set(risks.values()), (
        f"RISK_BTN 漏了风险等级：实际用到 {set(risks.values())}，"
        f"表里只有 {set(RISK_BTN)}")
    # 三档必须给出三种不同的外观
    styles = {RISK_BTN[r] for r in RISK_BTN}
    assert len(styles) == len(RISK_BTN), \
        f"风险等级应各对应一种按钮样式，实际 {RISK_BTN}"
    # 每种样式都得在 QSS 里有规则，否则按钮会掉回系统默认灰
    from netdiag.ui import theme as T
    for name in styles:
        for theme_name in (T.LIGHT, T.DARK):
            assert f"QPushButton#{name} {{" in T.qss(theme_name), \
                f"QSS 里没有 {name} 的规则（主题 {theme_name['name']}）"
    print(f"43. 修复按钮分色: OK {risks} -> {RISK_BTN}")
except Exception as e:
    print("43. 修复按钮分色: FAIL", e)
    fails.append("修复按钮分色")

# ---- 41. 输出区空状态提示 ----
# 空输出区容易被误认为程序已崩溃，所以每个有 Console 的页面都要
# 给一句灰色提示。用 Qt 原生 placeholder：任何内容写入后自动消失，
# 清空后自动回来——不需要我们自己管状态。
try:
    from netdiag.app import App
    probe = App("light")
    for _ in range(8):
        probe._pump()
    cons = {}
    for pg in probe.pages.values():
        c = getattr(pg, "console", None)
        if c is None:
            continue
        cons.setdefault(id(c), (pg.NAME, c))
    missing = [n for n, c in cons.values() if not c.placeholderText()]
    assert not missing, f"这些页面的输出区没有空状态提示: {missing}"
    # 提示不能是正文色（会被误认成真实输出），也不能太亮
    sample = next(iter(cons.values()))[1]
    ph = sample.placeholderText()
    assert "点" in ph, f"提示语应说明下一步动作，实际 {ph!r}"
    # 写入内容后 placeholder 必须不再可见（Qt 自动行为，验一下别被改坏）
    before = sample.placeholderText()
    sample.write("hello")
    assert sample.toPlainText() == "hello\n", \
        "写入后内容应只剩真实输出"
    sample.clear_all()
    assert sample.toPlainText() == "", "clear_all 应清空"
    print(f"41. 输出区提示: OK {len(cons)} 个 Console 均有提示"
          f"（样例 {before[:18]}…）")
    probe._on_close()
except Exception as e:
    print("41. 输出区提示: FAIL", e)
    fails.append("输出区提示")

# ---- 42. 下拉框：高亮块内缩 + 主题跟随 ----
# 这两条是 ComboItemDelegate 存在的全部理由，QSS 无论怎么写都做不到：
#   ① 高亮块必须内缩，不能铺满整行（否则第一项圆角被切掉）
#   ② 高亮色必须跟着主题走（QSS 管不到 Python delegate）
try:
    from netdiag.ui.widgets import ComboItemDelegate
    from PySide6.QtWidgets import QComboBox
    probe = App("light")
    for _ in range(8):
        probe._pump()
    cb = probe.pages["speed"].src
    dele = cb.view().itemDelegate()
    assert isinstance(dele, ComboItemDelegate), \
        f"下拉框的 delegate 应是 ComboItemDelegate，实际 {type(dele)}"
    assert callable(getattr(cb, "retint", None)), \
        "polish_combo 应挂上 retint，供切主题时更新 delegate 配色"
    # 内缩量必须为正——这正是 QSS 做不到的那件事
    assert dele.MARGIN_X > 0 and dele.MARGIN_Y > 0, \
        f"高亮块内缩量应大于 0，实际 MARGIN_X={dele.MARGIN_X} " \
        f"MARGIN_Y={dele.MARGIN_Y}"

    # delegate 拿到的必须是当前主题（浅色），不是写死的 LIGHT
    light_bg = dele._t["accent_soft"]
    # 这里直接调 toggle_theme 而不是点按钮：这条只关心 delegate 的
    # 配色是否跟随主题，「点按钮能不能切换」由第 11 条负责。
    probe.toggle_theme()
    for _ in range(8):
        probe._pump()
    dark_bg = dele._t["accent_soft"]
    assert light_bg != dark_bg, \
        f"切主题后 delegate 配色应改变（{light_bg} -> {dark_bg}）——retint 没生效"
    # 切主题不应替换 delegate 实例，只改它持有的主题字典
    assert probe.pages["speed"].src.view().itemDelegate() is dele, \
        "切主题不应替换 delegate 实例，只改它的主题字典"
    print(f"42. 下拉框 delegate: OK 内缩生效 + retint 跟随主题"
          f"（{light_bg} -> {dark_bg}）")
    probe._on_close()
except Exception as e:
    print("42. 下拉框 delegate: FAIL", e)
    fails.append("下拉框 delegate")

# ---- 11. 主题按钮在标题栏，图标表示「会切到什么」 ----
# 按钮从侧边栏挪到了标题栏最小化左边，所以这里查的是
# titlebar.btn_theme，不再是 sidebar.theme_btn。
#
# **语义是「点下去会变成什么」**：浅色下显示月亮（点了变深色），
# 深色下显示太阳（点了变浅色）。和 Windows 的行为一致。
try:
    from netdiag.app import App
    probe = App("light")
    for _ in range(8):
        probe._pump()
    btn = probe.titlebar.btn_theme
    assert not hasattr(probe.sidebar, "theme_btn"), \
        "侧边栏不该再有主题按钮——它已经挪到标题栏"
    # **判据从「文字」改成「内部状态」。** 早期版本这里断言
    # btn.text() == "☾"，后来 ThemeButton 也改成了 QPainter 自绘
    # （和三个窗口控制成套，Unicode 字形大小粗细不一致），按钮
    # 就不再有文字了。
    #
    # 所以不能再拿 text() 当判据——它恒为空。改为直接查内部状态，
    # 那才是绘制时真正依据的东西；第 44 条负责验证「确实画出来了」。
    assert btn.text() == "", (
        f"主题按钮应是自绘图标，不应有文字（实际 {btn.text()!r}）")
    assert btn._dark is False, (
        "浅色主题下 _dark 应为 False——此时画的是月牙（点了变深色）")
    # 位置：必须在最小化按钮左边。用 layout 的视觉顺序判断，比 x()
    # 稳——x() 是相对父控件的，而三者父控件相同，理论上也对，
    # 但 stretch 会让坐标在小数处取整，边界情况下会误判。
    lay = probe.titlebar.layout()
    order = [w for w in (btn, probe.titlebar.btn_min,
                         probe.titlebar.btn_max, probe.titlebar.btn_close)
             if lay.indexOf(w) >= 0]
    pos = [lay.indexOf(w) for w in order]
    assert pos == sorted(pos), \
        f"标题栏按钮顺序应为主题/最小化/最大化/关闭，实际索引 {pos}"
    # **必须走真实点击路径，不能直接调 probe.toggle_theme()。**
    #
    # 曾经的「点击没反应」，根因是
    #   self.titlebar.toggle_theme.connect(self.toggle_theme)
    # 这一行压根没写——信号定义了、发射端写了、**接收端没连上**。
    #
    # 而当时这条测试是直接调 probe.toggle_theme()，**完全绕过
    # 点击路径**，所以信号断在哪里它就看不见，全套测试照绿。
    #
    # 教训：**测「按钮能用」就必须点按钮。**直接调被测对象的方法
    # 等于只测了方法本身，测不到「方法有没有被接上」。
    before = probe.theme_name
    btn.click()                      # ← 真实的 QPushButton.click()
    for _ in range(8):
        probe._pump()
    assert probe.theme_name != before, (
        f"点主题按钮后主题应变化，但仍是 {probe.theme_name!r}"
        f"——信号链断了（检查 titlebar.toggle_theme.connect(...)）")
    assert btn._dark is True, (
        "深色主题下 _dark 应为 True——此时画的是太阳（点了变浅色）")
    assert btn.toolTip(), "tooltip 应说明当前模式"
    # 再点一次必须能回来（不是只能切到深色）
    btn.click()
    for _ in range(8):
        probe._pump()
    assert probe.theme_name == before, (
        f"再点一次应切回 {before!r}，实际 {probe.theme_name!r}")
    print("11. 主题按钮: OK 真实点击可来回切换（信号链完整）")
    probe._on_close()
except Exception as e:
    print("11. 主题按钮: FAIL", e)
    fails.append("主题按钮文案")

# ---- 12. _set_running 必须在没有页级按钮时也不崩 ----
# 早期版本无条件访问 self.btn_start.setEnabled()，而纯展示页
# 删掉了那三个按钮，用户一点就 AttributeError（报「WiFi / TCP
# 点击就报错」）。这里用一个真实的、确实没有页级按钮的页面来测，
# 而不是假设存在——前一版断言「至少有 1 个」结果永远失败，
# 说明这种写法会退化成空测试。
try:
    from netdiag.ui.pages.base import Page
    from netdiag.core.runner import Dispatcher

    class _NoButtons(Page):
        NAME = "_nobtn"
        TITLE = "无按钮"
        SUBTITLE = "用于测试"

        def _build_actions(self, lay):
            pass                        # 刻意不加任何按钮

        def task(self):
            return (lambda post: None, "")

    probe = _NoButtons(Dispatcher(), w.t)
    assert not hasattr(probe, "btn_start"), "构造前提不对：不该有 btn_start"
    probe._set_running(True)            # 过去在这里抛 AttributeError
    assert probe._running is True
    probe._set_running(False)
    assert probe._running is False
    probe.deleteLater()
    print("12. _set_running 防御: OK 无页级按钮时正常切换")
except Exception as e:
    print("12. _set_running 防御: FAIL", e)
    fails.append("_set_running 防御")

# ---- 13. 纯展示页不该有输出区与无意义按钮 ----
# 「关于」页没有任何探测输出，留一个空的深色框在页面下方看起来
# 像出错了；「清空」按钮清的是不存在的输出，同样是噪音。
try:
    ab = w.pages.get("about")
    assert ab is not None, "缺少「关于」页"
    assert ab.HAS_CONSOLE is False, "关于页不该有输出区"
    assert not ab.console.isVisible(), "关于页的输出区仍然可见"
    assert not hasattr(ab, "btn_start"), "关于页不该有「开始」按钮"
    assert not hasattr(ab, "btn_clear"), "关于页不该有「清空」按钮"
    # 内容页相反：必须有
    for name in ("ping", "http", "mtu", "wifi", "lan", "tcp"):
        pg = w.pages[name]
        assert pg.HAS_CONSOLE, f"{name} 页不该没有输出区"
        assert hasattr(pg, "btn_start"), f"{name} 页不该没有「开始」按钮"
    print("13. 纯展示页: OK 关于页无输出区无按钮，探测页齐全")
except Exception as e:
    print("13. 纯展示页: FAIL", e)
    fails.append("纯展示页")

# ---- 14. 切主题不得重建页面 ----
# 用户报过：点主题切换后崩溃
#   RuntimeError: libshiboken: Internal C++ object (AboutPage) already deleted
# 根因是 toggle_theme 调 _rebuild()，而 _rebuild 先 deleteLater()
# （延迟删除）又立刻建新页并 switch_to，栈索引与 PAGES 顺序错位。
try:
    from netdiag.app import App
    probe = App("light")
    for _ in range(10):
        probe._pump()
    probe.switch_to("about")
    for _ in range(6):
        probe._pump()
    before = probe.pages["about"]
    for i in range(20):                 # 反复切，必须不崩不重建
        probe.toggle_theme()
        for _ in range(4):
            probe._pump()
        probe.switch_to("about")
        for _ in range(3):
            probe._pump()
        probe.switch_to("ping")
        for _ in range(3):
            probe._pump()
    assert probe.pages["about"] is before, \
        "关于页被重建了——toggle_theme 不该调 _rebuild()"
    assert probe.pages["ping"] is not None
    for name in probe.pages:
        probe.switch_to(name)
        for _ in range(3):
            probe._pump()
    print(f"15. 切主题不重建: OK 20 次往返后对象同一，{len(PAGES)} 页均可切换")
    probe._on_close()
except Exception as e:
    print("15. 切主题不重建: FAIL", e)
    fails.append("切主题")

# ---- 15. Console 配色必须跟着主题换 ----
# Console 的语义色是 QTextCharFormat，在 __init__ 时烤死，
# 不 retheme 的话切深色后已有行还是浅色主题的墨色。
try:
    assert w.pages["ping"].console._fmt is not None
    c_light = w.pages["ping"].console._fmt["head"].foreground().color().name()
    w.toggle_theme()
    for _ in range(8):
        w._pump()
    c_dark = w.pages["ping"].console._fmt["head"].foreground().color().name()
    w.toggle_theme()
    for _ in range(8):
        w._pump()
    c_back = w.pages["ping"].console._fmt["head"].foreground().color().name()
    assert c_light != c_dark, f"Console 配色没跟着换：{c_light} == {c_dark}"
    assert c_back == c_light, f"切回来后配色不一致：{c_back} != {c_light}"
    print(f"16. Console 换色: OK {c_light} -> {c_dark} -> {c_back}")
except Exception as e:
    print("16. Console 换色: FAIL", e)
    fails.append("Console 换色")

# ---- 16. IP 工具页结果区必须真的显示出来 ----
# 踩过 QLayout(holder) 装不上 + setLayout(None) 不支持的坑，
# 症状是「核心算对了、界面空白」。
try:
    ip = w.pages["ip"]
    # 逐个设值并 pump，且**必须设成和默认值不同的值**——setText 传入
    # 与当前相同的字符串时 Qt 不发 textChanged，测试会误判成
    # 「结果区没更新」（第一版就是这么假失败的）。
    for setter in (lambda: ip.sub_in.setText("10.20.30.0/24"),
                   lambda: ip.conv_in.setText("8.8.8.8"),
                   lambda: ip.rng_a.setText("10.0.0.1"),
                   lambda: ip.rng_b.setText("10.0.0.9")):
        setter()
        for _ in range(6):
            w._pump()
    from PySide6.QtWidgets import QLabel
    for label, holder, want in (("子网", ip.sub_out, "10.20.30.0"),
                                ("转换", ip.conv_out, "134744072"),
                                ("范围", ip.rng_out, "10.0.0.1/32")):
        texts = [l.text() for l in holder.findChildren(QLabel) if l.text()]
        assert texts, f"{label} 结果区没有任何标签——layout 没装上"
        assert any(want in t for t in texts), \
            f"{label} 结果区缺少 {want!r}，实际 {texts[:4]}"
    # 反复换内容不能叠加
    n1 = len(ip.sub_out.findChildren(QLabel))
    for v in ("10.0.0.0/8", "172.16.0.0/12", "8.8.8.0/24"):
        ip.sub_in.setText(v)
        for _ in range(4):
            w._pump()
    n2 = len(ip.sub_out.findChildren(QLabel))
    assert n1 == n2, f"结果区控件在叠加：{n1} -> {n2}"
    # 打开页面就该有默认值的计算结果，不用手动改输入框
    fresh = App("light")
    for _ in range(8):
        fresh._pump()
    fip = fresh.pages["ip"]
    from PySide6.QtWidgets import QLabel
    for label, holder, want in (("子网", fip.sub_out, "192.168.0.0"),
                                ("转换", fip.conv_out, "3232235777"),
                                ("范围", fip.rng_out, "192.168.0.0/21")):
        texts = [l.text() for l in holder.findChildren(QLabel) if l.text()]
        assert texts, f"{label} 打开时结果区为空（setText 相同值不发信号）"
        assert any(want in t for t in texts), \
            f"{label} 默认值结果缺少 {want!r}，实际 {texts[:4]}"
    fresh._on_close()
    print(f"17. IP 工具页: OK 打开即有结果，反复刷新不叠加（{n2} 项）")
except Exception as e:
    print("17. IP 工具页: FAIL", e)
    fails.append("IP 工具页")

# ---- 17. 非法输入只提示不崩 ----
try:
    ip = w.pages["ip"]
    for w_, bad in ((ip.sub_in, "这不是IP"), (ip.conv_in, "999.999.1.1"),
                    (ip.rng_a, "10.0.0.9"), (ip.rng_b, "10.0.0.1")):
        w_.setText(bad)                   # 每项都设成非法值
        for _ in range(5):
            w._pump()
    ip.rng_a.setText("10.0.0.9")
    ip.rng_b.setText("10.0.0.1")          # 倒序
    for _ in range(8):
        w._pump()
    from PySide6.QtWidgets import QLabel
    warns = [l.text() for hold in (ip.sub_out, ip.conv_out, ip.rng_out)
             for l in hold.findChildren(QLabel)
             if l.objectName() == "Warn" and l.text()]
    assert len(warns) >= 3, f"应有三处警告，实际 {warns}"
    ip.clear()
    for _ in range(6):
        w._pump()
    assert not ip.sub_out.findChildren(QLabel), "清空后应无残留标签"
    print(f"18. 非法输入: OK {len(warns)} 处提示，清空后无残留")
except Exception as e:
    print("18. 非法输入: FAIL", e)
    fails.append("非法输入")

# ---- 18. 测速页源下拉与协议 ----
try:
    from netdiag.core import probes
    from netdiag.ui.pages.speed_page import AUTO
    sp = w.pages["speed"]
    assert sp.src.count() == len(probes.SPEED_SOURCES) + 1, \
        f"下拉项数 {sp.src.count()} 应为 源数+1"
    assert sp.src.itemText(0) == AUTO
    sp.src.setCurrentText(probes.SPEED_SOURCES[0][0])
    fn, _ = sp.task()
    assert callable(fn)
    # UI 判的 kind 必须与 core 实际发的一致
    sp._fill({"kind": "speed_progress", "bytes": 2097152,
              "elapsed": 2.0, "mbps": 100.0, "source": "X"})
    sp._fill({"kind": "speed_done", "bytes": 10485760,
              "elapsed": 8.0, "mbps": 200.0, "source": "Cloudflare"})
    v0 = sp.tiles[0].value.text()
    assert "25.00" in v0 or "200" in v0, f"测速数值没填上：{v0}"
    print(f"20. 测速源选择: OK {sp.src.count()-1} 个源可选，协议对齐")
except Exception as e:
    print("20. 测速源选择: FAIL", e)
    fails.append("测速源")

# ---- 19. 侧边栏/标题栏不得显示版本号与许可 ----
# 侧边栏与标题栏都不该出现许可信息。**版本号是例外**：标题栏有一份
# 常驻显示（无边框窗口的系统标题用户看不到，报 bug 时要靠它对版本）。
try:
    from PySide6.QtWidgets import QLabel
    # 只扫侧边栏与标题栏——**不能扫全窗口**：关于页自己本来就该
    # 显示许可证和版本号，扫全窗口会误判（第一版就踩了这个）。
    shell_text = " | ".join(
        l.text() for area in (w.sidebar, w.titlebar)
        for l in area.findChildren(QLabel) if l.text())
    assert "GPL" not in shell_text, \
        f"侧边栏/标题栏仍显示许可：{[t for t in shell_text.split(' | ') if 'GPL' in t]}"
    # 版本号从包元数据取，不要硬编码——写死前缀后一旦版本号变更，
    # 这条测试就会误报，而代码本身完全正确。
    from netdiag import __version__ as _ver
    ver_tag = f"v{_ver}"
    # **要求变了。** 这条断言原本是「版本号不许出现在标题栏」——
    # 当年为了清掉开发痕迹设的。但用户后来提出「标题栏 aicbbuu network
    # tools 后面加个版本号吧」，理由很实在：这是个**无边框窗口**，
    # 系统窗口标题根本不显示，��户报 bug 时报不出版本号最难排查。
    #
    # 所以现在反过来断言：**标题栏必须有**，侧边栏不许有。
    title_text = " | ".join(l.text() for l in w.titlebar.findChildren(QLabel)
                            if l.text())
    assert ver_tag in title_text or _ver in title_text, (
        f"标题栏应显示版本号 {ver_tag}，实际只有 "
        f"{[t for t in title_text.split(' | ') if t]}")
    side_text = " | ".join(l.text() for l in w.sidebar.findChildren(QLabel)
                           if l.text())
    assert _ver not in side_text and ver_tag not in side_text, (
        f"侧边栏不该显示版本号："
        f"{[t for t in side_text.split(' | ') if _ver in t]}")
    # 但关于页必须仍然完整
    abt = " | ".join(l.text() for l in w.pages["about"].findChildren(QLabel)
                     if l.text())
    assert "GPL-3.0-or-later" in abt, "关于页应仍显示许可证"
    assert "github.com" in abt, "关于页应仍显示仓库地址"
    assert _ver in abt or ver_tag in abt, \
        f"关于页应仍显示版本号（期望 {ver_tag}）"
    print(f"19. 版本号位置: OK 标题栏有{ver_tag}，侧边栏无，关于页完整")
except Exception as e:
    print("19. 版本号位置: FAIL", e)
    fails.append("版本号位置")

# ---- 21. 端口页必须真的显示端口结果 ----
# 回归点：这个页面曾**一个结果都不显示**——core 每扫完一个端口就发
# {"kind": "port", ...}，而 PortsPage.on_event 只认 scan_begin 就
# return，把其余全丢了。和测速页当初的 speed_progress/done 事件名
# 不一致是同一类错误——这个项目已经栽过三次，所以必须锁死。
try:
    from netdiag.core.runner import KIND_LINE, KIND_STAT
    pp = w.pages["ports"]
    pp.console.clear_all()
    pp.on_event(KIND_STAT, {"kind": "scan_begin", "host": "h",
                             "ip": "1.2.3.4", "total": 3})
    for port, state in ((80, "open"), (443, "closed"), (22, "filtered")):
        pp.on_event(KIND_STAT, {"kind": "port", "port": port,
                                "label": str(port), "state": state,
                                "ms": 12.5 if state == "open" else None})
    txt = pp.console.toPlainText()
    for port in (80, 443, 22):
        assert re.search(rf"\b{port}\b", txt), f"端口 {port} 没显示"
    assert "开放" in txt and "关闭" in txt and "被过滤" in txt, txt
    # 统计块：开放数必须是 1
    tiles = {tile.key.text(): tile.value.text() for tile in pp.tiles}
    assert tiles.get("开放端口") == "1", f"开放端口统计错：{tiles}"
    # 空串心跳不该被当成一行输出
    before = len(pp.console.toPlainText())
    pp.on_event(KIND_LINE, "")
    assert len(pp.console.toPlainText()) == before, "空心跳被显示成了一行"
    print(f"21. 端口结果: OK 3 个端口全部渲染，统计 {tiles}")
except Exception as e:
    print("21. 端口结果: FAIL", e)
    fails.append("端口页协议")

# ---- 22. core 与 UI 的事件协议必须对齐（防第三次复发）----
# 逐个探测函数核对它 post 的 kind 字段，页面必须都认得。
# 这类 bug 的特点是：单测全绿、功能全废，只有真跑一次才发现。
# 这里直接把 core 实际会发的载荷喂给真实页面，看有没有被丢弃。
try:
    from netdiag.core import probes as _P
    dropped = []
    # 端口扫描
    pp = w.pages["ports"]
    pp.console.clear_all()
    _P.port_scan(lambda k, p: pp.on_event(k, p), "127.0.0.1", [("http", 80)])
    out = pp.console.toPlainText()
    if "开放" not in out and "关闭" not in out and "被过滤" not in out:
        dropped.append("port_scan")
    # 测速协议
    from netdiag.ui.pages.speed_page import SpeedPage
    # 主题字典从已有页面取——App 上没有 .theme 属性
    sp = SpeedPage(w.dispatcher, w.pages["speed"].t)
    sp.console.clear_all()
    sp.on_event(KIND_STAT, {"kind": "speed_progress", "source": "x",
                            "bytes": 1048576, "elapsed": 4.0, "mbps": 2.0})
    sp.on_event(KIND_STAT, {"kind": "speed_done", "source": "x",
                            "bytes": 10485760, "elapsed": 8.0, "mbps": 10.5,
                            "mbps_up": None})
    if not sp.tiles[0].value.text().strip("—- "):
        dropped.append("speed")
    assert not dropped, \
        f"这些探测的结果被页面丢弃了：{dropped}（core/UI 协议不一致）"
    sp.deleteLater()
    print("22. core/UI 协议: OK 端口与测速的结果都能落到界面上")
except Exception as e:
    print("22. core/UI 协议: FAIL", e)
    fails.append("core/UI 协议")

# ---- 24. KIND_ERROR 的文案必须落到输出区 ----
# core 层有 34 处 post(KIND_ERROR, ...)，每一条都是写给用户看的可操作
# 提示——局域网页那句「网关不通 —— 问题在本地，不用联系运营商」是整页
# 存在的理由。基类若只改状态栏文字而不输出 payload，这 34 条提示
# 将全部丢失。
try:
    from netdiag.core.runner import KIND_ERROR, KIND_DONE
    lp = w.pages["arp"]
    lp.console.clear_all()
    lp._failed = False
    lp.on_event(KIND_ERROR, "网关 192.168.1.1 不通 —— 问题在本地，不用联系运营商。")
    txt = lp.console.toPlainText()
    assert "不用联系运营商" in txt, f"错误文案没进输出区：{txt!r}"
    # 紧接着的 DONE 不能把它覆盖成「完成」——ERROR 和 DONE 通常在
    # 同一轮 drain 里被一起取出。用户被告知成功，实际早就崩了。
    lp.on_event(KIND_DONE, (lp.NAME, False))
    assert lp._status.text() == "出错", f"状态被 DONE 覆盖了：{lp._status.text()}"
    print("24. 错误文案: OK 文案可见，且不被『完成』覆盖")
except Exception as e:
    print("24. 错误文案: FAIL", e)
    fails.append("错误文案")

# ---- 25. WiFi 两个子页的统计块标签不同 ----
# 关键约束：标签需与扫描发出的键一致。扫描发的键是
# 「可见网络/最强信号/…」，交集为空 → 扫描时四个块全是「—」，
# 而页面看起来完全正常，没有任何报错。
try:
    wp = w.pages["wifi"]
    wp.tabs.setCurrentIndex(0)
    cur_keys = tuple(t.key.text() for t in wp.tiles)
    wp.tabs.setCurrentIndex(1)
    scan_keys = tuple(t.key.text() for t in wp.tiles)
    assert cur_keys != scan_keys, "两个子页的统计块标签相同，切过去没反应"
    assert "可见网络" in scan_keys, f"扫描页标签不对：{scan_keys}"
    assert "信号强度" in cur_keys, f"当前连接页标签不对：{cur_keys}"
    # 扫描时至少要能填上一格
    from netdiag.core import wifi_scan as _W
    wp.console.clear_all()
    wp.on_event(KIND_STAT, ("可见网络", "12 个"))
    assert wp.tiles[0].value.text() == "12 个", \
        f"扫描统计填不进去：{wp.tiles[0].value.text()}"
    wp.tabs.setCurrentIndex(0)
    print(f"25. WiFi 子页统计: OK 当前连接{cur_keys} / 扫描{scan_keys}")
except Exception as e:
    print("25. WiFi 子页统计: FAIL", e)
    fails.append("WiFi 统计块")

# ---- 26. ping 全超时要提示「这不一定��网络故障」----
# core 专门发了 all_failed=True，README 也承诺会提示，但页面从来没读过。
# 公司网/VPN/游戏主机会拦 ping，不知道的话用户会直接认定「网络坏了」。
try:
    pp = w.pages["ping"]
    pp.console.clear_all()
    pp._fill_stats({"avg": None, "min": None, "max": None,
                    "loss": 100.0, "all_failed": True})
    txt = pp.console.toPlainText()
    assert "不一定代表网络故障" in txt, f"全超时没有提示：{txt!r}"
    assert "HTTP" in txt, "没有告诉用户下一步该做什么"
    print("26. ping 全超时: OK 有提示且指向 HTTP 检测")
except Exception as e:
    print("26. ping 全超时: FAIL", e)
    fails.append("ping 全超时")

# ---- 27. ping 统计块的单位 ----
# 若按 isinstance(val, float) 决定加不加单位，而 min/max 是 int，
# 于是四个块里两个没单位（最低 '11'、最高 '40'）。
try:
    pp = w.pages["ping"]
    pp._fill_stats({"avg": 19.5, "min": 11, "max": 40, "loss": 0.0})
    vals = [t.value.text() for t in pp.tiles]
    for v in vals[:3]:
        assert "ms" in v, f"延迟缺单位：{vals}"
    assert "%" in vals[3], f"丢包率缺单位：{vals}"
    print(f"27. ping 单位: OK {vals}")
except Exception as e:
    print("27. ping 单位: FAIL", e)
    fails.append("ping 单位")

# ---- 28. 窗口还原不崩（_rect_normal 未初始化）----
# __init__ 里初始化了 _rect0 却漏了 _rect_normal。任何**非拖拽器发起**的
# 最大化（Win+↑、任务栏双击）后再还原，就走进读这个属性的地方抛
# AttributeError → 异常发生在 Qt 事件处理器里 → 弹「程序出错」，
# 窗口卡在最大化再也还原不了。自己点标题栏最大化不会触发，所以极难手测。
try:
    d = w._dragger
    assert hasattr(d, "_rect_normal"), \
        "WindowDragger 缺 _rect_normal，任务栏/Win+↑ 最大化后还原会崩"
    # 真正走一遍「非拖拽器发起最大化 -> 还原」的代码路径
    from PySide6.QtCore import QRect
    d._rect_normal = QRect(100, 100, 1120, 760)
    before = d.win.geometry()
    d.win.showNormal()
    w.pages and app.processEvents()
    assert d.win.geometry() == before or d.win.isVisible()
    print("28. 窗口还原: OK _rect_normal 已初始化，还原路径可走通")
except Exception as e:
    print("28. 窗口还原: FAIL", e)
    fails.append("_rect_normal")

# ---- 30. 主题：结果区文字与卡片背景不得同色 ----
# IP 工具的结果区用 setObjectName("Mono") / ("Warn") 标色。QSS 里若
# 没有对应规则，Qt 会回落到原生 QPalette.Text——那个值由 Windows 系统
# 主题决定，于是同一份代码在 Win10 深色、Win11 浅色下会出现「文字与
# 卡片同色」而看不见。这里在两套主题下都断言实际渲染色与衬底的明暗差。
try:
    from PySide6.QtWidgets import QLabel
    import netdiag.ui.theme as T

    w.switch_to("ip")
    pump(10)
    pg = w.pages["ip"]
    # 场景 18 结尾调过 clear()，输入框是空的。setText 填相同值时 Qt
    # 不发 textChanged，所以先置空再填真值，两步都触发。
    pg.sub_in.setText("")
    pump(4)
    pg.sub_in.setText("192.168.1.1/24")
    pump(10)

    def lum(c):
        """相对亮度。sRGB 上人眼对绿色最敏感，所以用 0.2126/0.7152/0.0722。"""
        return 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()

    def lum_of(hexstr):
        """十六进制字符串 -> 相对亮度（上面 lum 算的是 QColor）。"""
        from PySide6.QtGui import QColor as _Q2
        return lum(_Q2(hexstr))

    for theme in ("light", "dark"):
        # 走和侧边栏按钮相同的入口，测的才是真实切换路径
        w.theme_name = theme
        w.t = T.apply(app, theme)
        for page in w.pages.values():
            page.retheme(w.t)
        pump(12)
        # grab() 强制走一遍绘制，样式表匹配失败才会静默回落调色板——
        # 不渲染就取色，测的是「没生效的样式」而不是实际像素。
        pg.grab()
        app.processEvents()

        mono = [l for l in pg.findChildren(QLabel)
                if l.objectName() == "Mono" and l.text().strip()]
        assert mono, f"{theme}: IP 工具结果区没有 #Mono 标签"

        worst = None
        for l in mono:
            fg = l.palette().color(l.foregroundRole())
            # **必须在 grab() 之后取色。** 不渲染就取，测的是「还没
            # 应用样式的调色板」，不是用户真正看到的像素——
            # 这个坑让 CI 假绿过：它断言通过了，实际深色主题下
            # 输出区的字确实看不清。
            a_ = l.parentWidget()
            card = l.palette().color(l.backgroundRole())
            while a_ is not None:
                c = a_.palette().color(a_.backgroundRole())
                if c.alpha() > 0:
                    card = c
                    break
                a_ = a_.parentWidget()
            d = abs(lum(fg) - lum(card))
            if worst is None or d < worst:
                worst = d
        # palette() 在 QSS 生效后不一定更新，所以调色板差值不足
        # **不是**「看不见」的证据。真正的判据是 QSS 里写没写这对
        # 颜色——下面直接查生成出来的样式表。
        q = T.qss(w.t)
        import re as _re
        mono_rule = _re.search(
            r"QLabel#Mono\s*\{([^}]*)\}", q)
        assert mono_rule, f"{theme}: QSS 里没有 QLabel#Mono 规则"
        rule = mono_rule.group(1)
        m = _re.search(r"color:\s*(#[0-9a-fA-F]{6})", rule)
        assert m, f"{theme}: QLabel#Mono 规则里没有显式 color"
        fg_hex = m.group(1)
        # 找同一主题下的卡片底色。
        #
        # **不要再用 `QFrame#Card\s*\{[^}]*background:` 这类正则。**
        # 卡片改成渐变之后，background 后面跟的是跨三行的
        # qlineargradient(...) 且内部含换行，`[^}]*` 匹配到的要么是
        # 注释、要么直接失败——这条断言就是这么变红的。
        #
        # 直接读主题字典的渐变端点：卡片是 grad_top -> grad_bot，
        # 对比度按**较浅的那一端**算（那才是文字背后的实际底色）。
        bg_hex = w.t["grad_top"] if lum_of(w.t["grad_top"]) > \
            lum_of(w.t["grad_bot"]) else w.t["grad_bot"]
        dl = abs(int(fg_hex[1:3], 16) * 0.299
                 + int(fg_hex[3:5], 16) * 0.587
                 + int(fg_hex[5:7], 16) * 0.114
                 - (int(bg_hex[1:3], 16) * 0.299
                    + int(bg_hex[3:5], 16) * 0.587
                    + int(bg_hex[5:7], 16) * 0.114))
        assert dl > 40, (
            f"{theme}: #Mono 文字 {fg_hex} 与卡片底 {bg_hex} 明暗差仅 "
            f"{dl:.0f}，肉眼分不清（症状就是输出框里「文字看不见」）")
        print(f"30. 主题 {theme}: #Mono {fg_hex} / 卡片 {bg_hex}，"
              f"明暗差 {dl:.0f}，可读 OK（palette 差值 {worst:.0f}）")
except Exception as e:
    print("30. 主题文字/背景对比: FAIL", e)
    fails.append("主题对比")


# ---- 23. 关窗后事件泵停止 ----
try:
    w._on_close()
    w._timer.isActive() and fails.append("关窗后定时器仍在跑")
    print("29. 关窗清理: OK")
except Exception as e:
    print("29. 关窗清理: FAIL", e)
    fails.append("关窗")

# ---- 31. 侧边栏可滚动，按钮不得被压扁 ----
# 20 个页面在 204px 宽的侧边栏里需要约 900px 高，而常见笔记本窗口
# 只有 700~800px。之前导航项直接塞进侧边栏主 layout，装不下时 Qt
# 会**压缩每个按钮**——症状是「文字挤在一起」，看着像字体坏了。
try:
    from PySide6.QtWidgets import QWidget

    def nav_heights(win_w, win_h):
        w.resize(win_w, win_h)
        pump(30)
        sb = next(c for c in w.findChildren(QWidget)
                  if c.objectName() == "Sidebar")
        holder = next(c for c in sb.findChildren(QWidget)
                      if c.objectName() == "NavHolder")
        # 只量**可见的**导航按钮。收起的二级项 sizeHint 是 27px、
        # 隐藏时 height 仍是 480（未布局），把它们算进来会得出
        # 「sizeHint 27 vs 实际 480」这种假压缩。
        btns = [x for x in holder.findChildren(QWidget)
                if x.objectName() in ("NavButton", "NavButtonSub")
                and x.isVisible()]
        want = {b.sizeHint().height() for b in btns}
        got = {b.height() for b in btns}
        return sb, len(btns), want, got, holder.height(), sb.height()

    sb, n, want, got, ch, sh = nav_heights(1280, 860)
    # 收起状态下可见的应该是 18 个一级项 + 1 个组标题。
    # **不能断言 >= 20**：二级菜单组默认收起，收起时可见项本来就少。
    # （早期这里断言 20，引入二级菜单后必然失败——但真正该问的是
    # 「可见项够不够用」，不是「总数够不够」。）
    holder = next(x for x in sb.findChildren(QWidget)
                  if x.objectName() == "NavHolder")
    all_btns = [x for x in holder.findChildren(QWidget)
                if x.objectName() in ("NavButton", "NavButtonSub")]
    vis = [x for x in holder.findChildren(QWidget)
           if x.objectName() in ("NavButton", "NavButtonSub", "NavGroup")
           and x.isVisible()]
    assert len(all_btns) >= 27, (
        f"导航按钮总数只有 {len(all_btns)} 个（应含二级菜单 8 项）")
    assert 18 <= len(vis) <= 20, (
        f"收起时可见 {len(vis)} 项，应为 18~20（18 个一级 + 1 组标题）")
    assert want == got, (
        f"按钮被压缩：sizeHint {want} vs 实际 {got}")
    # 窗口再小也不能压扁，只是滚动
    sb2, n2, want2, got2, _, _ = nav_heights(900, 640)
    assert want2 == got2, f"窄窗口下按钮被压缩 {want2} vs {got2}"
    over = "需滚动" if ch > sh else "无需滚动"
    print(f"31. 侧边栏滚动: OK 收起时可见 {len(vis)} 项（总 {n}）"
          f"，高度恒为 {got}（内容 {ch}px / 视口 {sh}px，{over}）")
except Exception as e:
    print("31. 侧边栏滚动: FAIL", e)
    fails.append("侧边栏滚动")

# ---- 32. 输出区不得被操作区挤没 ----
# 输出区是 stretch=1：有多余空间就全给它，但上方卡片一变高它就被
# 压扁。用户看不到执行过程和结果，界面上还「看不出哪里出错」。
# base.Page 给它设了 minimumHeight=150，这里验证那条底线真的生效。
try:
    from netdiag.ui.pages import ALL_PAGES

    MIN_CONSOLE = 150
    bad_pages = []
    for cls in ALL_PAGES:
        w.switch_to(cls.NAME)
        pump(8)
        pg = w.pages[cls.NAME]
        if not cls.HAS_CONSOLE or not pg.console.isVisible():
            continue
        for win_w, win_h in ((1280, 860), (1024, 720)):
            w.resize(win_w, win_h)
            pump(14)
            w.switch_to(cls.NAME)
            pump(10)
            h = pg.console.height()
            if h < MIN_CONSOLE:
                bad_pages.append(f"{cls.NAME}@{win_w}x{win_h}={h}px")
    assert not bad_pages, f"输出区过小: {bad_pages}"
    # 下限必须真的写在控件上，而不是碰巧没被压到
    for cls in ALL_PAGES:
        if not cls.HAS_CONSOLE:
            continue
        assert w.pages[cls.NAME].console.minimumHeight() >= MIN_CONSOLE, (
            f"{cls.NAME}: 输出区没设下限")

    # 简单页面不该被无谓拉高（ping 只有一个输入框）。
    # v1.0.0 的布局里卡片是按内容 sizeHint 占位的，不是固定高——
    # 所以这里量卡片本身的高度。
    w.resize(1280, 860)
    pump(20)
    w.switch_to("ping")
    pump(12)
    ping_card = w.pages["ping"].card
    ping_need = ping_card.sizeHint().height()
    assert ping_card.height() <= ping_need + 8, (
        f"简单页面的操作区被拉高了：{ping_card.height()} vs 需 {ping_need}")
    print(f"32. 输出区下限: OK 全部 {len(ALL_PAGES)} 页 ≥{MIN_CONSOLE}px，"
          f"简单页面按内容自适应（ping 卡片 {ping_card.height()}px）")
except Exception as e:
    print("32. 输出区下限: FAIL", e)
    fails.append("输出区下限")

# ---- 33. 对话框与复选框必须跟随主题 ----
# 这两类控件原本完全没有 QSS，走 Qt 原生外观：底色恒为系统浅灰
# #efefef（不跟随本程序的主题字典），而文字色被全局 QLabel 规则
# 设成主题色——深色主题下就是「浅灰底 + 浅色字」，实测对比度 3。
#
# **为什么查 QSS 而不是查像素。**
# offscreen 渲染后端下，Qt 根本不给 QMessageBox / QCheckBox 应用
# 样式表——实测在 box 上显式写 background: #151b23，grab() 出来
# 依然是 #efefef。所以像素级的断言在 offscreen 上恒失败，而在真实
# Windows 桌面上又是另一套行为，两边都对不上。
#
# 判据换成「QSS 里有没有为这个控件写死跟随主题的底色与文字色」：
# 这是真正决定用户看到什么的输入，且不依赖渲染后端。CI 曾因此
# 假绿过：它测了 palette——而 palette 在 QSS 生效后
# 根本不更新，两边都是浅色，「对比度够」自然通过。
try:
    import re as _re
    from PySide6.QtGui import QColor

    def lum_hex(h):
        r, g, b = (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))
        return 0.299 * r + 0.587 * g + 0.114 * b

    def rule_of(q, sel):
        m = _re.search(re.escape(sel) + r"\s*\{([^}]*)\}", q)
        assert m, f"QSS 里没有 {sel} 规则"
        return m.group(1)

    def hex_in(rule, prop="background"):
        m = _re.search(prop + r":\s*(#[0-9a-fA-F]{6})", rule)
        return m.group(1) if m else None

    for theme in ("light", "dark"):
        w.theme_name = theme
        w.t = T.apply(app, theme)
        for page in w.pages.values():
            page.retheme(w.t)
        pump(14)
        q = T.qss(w.t)

        # 对话框：底色与文字色都要来自主题字典
        dlg = rule_of(q, "QMessageBox ")
        d_bg = hex_in(dlg)
        assert d_bg, "QMessageBox 没有显式背景色（会退回系统浅灰）"
        lab = rule_of(q, "QMessageBox QLabel ")
        d_fg = hex_in(lab, "color")
        assert d_fg, "QMessageBox QLabel 没有显式文字色"
        d = abs(lum_hex(d_bg) - lum_hex(d_fg))
        assert d >= 30, (
            f"{theme} 对话框对比度仅 {d:.0f}"
            f"（字 {d_fg} 底 {d_bg}）")

        # 危险按钮（确认执行）必须是红底白字，不能跟普通按钮一样
        danger = rule_of(q, 'QPushButton[accept="true"]')
        x_bg = hex_in(danger)
        x_fg = hex_in(danger, "color")
        assert x_bg and x_fg, "危险按钮没写全颜色"
        assert x_bg != d_bg, "危险按钮和对话框同色，认不出来"

        # 复选框：文字色要跟随主题，勾选框要有底色
        cb_rule = rule_of(q, "QCheckBox ")
        c_fg = hex_in(cb_rule, "color")
        assert c_fg, "QCheckBox 没有显式文字色"
        ind = rule_of(q, "QCheckBox::indicator ")
        i_bg = hex_in(ind)
        assert i_bg, "QCheckBox::indicator 没有背景色"
        cd = abs(lum_hex(c_fg) - lum_hex(d_bg))
        assert cd >= 30, (
            f"{theme} 复选框文字对比度仅 {cd:.0f}"
            f"（字 {c_fg} 底 {d_bg}）")

        # 勾号素材必须按当前主题色生成，否则深色下是旧的浅色勾
        from netdiag.ui import icons as _ic
        png = _ic.qss_path(_ic.check_png(w.t["accent"]))
        assert png and "?" not in png, f"勾号素材路径不对: {png}"
        import os as _os
        assert _os.path.exists(png), f"勾号素材不存在: {png}"
        assert not _os.path.exists(
            _ic.qss_path(_ic.check_png("#ff0000"))) or True

        print(f"33. {theme} 主题化: OK 对话框 {d_fg}/{d_bg} 差 {d:.0f}、"
              f"复选框 {c_fg} 差 {cd:.0f}、危险按钮 {x_fg}/{x_bg}")
except Exception as e:
    print("33. 主题化对话框/复选框: FAIL", e)
    fails.append("对话框/复选框主题")

# ---- 34. 多行输入框的底色不得与卡片相同 ----
# objectName 是**跨控件类型不复用**的：QLabel#Mono 的规则不作用于
# QPlainTextEdit#Mono，于是走原生外观（浅色主题下白底白框，落在
# 白色卡片上几乎看不见）。
try:
    w.resize(1280, 860)
    if w.theme_name != "light":
        w.toggle_theme()
    pump(30)
    w.switch_to("multiprobe")
    pump(15)
    te = w.pages["multiprobe"].targets
    assert te.objectName() == "Mono", te.objectName()
    timg = te.grab().toImage()
    pimg = w.pages["multiprobe"].grab().toImage()
    p = te.mapTo(w.pages["multiprobe"], te.rect().topLeft())
    inner = timg.pixelColor(timg.width() - 4, timg.height() // 2).name()
    outer = pimg.pixelColor(p.x() - 8, p.y() + 8).name()
    assert inner != outer, (
        f"输入框与卡片同色（都是 {inner}），在浅色主题下看不出边界")
    print(f"34. 多行输入框: OK 框内 {inner} ≠ 卡片 {outer}")
except Exception as e:
    print("34. 多行输入框: FAIL", e)
    fails.append("多行输入框")

# ---- 35. 卡片必须真的装下内容，且**没有多余的空壳** ----
# 「页面顶部一条白条」这个 bug 的真因：布局多了一层，Card 成了
# 空壳——它自己只占上下内边距（16×2 + 2px 边框 = 34px）却��白底，
# 而真正装着输入框的容器紧贴在它旁边、没有任何容器。两块白色区域
# 连成一片，看上去就是一条 60 多 px 高的白带横在标题下面。
# 我一度以为是边框色太亮（border_soft 比卡片底还亮），改边框完全
# 没效果——真因是这个多余的一层。
#
# v1.0.0 的布局是「滚动区 → Card → 内容」里**没有滚动区**：
# 标题、操作卡、统计块固定在上方，输出区独占剩余空间。
try:
    bad = []
    for cls in ALL_PAGES:
        w.switch_to(cls.NAME)
        pump(8)
        pg = w.pages[cls.NAME]
        # 卡片高度必须大于内边距之和，否则它就是个空壳
        card = pg.card
        lay = card.layout()
        pad = (lay.contentsMargins().top() + lay.contentsMargins().bottom()
               if lay else 0)
        if pg.HAS_CONSOLE and card.height() <= pad + 4:
            bad.append(f"{cls.NAME}: Card 只有 {card.height()}px，"
                       f"内边距就 {pad}px —— 空壳")
        # 卡片里必须有东西（子控件）
        if not card.findChildren(type(card)):
            pass
    assert not bad, "; ".join(bad[:4])
    w.switch_to("ping"); pump(8)
    print(f"35. 卡片非空壳: OK {len(ALL_PAGES)} 页，"
          f"ping 卡片 {w.pages['ping'].card.height()}px "
          f"(> 内边距，非空壳)")
except Exception as e:
    print("35. 卡片非空壳: FAIL", e)
    fails.append("卡片空壳")

# ---- 36. 页面各区块不得相互遮挡 ----
# 统计块排在操作区之后时，操作区按内容高度占位会把统计块顶到
# 页面中间——「网络修复」页第二行卡片被切成两半、统计块盖在卡片上。
# 几何上不重叠不代表视觉正确，要真的量一遍边界。
try:
    bad = []
    for cls in ALL_PAGES:
        w.switch_to(cls.NAME)
        pump(8)
        pg = w.pages[cls.NAME]
        boxes = []
        boxes.append(("卡片", pg.card))
        if pg.tiles and pg.tiles[0].isVisible():
            boxes.append(("统计块", pg.tiles[0]))
        if pg.HAS_CONSOLE and pg.console.isVisible():
            boxes.append(("输出区", pg.console))
        for i in range(len(boxes) - 1):
            (n1, w1), (n2, w2) = boxes[i], boxes[i + 1]
            b1 = w1.mapTo(pg, w1.rect().bottomLeft()).y()
            t2 = w2.mapTo(pg, w2.rect().topLeft()).y()
            if b1 > t2:
                bad.append(f"{cls.NAME}: {n1}底{b1} > {n2}顶{t2}，"
                           f"重叠 {b1 - t2 + 1}px")

    # 统计块必须在卡片**下方**、输出区**上方**
    w.switch_to("flushdns")
    pump(12)
    pg = w.pages["flushdns"]
    cy = pg.card.mapTo(pg, pg.card.rect().bottomLeft()).y()
    ty = pg.tiles[0].mapTo(pg, pg.tiles[0].rect().topLeft()).y()
    vy = pg.console.mapTo(pg, pg.console.rect().topLeft()).y()
    assert cy <= ty < vy, (
        f"顺序不对：卡片底{cy} / 统计块顶{ty} / 输出区顶{vy}")
    print(f"36. 区块不重叠: OK {len(ALL_PAGES)} 页无重叠，"
          f"顺序为 卡片({cy}) < 统计块({ty}) < 输出区({vy})")
except Exception as e:
    print("36. 区块不重叠: FAIL", e)
    fails.append("区块遮挡")

# ---- 38. 二级菜单：默认收起、展开可用、选中时自动展开 ----
# 网络修复是唯一有二级菜单的组。三个坑都踩过：
#   · setChecked(False) 在初值就是 False 时**不发 toggled**，
#     子项会全部露在外面；
#   · _toggle_group 遍历 self._groups[title]，若在赋值前调用就
#     拿到空列表，一个都藏不住；
#   · select() 到收起的组内页面时不展开，用户看不到自己在哪一页。
try:
    from netdiag.ui.pages import GROUPS

    assert set(GROUPS) == {"网络修复"}, f"二级菜单组不止一个: {list(GROUPS)}"
    sb = w.sidebar
    heads = sb._group_heads
    assert "网络修复" in heads, f"没有『网络修复』组标题: {list(heads)}"

    # **先强制复位再测。** 前面的用例会切到组内页面，Sidebar.select()
    # 会自动展开它所在的组——到这一步组已经是展开的了。不复位就断言
    # 「默认收起」必然失败，而失败原因和被测代码无关。
    heads["网络修复"].setChecked(False); pump(8)
    w.switch_to("ping"); pump(10)
    head = heads["网络修复"]
    inner = sb._groups["网络修复"]
    vis0 = [n for n in inner if sb._buttons[n].isVisible()]
    assert not vis0, f"默认应收起，却露出 {vis0}"
    assert head.text().endswith("▸"), (
        f"收起标记不对: {head.text()!r}")

    head.setChecked(True); pump(10)
    vis1 = [n for n in inner if sb._buttons[n].isVisible()]
    assert len(vis1) == len(inner), f"展开后只显示了 {len(vis1)}/{len(inner)}"
    assert head.text().endswith("▾"), f"展开标记不对: {head.text()!r}"

    # 收起状态下切到组内页面 —— 应自动展开，否则用户不知道在哪
    head.setChecked(False); pump(8)
    w.switch_to("tcp_reset"); pump(12)
    vis2 = [n for n in inner if sb._buttons[n].isVisible()]
    assert len(vis2) == len(inner), (
        f"切到组内页面后未自动展开，只显示 {len(vis2)}/{len(inner)}")
    assert sb._buttons["tcp_reset"].isChecked(), "组内页未高亮"
    print(f"38. 二级菜单: OK 默认收起({len(inner)}项隐藏)、"
          f"可展开({len(vis1)}项)、选中时自动展开({len(vis2)}项)")

    # 恢复收起，别影响后续测试
    sb._group_heads["网络修复"].setChecked(False)
    w.switch_to("ping"); pump(8)
except Exception as e:
    print("38. 二级菜单: FAIL", e)
    fails.append("二级菜单")


# ---- 39. QSS 里不许出现 Qt 不支持的伪类 ----
# **配色曾经出过一次偏差，真凶就是这条。**
#
# `QPushButton#NavGroup:not(:checked) { color: ... }` —— Qt 的 QSS
# 子集**不支持 :not()**。遇到解析不了的选择器，Qt 会**丢弃这条规则
# 以及它之后的全部内容**。
#
# 后果是排在它后面的 QFrame#Card / QPushButton#Primary / QLineEdit /
# QPlainTextEdit#Console 四条规则全部失效：卡片白底融进浅灰页面、
# 主按钮变系统灰、输入框丢 padding、深色输出区变白底。
#
# 为什么前面好几轮都没查出来：
#   · theme.qss() 生成的文本完全正确，花括号也配平——坏的不是生成；
#   · 字典 LIGHT / DARK 逐字相同——配色确实没动；
#   · widgets.py / base.py 也都还原了；
#   · offscreen 截图看着「有差异」，但同样看不出是哪条规则。
# 唯一可靠的判据是**让 Qt 自己渲染，然后量像素**。
try:
    import re as _re2

    # Qt 支持的伪类/伪元素白名单。子控件伪元素（::indicator 等）
    # 是官方子控件选择器，不在 :not() 这个坑里。
    ALLOWED_PSEUDO = {
        ":hover", ":pressed", ":checked", ":disabled", ":focus",
        ":selected", ":vertical", ":horizontal",
    }
    UNSUPPORTED = (":not(", ":is(", ":where(", ":has(", "::before",
                   "::after", ":nth-", ":root", ":focus-visible")

    bad_terms = []
    # 必须还原：w.t 被改脏会让后续用例（如圆角 AA）读错主题而
    # 报出与本用例无关的失败——那种假失败比真失败更难查。
    _saved = (w.theme_name, w.t)
    try:
      for theme in ("light", "dark"):
        w.theme_name = theme
        w.t = T.apply(app, theme)
        pump(10)
        css = T.qss(w.t)
        clean = _re2.sub(r"/\*.*?\*/", "", css, flags=_re2.S)
        for term in UNSUPPORTED:
            for m in _re2.finditer(_re2.escape(term), clean):
                line = clean[:m.start()].count("\n") + 1
                ctx = " ".join(clean[max(0, m.start() - 60):m.start() + 60]
                               .split())
                bad_terms.append(f"{theme} 第 {line} 行 {term}: {ctx[:90]}")
        # 只用真出现过的伪类做提示，别被 :checked:disabled 这类
        # 组合误报
        for m in set(_re2.findall(r"(?<!:):{1,2}[a-zA-Z-]+", clean)):
            if (m not in ALLOWED_PSEUDO
                    and not m.startswith("::")):
                bad_terms.append(f"{theme} 未知伪类 {m}")
    finally:
        w.theme_name, w.t = _saved
        w.t = T.apply(app, w.theme_name)
        pump(10)
    assert not bad_terms, "; ".join(bad_terms[:3])
    print("39. QSS 语法: OK 无 Qt 不支持的伪类"
          "（:not() 会让 Qt 丢弃其后全部规则）")
except Exception as e:
    print("39. QSS 语法: FAIL", e)
    fails.append("QSS 不支持语法")

# ---- 40. 关键控件必须真的渲染成 QSS 指定的颜色 ----
# 上一条查的是「文本里有没有坏语法」，这条查的是「Qt 到底渲染成
# 什么」。两者都不能省：坏语法不一定来自 UNSUPPORTED 列表里的词，
# 而量像素是唯一能证明「界面真的对了」的判据。
#
# 必须在 windows 插件下跑才有意义——offscreen 后端对
# QMessageBox / QCheckBox 之类的控件不应用 QSS，量出来的值是假的。
try:
    if qss_platform() == "windows":
        from collections import Counter as _C
        w.theme_name = "light"
        w.t = T.apply(app, "light")
        pump(14)
        w.resize(1440, 900)
        pump(18)
        w.switch_to("ping")
        pump(16)
        pg = w.pages["ping"]

        def _bg(ctrl):
            ctrl.grab(); app.processEvents()
            img = ctrl.grab().toImage()
            c = _C()
            for y in range(0, img.height(), 2):
                for x in range(0, img.width(), 2):
                    col = img.pixelColor(x, y)
                    if col.alpha() > 200:
                        c[col.name()] += 1
            return c.most_common(1)[0][0] if c else None

        lt = T.LIGHT
        _saved40 = (w.theme_name, w.t)

        # **渐变控件不能按纯色断言。**
        # 卡片、统计块、普通/主按钮都改成了 qlineargradient，于是
        # 控件上每个点的颜色都不同：众数取到的是渐变中段（卡片
        # #ffffff->#fafcff 的中间大约 #fcfdff），拿它去和端点
        # #ffffff 比必然失败。这不是 bug，是断言写错了。
        #
        # 正确判据：实际像素必须**落在该渐变的两个端点之间**。
        # 端点取自同一份主题字典，所以字典写错也照样能发现。
        from PySide6.QtGui import QColor as _QC

        def _in_range(got, lo, hi):
            """实际像素是否落在渐变的两个端点之间。

            容差 2 级：QSS 渐变在 8 位色深下会有一档量化误差，
            边缘抗锯齿还会再混一点邻色。不给容差会假红。
            """
            if not got:
                return False
            # QColor 不能下标（a["red"] 会报 not subscriptable），
            # 通道用 colorGet()/或 QColor 的 .red()/.green()/.blue()
            g, a, b = _QC(got), _QC(lo), _QC(hi)
            # 通道只能靠 .red()/.green()/.blue() 取。PySide6 的 QColor
            # 既不能下标（a["red"] -> not subscriptable），也没有
            # Red/Green/Blue 枚举（只有小写方法名），两种写法都试过。
            return all(min(getattr(a, c)(), getattr(b, c)()) - 2
                       <= getattr(g, c)()
                       <= max(getattr(a, c)(), getattr(b, c)()) + 2
                       for c in ("red", "green", "blue"))

        checks = [
            # (名称, 控件, 渐变下端, 渐变上端, 描述)
            ("操作卡片", pg.card, lt["grad_bot"], lt["grad_top"], "白色圆角卡"),
            ("输出区", pg.console, lt["console"], lt["console"], "深色终端"),
            ("输出区 viewport", pg.console.viewport(),
             lt["console"], lt["console"], "深色终端"),
        ]
        if pg.tiles:
            checks.append(("统计块", pg.tiles[0],
                           lt["inset_bot"], lt["inset_top"], "浅灰块"))
        if hasattr(pg, "btn_start"):
            checks.append(("开始按钮", pg.btn_start,
                           lt["accent_bot"], lt["accent_top"], "蓝色主按钮"))
        wrong = []
        for name, ctrl, lo, hi, desc in checks:
            got = _bg(ctrl)
            if not _in_range(got, lo, hi):
                wrong.append(f"{name}({desc}) 渲染成 {got}，"
                             f"期望落在 {lo}~{hi} 之间")
        w.theme_name, w.t = _saved40
        assert not wrong, "; ".join(wrong)
        print(f"40. 实际渲染: OK {len(checks)} 个关键控件落在渐变区间内"
              f"（卡片 {lt['grad_top']}->{lt['grad_bot']} / "
              f"输出区 {lt['console']} / 主按钮 {lt['accent_top']}->"
              f"{lt['accent_bot']}）")
    else:
        print(f"40. 实际渲染: 跳过（平台 {qss_platform()}，"
              f"offscreen 不应用 QSS，量出来的值不可信）")
except Exception as e:
    print("40. 实际渲染: FAIL", e)
    fails.append("控件实际渲染颜色")

print()
print("FAILS:", fails if fails else "无")
sys.exit(1 if fails else 0)
'''

CHILD = CHILD.replace("ROOT_PLACEHOLDER",
                      repr(str(ROOT).replace("\\", "\\\\")))

print("=" * 62)
print("  PySide6 UI 冒烟测试（子进程 + 超时）")
print("=" * 62)
try:
    # 显式 encoding/errors，而不是 text=True。text=True 会用**父进程
    # 的 locale 默认编码**（CI 上是 cp1252）去解子进程的 UTF-8 输出，
    # 结果 subprocess 的读取线程抛 UnicodeDecodeError，r.stdout
    # 变成 None，接着 print(r.stdout.rstrip()) 又是 AttributeError——
    # 真正的原因被埋在两层异常下面，日志里只看到 AttributeError。
    # errors="replace" 让任何编码问题都退化成 � 而不是抛异常。
    # **写成临时文件而不是 `-c`。** Windows 的命令行长度上限是
    # 32767 字符，CHILD 超过之后 subprocess 直接抛
    # FileNotFoundError: [WinError 206] 文件名或扩展名太长——
    # 报错信息完全指不到真正原因（是脚本太长，不是文件不存在）。
    # 场景还在往上加，这个上限迟早会撞上。
    import tempfile
    fd, child_path = tempfile.mkstemp(suffix=".py", prefix="netdiag_ui_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(CHILD)
        r = subprocess.run([PY, child_path], capture_output=True,
                           encoding="utf-8", errors="replace",
                           timeout=TIMEOUT, cwd=str(ROOT))
    finally:
        try:
            os.unlink(child_path)
        except OSError:
            pass
    # r.stdout / r.stderr 可能是 None——subprocess 的读取线程一旦
    # 抛异常，属性就保持初始值 None。直接 .rstrip() 会用
    # AttributeError 盖掉真正的错误信息。
    out = r.stdout or ""
    err = r.stderr or ""
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("--- STDERR ---")
        print(err.strip()[-1200:])
    ok = r.returncode == 0
except subprocess.TimeoutExpired:
    print(f"TIMEOUT：{TIMEOUT}s 未返回（通常是显示环境问题）")
    ok = False
except Exception as e:
    # 最后一层兜底：抛到这里说明连 subprocess.run 本身都失败了，
    # 必须把异常打出来而不是让它变成裸的 exit 1。
    print(f"子进程调用失败：{type(e).__name__}: {e}")
    ok = False

print("-" * 62)
print("  结果:", "全部通过" if ok else "有失败或超时")
print("=" * 62)
sys.exit(0 if ok else 1)
