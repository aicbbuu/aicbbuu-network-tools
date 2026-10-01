"""
设计系统：颜色、间距、字体、QSS 样式表。

与常规 Qt 项目的不同：样式集中在一处（这里），控件本身不碰
颜色。切主题只需换一份字典 + 重新 setStyleSheet。

形状语言
--------
圆角只用两个尺度：12px（卡片 / 按钮 / 输入框 / 窗口）、8px
（导航项、标签、统计块）。层级靠**阴影和背景色差**区分，不靠
加大圆角——这是现代扁平设计的惯例，也避免界面看着花。
"""
from __future__ import annotations

from . import icons as _icons

from typing import Any

# ---------------------------------------------------------------- #
#  尺度
# ---------------------------------------------------------------- #
# 主圆角：卡片、按钮、输入框、窗口。**必须与 App.CORNER 一致**
# ——侧边栏用 QSS 画自己的左侧圆角，两个值不同就会出现「窗口是
# 圆角、侧边栏是直角」的错位（左下角尤其明显）。
RADIUS = 14
RADIUS_SM = 8       # 小圆角：导航项、状态块

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
SPACE_2XL = 32

NAV_W = 204
TITLE_H = 44


# ---------------------------------------------------------------- #
#  配色
# ---------------------------------------------------------------- #
LIGHT: dict[str, Any] = {
    "name": "light",
    # 背景层级：越靠前的层越亮
    "window":      "#f4f7fb",
    "sidebar":     "#ffffff",
    "card":        "#ffffff",
    "surface":     "#f8fafc",
    "inset":       "#eef2f7",
    "hover":       "#eaf1ff",
    "border":      "#dbe4f0",
    "border_soft": "#e9eff6",
    # 文字
    "text":        "#16202f",
    "text_dim":    "#5b6880",
    "text_mute":   "#8b97ab",
    "text_invert": "#ffffff",
    # 品牌 / 语义
    "accent":      "#2563eb",
    "accent_hi":   "#3b82f6",
    "accent_soft": "#dce9ff",
    "accent_dim":  "#1d4ed8",
    "ok":          "#0f9d58",
    "warn":        "#c2740a",
    "err":         "#dc2626",
    # 输出面板（等宽文本区）
    "console":     "#111827",
    "console_fg":  "#d7e0f0",
    "console_dim": "#7b8ba6",
    # 阴影
    "shadow":      "#0f172a",
    # 投影原来 alpha=24，白卡在 #f4f7fb 底上几乎看不出来——
    # 卡片读起来是一块平色。40 才够让边缘浮起来，又不至发灰。
    "shadow_a":    40,
    "shadow_a_hi": 42,
    # 渐变端点。纯平色块是「不够细腻」的主因：真实软件给按钮、卡片、
    # 统计块都一点上亮下暗的过渡，眼睛会读成「有体积」。
    # 值都取同色系相邻色阶，幅度刻意小（2~8%），避免变成花哨配色。
    # 深色主题另有一套——那里「高光」是提亮，不是纯白。
    "grad_top":     "#ffffff",   # 卡片/统计块上缘高光
    "grad_bot":     "#fafcff",   # 下缘微暗
    "accent_top":   "#3b82f6",   # 主按钮上缘
    "accent_bot":   "#2563eb",   # 主按钮下缘
    "err_top":      "#f2585b",   # 危险按钮上缘
    "err_bot":      "#dc2626",
    "warn_top":     "#f59e0b",   # 中风险按钮上缘
    "warn_bot":     "#c2740a",
    "inset_top":    "#f2f6fc",   # 统计块上缘
    "inset_bot":    "#e9eff7",   # 下缘
    "btn_top":      "#ffffff",   # 次要按钮上缘
    "btn_bot":      "#f1f5f9",   # 下缘
}

DARK: dict[str, Any] = {
    "name": "dark",
    "window":      "#0d1117",
    "sidebar":     "#141a23",
    "card":        "#1a222d",
    "surface":     "#202a37",
    "inset":       "#0f141b",
    "hover":       "#24334c",
    "border":      "#2b3644",
    "border_soft": "#232d39",
    "text":        "#e6edf7",
    "text_dim":    "#9aa8bd",
    "text_mute":   "#6b7a91",
    "text_invert": "#0d1117",
    "accent":      "#3b82f6",
    "accent_hi":   "#60a5fa",
    "accent_soft": "#1d3356",
    "accent_dim":  "#93c5fd",
    "ok":          "#34d399",
    "warn":        "#fbbf24",
    "err":         "#f87171",
    "console":     "#070b11",
    "console_fg":  "#c3cfe2",
    "console_dim": "#6d7f99",
    "shadow":      "#000000",
    # 深色里投影靠「更深」而不是「更黑」——纯黑 alpha 60 压在
    # #0d1117 上会糊成一片，反把卡片边界吃掉。52 刚好。
    "shadow_a":    52,
    "shadow_a_hi": 90,
    "grad_top":     "#1f2836",
    "grad_bot":     "#161d27",
    "accent_top":   "#4f8ff7",
    "accent_bot":   "#2f6fe0",
    "err_top":      "#f87171",   # 危险按钮上缘
    "err_bot":      "#b91c1c",
    "warn_top":     "#fbbf24",   # 中风险按钮上缘
    "warn_bot":     "#d97706",
    "inset_top":    "#131a23",
    "inset_bot":    "#0c1117",
    "btn_top":      "#232c39",
    "btn_bot":      "#1b232e",
}

THEMES = {"light": LIGHT, "dark": DARK}


def get(name: str | None) -> dict[str, Any]:
    return THEMES.get(name or "light", LIGHT)


# ---------------------------------------------------------------- #
#  QSS
# ---------------------------------------------------------------- #
def qss(t: dict[str, Any]) -> str:
    """生成整份样式表。

    QSS 是 Qt 的 CSS 子集：支持盒模型、border-radius、渐变、
    :hover / :pressed / :checked / :disabled 伪类，但**不支持**
    flex、grid、box-shadow（阴影走 QGraphicsDropShadowEffect）。
    """
    # 复选框的勾号、下拉框的箭头。QSS 的 image: url() 需要一个真实
    # 文件路径，所以按主题色实时生成到临时目录——素材不进版本库，
    # 也不会被 PyInstaller 打包成构建时的旧颜色。
    _check = _icons.qss_path(_icons.check_png(t["accent"]))
    # 箭头取次要文字色：比正文淡，比禁用态深，两种主题都成立。
    _chev = _icons.qss_path(_icons.chevron_png(t["text_dim"]))
    # 禁用态不能沿用同一个箭头——否则「能用」和「不能用」看起来一样。
    _chev_off = _icons.qss_path(_icons.chevron_png(t["text_mute"]))
    chevron_light = _chev.replace("\\", "/")
    chevron_light_dis = _chev_off.replace("\\", "/")
    accent = t["accent"]
    accent_hi = t["accent_hi"]
    accent_soft = t["accent_soft"]
    accent_fg = t["accent_dim"] if t["name"] == "light" else accent_hi
    border = t["border"]
    text = t["text"]
    text_dim = t["text_dim"]
    card = t["card"]
    window_bg = t["window"]
    surface = t["surface"]
    inset = t["inset"]
    warn = t["warn"]
    radius_sm = RADIUS_SM

    return f"""
/* ---------- 基础 ----------
    注意：不要写无条件的「QWidget 加 background 通配」。
   QSS 的样式会沿控件树继承，一条全局背景规则会污染所有子控件的
   调色板，表现为按钮的 background 失效（只有 border / color
   命中，控件变成透明或继承父级的浅色）。
   这里只设字体和文字色，背景由各分区自己声明。 */
QWidget {{
    color: {text};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}}

/* ---------- 窗口与分区 ---------- */
/* RoundedRoot 自己用 QPainter 画抗锯齿圆角背景。
   这里**绝不能**设 background —— QSS 一旦上色，圆角外的
   透明区就被填成不透明，QPainter 的抗锯齿边缘会被彻底盖掉，
   7 倍放大看到的就是硬阶梯。 */
/* ---------- 统计块 ---------- */
#StatTile {{
    border: none;
    border-radius: {radius_sm}px;

    /* 微渐变：上缘略亮。四个块并排时，纯色读起来是「一整条灰带」，
       加一点点上亮下暗后每块自己有了边界感，间隙不用靠描边去分。 */
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["inset_top"]},
        stop:1 {t["inset_bot"]}
    );
}}

/* ---------- 结果区 ---------- */
/* 输入非法时的提示。不用 QSS 会退回默认色，在深色主题下几乎
   看不见——用户会以为是空白。 */
Warn {{
    color: {warn};
    background: transparent;
    border: none;
    font-size: 13px;
}}
/* 计算结果的值。等宽字体让数字能上下对齐，长二进制串也不乱。 */
Mono {{
    color: {text};
    background: transparent;
    border: none;
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 13px;
}}

/* ---------- 关于页 ---------- */
/* 小标题：比正文重、比主标题轻。必须用 QSS 声明而不是内联
   setStyleSheet——内联会打断子树的全局 QSS 继承。 */
SubHead {{
    color: {text};
    font-size: 14px;
    font-weight: 600;
    background: transparent;
    border: none;
}}
/* 可点击的链接。Qt 里 QLabel 要设 openExternalLinks 才会变成手型
   光标并可点，文字色也要自己给。 */
Link {{
    color: {accent};
    background: transparent;
    border: none;
    text-decoration: underline;
}}
#RootFrame {{
    background: transparent;
    border: none;
}}
#Sidebar {{
    background: {t["sidebar"]};
    /* 左侧圆角**必须由 QSS 自己画**。RoundedRoot 只负责画窗口
       底色的圆角，它并不裁剪子控件；侧边栏是个不透明的直角矩形，
       会直接盖住左下角的圆角（实测大窗口下左下角变直角）。
       半径必须与 App.CORNER 一致。 */
    border-top-left-radius: {RADIUS}px;
    border-bottom-left-radius: {RADIUS}px;
    border-right: 1px solid {t["border_soft"]};
}}
/* 关键：这些「透明容器」必须靠 QSS 声明，不能用内联
   setStyleSheet("background: transparent")。
   Qt 的规则是：控件树上只要有任一祖先带非空内联样式表，
   全局 QSS 对该子树就整体失效——表现是子控件的 background
   全部丢失（按钮变透明、只剩边框）。 */
#PageArea, #CardArea, #BodyArea, #TitleBar,
#PageScroll, #PageScroll > QWidget,
QStackedWidget, QStackedWidget > * {{
    background: transparent;
}}
#PageArea {{ background: {window_bg}; }}

#TitleLabel {{ font-size: 13px; font-weight: 600; color: {text}; }}
#BrandName   {{ font-size: 16px; font-weight: 700; color: {accent}; }}
#BrandSub    {{ font-size: 11px; color: {t["text_mute"]}; }}

/* 侧边栏内的无边框容器：显式声明透明，否则会继承成浅灰 */
#Sidebar QWidget {{
    background: transparent;
}}

/* ---------- 导航 ---------- */
/* 滚动区必须显式透明。QScrollArea 自带 viewport，父级那条
   #Sidebar QWidget 规则不一定覆盖到它，结果滚动区里出现一块与
   侧边栏不同色的底。 */
#NavScroll, #NavScroll > QWidget, #NavHolder {{
    background: transparent;
    border: none;
}}
#NavScroll QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
#NavScroll QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 4px;
    min-height: 24px;
}}
#NavScroll QScrollBar::handle:vertical:hover {{
    background: {t["text_mute"]};
}}
#NavScroll QScrollBar::add-line, #NavScroll QScrollBar::sub-line {{
    height: 0; width: 0;
}}
#NavScroll QScrollBar::add-page, #NavScroll QScrollBar::sub-page {{
    background: transparent;
}}

QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    color: {text_dim};
    /* padding 与行高都用 v1.0.0 的取值（9px 12px / 31px 行高）。
       我曾收窄到 7px 10px，那是在导航项还带 17px 图标时调的
       （去掉图标后行高不变、只是左右留白少一点）。图标已经取消，
       恢复成原来的留白，与 v1.0.0 一致。 */
    padding: 9px 12px;
    text-align: left;
    font-size: 13px;
    outline: none;
}}
QPushButton#NavButton:hover   {{ background: {t["hover"]}; color: {text}; }}
QPushButton#NavButton:checked {{
    /* 左侧 3px 指示条。纯底色的选中态在 20 项里很难一眼定位，
       加一道竖条就成了「位置 + 高亮」双编码。只画 border-left，
       右边不画，所以选中块看起来仍是一整条而不是被框住。 */
    background: {accent_soft};
    border-left: 3px solid {accent};
    color: {accent_fg};
    font-weight: 600;
    outline: none;
}}

/* 二级菜单的组标题（可折叠）。
   与 NavButton 分开而不是复用，理由有两条：
   1. 标题**不参与页面选中态**——它是「展开/收起」的开关，不是
      「切到某一页」。选中组内某页时高亮的是那一项，不是标题。
   2. 它需要有**底色**，才能在视觉上把「一组的 8 个子项」和外面
      的 19 个平铺项区分开。纯文字缩进在 204px 宽、13px 字号的
      侧边栏里几乎看不出来。 */
QPushButton#NavGroup {{
    background: {t["inset"]};
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    color: {text};
    padding: 8px 10px;
    text-align: left;
    font-size: 13px;
    font-weight: 700;
    outline: none;
}}
QPushButton#NavGroup:hover {{
    background: {t["hover"]};
    border-color: {accent_hi};
}}
/* **不能用 :not(:checked)。** Qt 的 QSS 子集不支持它，遇到解析不了的
   选择器，Qt 会**丢弃这条规则以及它之后的全部内容**。

   症状特别隐蔽：界面上「卡片看不见了、Primary 按钮变灰、输出区变白」，
   而 qss() 生成的文本完全正确、花括号也配平——因为坏的不是生成，
   是 Qt 的解析在这一行就停了。被它废掉的正是排在后面的
   QFrame#Card / QPushButton#Primary / QLineEdit /
   QPlainTextEdit#Console 四条规则。

   代价是「收起状态」没法单独上色：QSS 里无法区分 checked 与否，
   Qt 也不给这种伪类。改用 objectName 区分——见 NavGroupOff。 */
QPushButton#NavGroupOff {{
    background: {inset};
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    color: {text_dim};
    padding: 8px 10px;
    text-align: left;
    font-size: 13px;
    font-weight: 700;
    outline: none;
}}

/* 二级菜单里的页面项：左缩进 + 小一号字，视觉上从属于组标题。
   缩进用 padding 而不是文字前的空格——空格在 QSS 里没法改
   hover 的背景宽度，会出现「背景没盖到缩进部分」的错位。 */
QPushButton#NavButtonSub {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    color: {text_dim};
    padding: 7px 10px 7px 22px;
    text-align: left;
    font-size: 12px;
    outline: none;
}}
QPushButton#NavButtonSub:hover {{
    background: {t["hover"]};
    color: {text};
}}
QPushButton#NavButtonSub:checked {{
    background: {accent_soft};
    color: {accent_fg};
    font-weight: 600;
    outline: none;
}}

/* 小尺寸次要按钮（端口预设等）：比主按钮矮，比正文重 */
QPushButton#Chip {{
    background: {t["surface"]};
    border: 1px solid {border};
    border-radius: 6px;
    color: {text_dim};
    padding: 3px 11px;
    font-size: 12px;
    outline: none;
}}
QPushButton#Chip:hover {{
    background: {accent_soft};
    border-color: {accent_hi};
    color: {accent_fg};
    outline: none;
}}

/* ---------- 表单滚动区 ---------- */
/* 与 #NavScroll 同理：QScrollArea 的 viewport 不吃父级的透明规则，
   不显式声明就会出现一块与卡片区不同色的底。

   注意 #FormScroll 里现在装的是 Card 本身（不再隔一层 holder），
   所以这条只管滚动区和它的 viewport——卡片的白底由 QFrame#Card
   负责。 */
#FormScroll, #FormScroll > QWidget {{
    background: transparent;
    border: none;
}}
#FormScroll QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
#FormScroll QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 4px;
    min-height: 24px;
}}
#FormScroll QScrollBar::handle:vertical:hover {{
    background: {t["text_mute"]};
}}
#FormScroll QScrollBar::add-line, #FormScroll QScrollBar::sub-line {{
    height: 0; width: 0;
}}
#FormScroll QScrollBar::add-page, #FormScroll QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------- 卡片 ---------- */
QFrame#Card {{
    /* 上缘略亮、下缘略暗。这一层几乎察觉不到，但拿掉之后卡片就退化成
       一块平色——眼睛是靠这种微差读出「有厚度」的，纯平色在屏幕上
       会显得像贴纸。

       幅度必须小（#ffffff -> #fafcff 只差 3 级）。再深会变成「脏」。
       深色主题要用另一组值（那边「高光」是提亮而非纯白），所以走
       字典的 grad_top / grad_bot，不在这里写死。 */
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["grad_top"]},
        stop:1 {t["grad_bot"]}
    );
    /* border_soft 是 v1.0.0 的取值，照旧。
       我曾把它改成更深的 border，理由是「页面顶部有一条白条」——
       那个判断错了：白条真因是布局多了一层、Card 成了空壳
       （见 pages/base.py 的注释），跟边框色无关。改边框不但没用，
       还让卡片边缘比 v1.0.0 深了一点。这里恢复。 */
    border: 1px solid {t["border_soft"]};
    border-radius: {RADIUS}px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    /* 次要按钮也走一层极淡渐变。上面的「开始」有渐变、这里没有，
       两者并排时会一个像按键、一个像色块——这种不一致比缺细节
       更明显。幅度比主按钮小得多，不抢主按钮的视觉权重。 */
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["btn_top"]},
        stop:1 {t["btn_bot"]}
    );
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    color: {text};
    padding: 7px 15px;
    font-size: 13px;
    outline: none;
}}
QPushButton:hover   {{ background: {t["hover"]}; border-color: {accent_hi}; }}
QPushButton:pressed {{ background: {inset}; }}
QPushButton:disabled {{
    color: {t["text_mute"]};
    background: {surface};
    border-color: {t["border_soft"]};
    outline: none;
}}

QPushButton#Primary {{
    /* 上缘亮一档，模拟光从上方来。这一步之后按钮从「色块」变成
       「按键」——纯色的 #2563eb 看着像色块，#3b82f6 -> #2563eb
       的过渡才像能按下去。 */
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["accent_top"]},
        stop:1 {t["accent_bot"]}
    );
    border: 1px solid {accent};
    color: #ffffff;
    font-weight: 600;
    outline: none;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["accent_hi"]},
        stop:1 {t["accent_top"]}
    );
    border-color: {accent_hi};
}}

/* 风险等级按钮：颜色跟着操作的风险走。
   ----------------------------------------------------------
   **为什么低风险和高风险不能用同一个蓝色。**

   网络修复这一组有 8 页，用户是在一排长得几乎一样的页面之间点来
   点去。「重置 Winsock 目录」和「清空 DNS 缓存」的按钮形状、位置、
   文字全都一样，只有顶部的风险徽章不同——而徽章在视线之上，实际
   操作时眼睛盯的是按钮。肌肉记忆会让人顺手点下去。

   而这两个操作的后果差着量级：前者要重启电脑、浏览器和 VPN 客户端
   在重启前可能全不可用；后者只是让下次访问网站时多查一次 DNS。
   颜色是最快的风险提示——比读文字快，比找徽章快。

   深色主题下红橙要压暗，否则 #f87171 配白字对比度不够、看着发粉。 */
QPushButton#DangerBtn {{
    /* 上缘亮一档，和 Primary 同一套做法：色块变按键 */
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["err_top"]},
        stop:1 {t["err_bot"]}
    );
    border: 1px solid {t["err"]};
    color: #ffffff;
    font-weight: 600;
    outline: none;
}}
QPushButton#DangerBtn:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["err_top"]},
        stop:1 {t["err_top"]}
    );
    border-color: {t["err_top"]};
}}
QPushButton#DangerBtn:pressed {{
    background: {t["err_bot"]};
}}
QPushButton#DangerBtn:disabled {{
    background: {t["inset"]};
    border-color: {border};
    color: {t["text_mute"]};
}}

QPushButton#WarnBtn {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["warn_top"]},
        stop:1 {t["warn_bot"]}
    );
    border: 1px solid {t["warn"]};
    /* 浅色主题下 #c2740b 配白字对比度不够（3.9:1），用深墨色 */
    color: {t["text"]};
    font-weight: 600;
    outline: none;
}}
QPushButton#WarnBtn:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["warn_top"]},
        stop:1 {t["warn_top"]}
    );
    border-color: {t["warn_top"]};
}}
QPushButton#WarnBtn:pressed {{
    background: {t["warn_bot"]};
}}
QPushButton#WarnBtn:disabled {{
    background: {t["inset"]};
    border-color: {border};
    color: {t["text_mute"]};
}}
QPushButton#Primary:pressed {{ background: {t["accent_dim"]}; }}
QPushButton#Primary:disabled {{
    background: {t["border_soft"]};
    border-color: {t["border_soft"]};
    color: {t["text_mute"]};
    outline: none;
}}

QPushButton#WinBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    outline: none;
}}
QPushButton#WinBtn:hover   {{ background: {t["hover"]}; }}
QPushButton#WinBtn:pressed {{ background: {inset}; }}

/* ---------- 输入 ---------- */
QLineEdit, QSpinBox, QComboBox {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["inset_bot"]},
        stop:1 {t["inset_top"]}
    );
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    padding: 7px 11px;
    color: {text};
    selection-background-color: {accent};
    selection-color: #ffffff;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {accent};
    background: {card};
}}
QLineEdit::placeholder {{ color: {t["text_mute"]}; }}

/* QLineEdit 内部靠一个 QTextEdit 子控件显示文字，它的调色板不会
   自动跟随 QSS 的 color 声明——深色主题下会出现「浅色文字配深色
   背景」甚至看不清。必须显式指定。 */
QLineEdit QTextEdit {{
    background: transparent;
    color: {text};
    selection-background-color: {accent};
    selection-color: #ffffff;
}}
QSpinBox QTextEdit, QAbstractSpinBox QTextEdit {{
    background: transparent;
    color: {text};
    selection-background-color: {accent};
    selection-color: #ffffff;
}}

/* 标题栏里的版本号。
   灰色小字，紧跟在软件名后面。
   **对比度刻意压低一档**——它是辅助信息，不该和软件名抢注意力；
   但也不能太淡，否则在浅灰标题栏上要找半天。用 text_mute 而不是
   border 色。 */
QLabel#TitleVersion {{
    color: {t["text_mute"]};
    font-size: 12px;
    background: transparent;
    outline: none;
}}

/* ---------- 下拉框 ----------
   **之前只有本体样式，弹出列表和箭头完全没管**，于是 Windows 原生的
   灰色方块箭头就露在右边——那个带独立边框的小方块是「复古感」的
   主要来源，弹出列表也是系统默认的白底黑框，深色主题下更是刺眼。

   箭头用运行时生成的 PNG 而不是 QSS 的 border 三角：Windows 风格下
   border 三角会渲染成实心方块（和 QSpinBox 的箭头同一个坑），
   而 `image:` 对 SVG 不可靠。PNG 走 Qt 原生图像加载，任何平台都画得
   出来。见 ui/icons.py 的 chevron_png。

   弹出列表要单独配：QComboBox 的下拉列表是一个**顶层窗口**，不吃
   控件自身的 QSS 继承，必须用 QComboBox QAbstractItemView 选中。 */
QComboBox {{
    /* 右侧留出箭头的位置，文字不要压到箭头上 */
    padding-right: 30px;
}}
QComboBox::drop-down {{
    /* 去掉系统那个带边框的方块。subcontrol-origin / position 必须
       显式给，否则在部分平台上箭头会跑到框外或压住边框。 */
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    border-left: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({chevron_light});
    /* 18px 不是随手取的：控件高 34px，24px 的箭头占了七成，
       视觉上比控件本身还抢眼。18px 大约是内高的一半，和
       「开始」按钮里文字的大小感相当。 */
    width: 18px;
    height: 18px;
    margin-right: 5px;
}}
QComboBox::down-arrow:disabled {{
    image: url({chevron_light_dis});
}}

/* 下拉弹出的列表。它是**顶层窗口**，不吃控件自身的 QSS 继承，
   必须用 QComboBox QAbstractItemView 选中才能配上。

   这里只管「框」和「行高」；**每一项的底色和文字由
   ui/widgets.py 的 ComboItemDelegate 自绘**——见那里的注释，
   简单说就是 QSS 无论怎么写都改不动 item 高亮块的矩形。
   所以这里刻意**不写** ::item:hover / ::item:selected：
   写了也不生效，留着只会让下一个人以为它有用。 */
QComboBox QAbstractItemView {{
    background: {card};
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    /* 框的内缩。delegate 画的高亮块还会再自己内缩 5px，
       两层加起来左右共 10px，视觉上就是一条舒服的边距。 */
    padding: 5px;
    outline: none;
    /* 关掉系统的高亮配色——delegate 自己画，别让 Qt 再叠一层 */
    selection-background-color: transparent;
    /* 不出横向滚动条：开着的话 delegate 会把 item 拉满整个宽度 */
    horizontal-scrollbar-policy: ScrollBarAlwaysOff;
    color: {text};
}}
QComboBox QAbstractItemView::item {{
    /* 行高给足 = 每项之间有呼吸，不会糊成一张表。
       **这一条是唯一还靠 QSS 生效的 item 规则**，delegate 读了
       sizeHint 来决定每行多高。 */
    min-height: 30px;
    color: {text};
}}
/* 滚动条：下拉列表通常不长，但源多的时候会出 */
QComboBox QAbstractItemView::item:disabled {{
    color: {t["text_mute"]};
}}

/* SpinBox：隐藏系统箭头，改用滚轮 + 直接输入。
   QSS 的 border 三角箭头在 Windows 风格下渲染不稳定（会变成
   实心方块），与其做个半成品不如去掉——次数这类参数用滚轮
   调整本来就比点箭头顺手。 */
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px;
    border: none;
    background: transparent;
}}

/* ---------- 输出面板 ---------- */
QPlainTextEdit#Console, QPlainTextEdit#Console > QWidget {{
    background: {t["console"]};
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    color: {t["console_fg"]};
    padding: 10px 12px;
    selection-background-color: {accent};
}}

/* Console 的 viewport 必须显式配色。
   widgets.Console 给 viewport 设了 setAutoFillBackground(True)，
   但**不设背景色**时 Qt 会填 palette().window()（系统浅灰），
   把 QSS 写在控件本体上的 #111827 整个盖住——于是「深色终端」
   变成白底。AutoFillBackground 是逐控件开关，关掉它不如把颜色
   显式写对，这样跟随主题也可靠。 */
QPlainTextEdit#Console QWidget#ConsoleViewport {{
    background: {t["console"]};
}}
QPlainTextEdit#Console > QWidget {{
    background: {t["console"]};
}}

/* ---------- 复选框 ----------
   同样原本没有 QSS，走 Qt 原生外观：底色恒为系统浅灰 #efefef
   （不跟随本程序的主题字典），而文字色被全局 QLabel 规则设成
   主题色——深色主题下就是「#efefef 浅灰底 + #e6edf7 浅色字」，
   实测对比度只有 3，复选框整条文字等于隐形。

   **勾选态是描边蓝勾 + 白底，不是实心蓝块。** 取舍在这里：
       实心色块看着像「一个被按下的按钮」，而打勾传达的是
   「这一项被勾上了」——语义不同，误触时也更容易分辨当前状态。
   纯 QSS 画不出勾号（border 拼的三角在 Windows 上会糊成实心
   方块；indicator 的 image: 对 SVG 支持也不可靠——QtSvg 装着、
   文件也在，勾号就是不画），所以用 image: url() 挂一张运行时
   按主题色生成的 PNG。

   勾号素材由 ui/icons.py 用纯几何画出来（不引第三方图标库，
   无版权风险），写到临时目录供 QSS 的 url() 加载。 */
QCheckBox {{
    background: transparent;
    color: {text};
    spacing: 8px;
    padding: 2px 0;
    outline: none;
}}
QCheckBox:disabled {{ color: {t["text_mute"]}; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {t["text_mute"]};
    border-radius: 4px;
    background: {card};
    image: none;
}}
QCheckBox::indicator:hover {{ border-color: {accent_hi}; }}
QCheckBox::indicator:checked {{
    background: {card};
    border: 1px solid {accent};
    image: url("{_check}");
}}
QCheckBox:disabled::indicator {{
    border-color: {t["border"]};
    background: {surface};
    image: none;
}}

/* ---------- 对话框 ----------
   QMessageBox 原本完全没有 QSS，走的是 Qt 原生外观：底色恒为浅灰
   #efefef（由系统主题决定，不跟随本程序的主题字典）。而全局
   QLabel 规则会把文字设成主题色——深色主题下就成了「浅灰底 +
   #e6edf7 浅色字」，实测对比度只有 3，等于看不见。

   这里补齐对话框自身的底色与文字色，让它跟随主题。 */
QMessageBox {{
    background: {card};
    border: 1px solid {border};
}}
QMessageBox QLabel {{
    background: transparent;
    color: {text};
}}
/* 对话框里的 QLabel 可能是 RichText（setTextFormat 默认 Auto），
   显式给到前景色，避免 html 里没写 color 时继承到错误值 */
QMessageBox QLabel:disabled {{ color: {t["text_mute"]}; }}
/* 图标（警告/错误/info）保持 Qt 默认绘制的彩色，不设 background */
QMessageBox QLabel#qt_msgbox_ex_label_icon {{
    background: transparent;
}}
/* 按钮行 */
QMessageBox QDialogButtonBox {{
    background: transparent;
}}
QMessageBox QPushButton {{
    /* 次要按钮也走一层极淡渐变。上面的「开始」有渐变、这里没有，
       两者并排时会一个像按键、一个像色块——这种不一致比缺细节
       更明显。幅度比主按钮小得多，不抢主按钮的视觉权重。 */
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["btn_top"]},
        stop:1 {t["btn_bot"]}
    );
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    color: {text};
    padding: 6px 16px;
    min-width: 84px;
    outline: none;
}}
QMessageBox QPushButton:hover {{
    background: {t["hover"]};
    border-color: {accent_hi};
}}
/* 「确认执行」这类危险动作要一眼能认出来 */
QMessageBox QPushButton[accept="true"] {{
    background: {t["err"]};
    border: 1px solid {t["err"]};
    color: #ffffff;
    font-weight: 600;
}}
QMessageBox QPushButton[accept="true"]:hover {{
    background: {t["err"]};
    border-color: {t["err"]};
}}

/* ---------- 滚动区 viewport ----------
   QScrollArea 内部有一个强制存在的 viewport 控件，它的 objectName
   固定是 Qt 内部名 `qt_scrollarea_viewport`，而且它**不是
   QScrollArea 的直接子控件**（在内部的 container 里），所以
   `#NavScroll > QWidget` 这类选择器匹配不到它。

   **必须限定在 #NavScroll / #FormScroll 之内，不能全局设。**
   `QPlainTextEdit` 内部也含 QScrollArea，它的 viewport 就是输出区
   真正显示文字的地方。全局给 qt_scrollarea_viewport 设透明，会把
   Console 的底色也一起撤掉，
   而 viewport 开了 AutoFillBackground 却填不上色——最后透出系统
   默认的浅灰，「深色输出区」变成白底。

   顺带说明：这里曾经被当成「页面顶部白条」的根因。其实不是——
   白条真因是布局多了一层、Card 成了空壳（见 pages/base.py），
   viewport 那时只是被牵连。这里保留限定版本，因为它确实管用，
   而全局版本会误伤文本控件。 */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea#NavScroll > QWidget,
QScrollArea#NavScroll > QWidget > QWidget,
QScrollArea#NavScroll QWidget#qt_scrollarea_viewport,
QScrollArea#FormScroll > QWidget,
QScrollArea#FormScroll > QWidget > QWidget,
QScrollArea#FormScroll QWidget#qt_scrollarea_viewport,
QScrollArea#ToolScroll > QWidget,
QScrollArea#ToolScroll > QWidget > QWidget,
QScrollArea#ToolScroll QWidget#qt_scrollarea_viewport,
QWidget#ToolScrollBody {{
    background: transparent;
    border: none;
}}
/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {t["text_mute"]}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {border};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t["text_mute"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- 文字层级 ---------- */
/* 页面标题。
   letter-spacing 给 -0.2px：中文方块字在 21px 下按默认间距排会
   显得松散（每个字之间能塞进一根头发丝），收紧一点点就紧凑。
   幅度必须极小——-0.5px 以上中文会挤成一团，笔画多的字
   （如「网络」「诊断」）最先糊。 */
QLabel#H1 {{
    font-size: 21px;
    font-weight: 700;
    color: {text};
    letter-spacing: -0.2px;
}}
QLabel#H2      {{ font-size: 15px; font-weight: 600; color: {text}; }}
QLabel#Dim     {{ color: {text_dim}; font-size: 12px; }}
/* Mono / Warn 用于 IP 工具结果区里标记值和警告。这两个 objectName
   若缺了 QSS 规则，Qt 会回落到原生 QPalette.Text——那个值由 Windows
   系统主题决定，于是同一份代码在 Win10 深色、Win11 浅色下会出现
   「文字与卡片同色」而看不见。凡是 setObjectName 的控件都必须在此
   有对应规则。 */
QLabel#Mono    {{ color: {text}; background: transparent; font-family: "Cascadia Mono", Consolas, monospace; }}
QLabel#Warn    {{ color: {t["err"]}; background: transparent; }}

/* 多行输入框。**objectName 是跨控件类型不复用的**——上面那条
   QLabel#Mono 只作用于 QLabel，给 QPlainTextEdit 设同名 objectName
   不会命中它，于是走原生 QSS 的默认外观：浅色主题下是白底浅灰框，
   落在白色卡片上几乎看不见。必须按类型各写一条。 */
QPlainTextEdit#Mono {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["inset_bot"]},
        stop:1 {t["inset_top"]}
    );
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    color: {text};
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 12px;
    padding: 8px 10px;
    selection-background-color: {accent};
    selection-color: {t["text_invert"]};
}}
/* QPlainTextEdit 的 viewport 是独立子控件，QSS 的 background 只作用在
   控件本体上——不给 viewport 一起设色，就会「深色框里一块白底」。 */
QPlainTextEdit#Mono > QWidget {{ background: {inset}; }}
/* IP 工具结果区里 _kv_grid / _new_box 造的是裸 QWidget，QSS 按类型
   匹配不到，只能靠 objectName。它们同样会回落到原生 Base 色
   （Windows 主题给的浅灰），在深色主题下把浅色文字衬成浅灰。 */
QWidget#ResultGrid, QWidget#ResultBox {{ background: transparent; }}
QLabel#StatVal {{ font-size: 18px; font-weight: 700; color: {text}; }}
QLabel#StatKey {{ color: {t["text_mute"]}; font-size: 11px; }}

/* **不要给 QScrollArea 刷底色。**
   QPlainTextEdit / QTextEdit / QListWidget **内部都含一个
   QScrollArea**，所以给 QScrollArea 加 background 通配会一路盖到
   输出区上——Console 的深色底（#111827）被刷成 surface（#f8fafc），
   整个页面的卡片、按钮底色也跟着被压平。

   这条规则当初是为了「IP 工具的子页在深色主题下透出系统灰」加的，
   结果误伤了全程序：卡片看不出边界、Primary 按钮变灰、输出区变白。
   现在只保留下面那条显式设透明 viewport 的规则——深色主题下的
   透出问题由各控件自己的 background 解决，不该由滚动容器兜底。 */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QScrollBar {{ background: transparent; }}
/* ---------- 标签页 ----------
   QTabBar / QTabWidget::pane 此前**完全没有 QSS 规则**。Qt 对它们
   用的是系统调色板，不受 QSS 的 color 影响，于是深色主题下
   IP 工具页和 WiFi 页的标签条是纯白（实测 #ffffff），两块刺眼
   的白板夹在深色界面中间。
   不用 objectName 选择器：这两个是原生类名，全局规则就够，
   而且省得每个调用点都要记得设 objectName。 */
QTabWidget::pane {{
    background: {card};
    border: 1px solid {border};
    border-radius: {RADIUS_SM}px;
    top: -1px;
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {text_dim};
    border: 1px solid transparent;
    border-top-left-radius: {RADIUS_SM}px;
    border-top-right-radius: {RADIUS_SM}px;
    padding: 6px 16px;
    margin-right: 2px;
}}
QTabBar::tab:hover {{ color: {text}; background: {inset}; }}
QTabBar::tab:selected {{
    color: {accent};
    background: {card};
    border-color: {border};
    border-bottom-color: {card};
    font-weight: 600;
}}
QTabBar::focus {{ outline: none; }}

/* 分割线用 border_soft（比 border 浅一档）。这个取法是对的：
   分割线的作用是「暗示有分组」，不是「画出边界」——用 border
   那档会显得像表格框线，在侧边栏这种窄长区域尤其硬。 */
QFrame#Divider {{
    background: {t["border_soft"]};
    border: none;
    max-height: 1px;
}}

QToolTip {{
    background: {inset};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 5px 8px;
}}
"""


def apply(app, name: str) -> dict[str, Any]:
    """把主题应用到 QApplication，返回该主题的字典。"""
    t = get(name)
    app.setStyleSheet(qss(t))
    return t
