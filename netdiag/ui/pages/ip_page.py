"""IP 工具页：子网计算器 / 地址转换器 / 范围扩展器。

三个工具放一页而不是三页，因为它们操作的是同一个心智对象
（一个 IPv4 地址或一段范围），排查时常常连续用：先算子网，再把
某个地址转成十进制看看，再算一整段的 CIDR。

与「ARP 列表」不同，这里没有任何系统调用——全是纯位运算，放在
ipmath 里。UI 侧同步算完就显示，不走 Dispatcher 后台线程：
那点计算量走线程反而更慢，还要处理跨线程更新 UI 的问题。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QLineEdit, QScrollArea, QTabWidget, QVBoxLayout,
    QWidget,
)

from ...core import ipmath
from .base import Page
from .. import theme as T


def _kv_grid(pairs: list[tuple[str, str]]) -> QWidget:
    """两列表格：左标签右值。值用等宽字体，方便对齐看数字。"""
    w = QWidget()
    w.setObjectName("ResultGrid")
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(T.SPACE_LG)
    g.setVerticalSpacing(7)
    for row, (k, v) in enumerate(pairs):
        key = QLabel(k)
        key.setObjectName("Dim")
        val = QLabel(v)
        val.setObjectName("Mono")
        val.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setWordWrap(True)
        g.addWidget(key, row, 0)
        g.addWidget(val, row, 1)
    g.setColumnStretch(1, 1)
    return w


def _reset(holder: QWidget, msg: str = "") -> None:
    """清空结果容器，可选显示一行提示。

    **必须销毁旧子控件**而不是只 setText('')：结果区是反复重建的
    （每敲一个字符就换一次内容），只清文本会让上一轮的 QLabel
    一直堆在布局里，输入框越敲越慢、界面越滚越长。
    """
    box = _new_box(holder)
    if not msg:
        return
    lab = QLabel(msg)
    lab.setObjectName("Warn")
    lab.setWordWrap(True)
    box.addWidget(lab)


def _new_box(holder: QWidget) -> QVBoxLayout:
    """拿一个干净的纵向布局来装结果。

    **这里踩了两个 Qt 的坑**，都会让「核心算对了、界面空白」：

    1. `QVBoxLayout(holder)` —— holder 已有布局时，Qt 只在 stderr 打
       一句 QWARN "already has a layout"，返回的 layout 并没有被安装，
       之后 addWidget 全加到游离对象上。
    2. `holder.setLayout(None)` 想先卸载旧布局 —— Qt 不支持，
       setLayout 的参数类型是 QLayout，传 None 不会清空槽位；接着
       setLayout(新) 同样被拒绝，widget 继续用旧布局。

    **正确做法：复用**已有布局，只把里面的条目取干净。
    """
    box = holder.layout()
    if box is None:
        box = QVBoxLayout(holder)
    else:
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w is not None:
                # **只 setParent(None)**，不 deleteLater()。
                # deleteLater **是延迟删除**，要等事件循环转一圈才真正
                # 销毁；如果 textChanged 在同一轮里连着触发多次
                # （用户改完起始地址马上改终端地址，或测试批量
                # setText），旧控件还没销毁就被下一轮重新 parent
                # 回去，结果区会出现重影或干脆空白。
                # setParent(None) 立即脱离控件树，剩下的交给
                # Python 引用计数回收。
                w.setParent(None)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(T.SPACE_XS)
    return box


def _fill(holder: QWidget, pairs: list[tuple[str, str]]) -> None:
    """把键值对填进结果容器（先清空再填）。"""
    box = _new_box(holder)
    box.addWidget(_kv_grid(pairs))
    box.addStretch(1)


class IpToolPage(Page):
    NAME = "ip"
    TITLE = "IP 工具"
    SUBTITLE = "IPv4 子网计算、地址转换、范围转 CIDR——纯计算，不发任何请求"

    # 三个子工具都不走后台任务，也没有统计块
    HAS_STATS = False
    # 结果就显示在表单区里（子网 22 行、转换 18 行、范围可很长），
    # 再留一个空输出区只会把结果挤成两行，而且那一大块深色看着
    # 像出错了。
    HAS_CONSOLE = False

    def _build_form(self, lay: QVBoxLayout) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._wrap(self._subnet_tab()), "子网计算器")
        self.tabs.addTab(self._wrap(self._convert_tab()), "地址转换器")
        self.tabs.addTab(self._wrap(self._range_tab()), "范围扩展器")
        lay.addWidget(self.tabs, 1)
        # 打开页面就把默认值算出来。必须显式调：默认值是在
        # 构造时通过 setText 填的，而 setText 填相同值时 Qt 不发
        # textChanged，光连信号的话结果区会是空的——用户得手动
        # 改一下输入框才看到结果。
        self._do_subnet(self.sub_in.text())
        self._do_convert(self.conv_in.text())
        self._do_range()

    def _build_actions(self, lay: QVBoxLayout) -> None:
        """三个子工具都是输入即算，没有「开始」可点。"""
        from ..widgets import GhostButton
        row = QVBoxLayout()
        self.btn_clear = GhostButton("清空全部")
        self.btn_clear.clicked.connect(self.clear)
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        lay.addLayout(row)

    @staticmethod
    def _wrap(inner: QWidget) -> QScrollArea:
        """每个子工具内容可能较长，套一层纵向滚动。

        **刻意关掉横向滚动条**并让内部 widget 随宽度伸缩——子网掩码的
        二进制串有 35 个字符，窗口窄的时候换行比出现横向滚动条好。

        objectName 必须是 `ToolScroll`：theme 里让滚动区 viewport 透明
        的那条规则按前缀限定（`#NavScroll` / `#FormScroll`），**没给
        objectName 的滚动区不在范围内**，Qt 就给它填系统灰
        `#f0f0f0`。深色主题下那是一块刺眼的白底，浅色主题下也明显
        比卡片深一档。用户报的「IP 工具输出区显示异常，跟背景色有关」
        就是这个——它不是输出区，是子页的滚动容器。

        用 Qt 内部名 `qt_scrollarea_viewport` 全局兜底是错的：
        QPlainTextEdit 内部也含 QScrollArea，会把 Console 的深色底
        一起撤掉（那个 bug 已修，别退回去）。
        """
        sc = QScrollArea()
        sc.setObjectName("ToolScroll")
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(inner)
        inner.setObjectName("ToolScrollBody")
        return sc

    # ---------------------------------------------------------------- #
    #  1. 子网计算器
    # ---------------------------------------------------------------- #
    def _subnet_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, T.SPACE_SM, 0)
        v.setSpacing(T.SPACE_MD)

        row = QVBoxLayout()
        row.setSpacing(T.SPACE_XS)
        self.sub_in = QLineEdit("192.168.0.0/24")
        self.sub_in.setPlaceholderText("192.168.0.0/24，或只填 192.168.1.5")
        self.sub_in.textChanged.connect(self._do_subnet)
        row.addWidget(QLabel("网段"))
        row.addWidget(self.sub_in)
        v.addLayout(row)

        v.addWidget(self._note(
            "可以带主机位（填 192.168.1.5/24 也可以），会按掩码归到"
            " 192.168.1.0/24。只填 IP 不带掩码时按 /32 处理。"))
        v.addSpacing(T.SPACE_XS)

        self.sub_out = QWidget()
        self.sub_out.setObjectName("ResultBox")
        v.addWidget(self.sub_out)
        v.addStretch(1)
        return w

    def _do_subnet(self, text: str) -> None:
        t = text.strip()
        if not t:
            _reset(self.sub_out)
            return
        try:
            d = ipmath.subnet_info(t)
        except ValueError as e:
            _reset(self.sub_out, f"⚠ {e}")
            return
        except Exception as e:                            # noqa: BLE001
            _reset(self.sub_out, f"⚠ 无法解析：{e}")
            return

        pairs = [
            ("网段", d["cidr"]),
            ("网络地址", d["network"]),
            ("网络掩码", d["netmask"]),
            ("二进制掩码", d["netmask_bin"]),
            ("CIDR 编号", d["prefix"]),
            ("通配符掩码", d["wildcard"]),
            ("网络规模", f"{d['total']} 个地址（可用 {d['usable']}）"),
            ("第一可用", d["first"]),
            ("最后可用", d["last"]),
            ("广播地址", d["broadcast"]),
            ("IP 类别", d["class"]),
        ]
        _fill(self.sub_out, pairs)

    # ---------------------------------------------------------------- #
    #  2. 地址转换器
    # ---------------------------------------------------------------- #
    def _convert_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, T.SPACE_SM, 0)
        v.setSpacing(T.SPACE_MD)

        row = QVBoxLayout()
        row.setSpacing(T.SPACE_XS)
        self.conv_in = QLineEdit("192.168.1.1")
        self.conv_in.setPlaceholderText("输入一个 IPv4 地址")
        self.conv_in.textChanged.connect(self._do_convert)
        row.addWidget(QLabel("IPv4 地址"))
        row.addWidget(self.conv_in)
        v.addLayout(row)

        self.conv_out = QWidget()
        self.conv_out.setObjectName("ResultBox")
        v.addWidget(self.conv_out)
        v.addStretch(1)
        return w

    def _do_convert(self, text: str) -> None:
        t = text.strip()
        if not t:
            _reset(self.conv_out)
            return
        try:
            d = ipmath.convert_ipv4(t)
        except Exception as e:                            # noqa: BLE001
            _reset(self.conv_out, f"⚠ 不是合法的 IPv4 地址：{e}")
            return
        pairs = [
            ("点分十进制", d["ip"]),
            ("十进制", d["decimal"]),
            ("十六进制", d["hex"]),
            ("十六进制(整型)", d["hex_plain"]),
            ("二进制", d["binary"]),
            ("二进制(点分)", d["binary_dotted"]),
            ("IPv6 映射", d["ipv6_mapped"]),
            ("IPv6 简短", d["ipv6_short"]),
            ("IPv6 完整", d["ipv6"]),
        ]
        _fill(self.conv_out, pairs)

    # ---------------------------------------------------------------- #
    #  3. 范围扩展器
    # ---------------------------------------------------------------- #
    def _range_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, T.SPACE_SM, 0)
        v.setSpacing(T.SPACE_MD)

        g = QGridLayout()
        g.setHorizontalSpacing(T.SPACE_MD)
        g.setVerticalSpacing(T.SPACE_XS)
        self.rng_a = QLineEdit("192.168.0.0")
        self.rng_b = QLineEdit("192.168.7.255")
        self.rng_a.textChanged.connect(self._do_range)
        self.rng_b.textChanged.connect(self._do_range)
        g.addWidget(QLabel("起始地址"), 0, 0)
        g.addWidget(self.rng_a, 0, 1)
        g.addWidget(QLabel("终端地址"), 1, 0)
        g.addWidget(self.rng_b, 1, 1)
        g.setColumnStretch(1, 1)
        v.addLayout(g)

        v.addWidget(self._note(
            "会算出覆盖这段范围所需的最少 CIDR 块。"
            "如果能压成一个，直接给出；压不成就列出所有块。"))
        v.addSpacing(T.SPACE_XS)

        self.rng_out = QWidget()
        self.rng_out.setObjectName("ResultBox")
        v.addWidget(self.rng_out)
        v.addStretch(1)
        return w

    def _do_range(self, *_a) -> None:
        a, b = self.rng_a.text().strip(), self.rng_b.text().strip()
        if not a or not b:
            _reset(self.rng_out)
            return
        try:
            d = ipmath.range_to_cidrs(a, b)
        except ValueError as e:
            _reset(self.rng_out, f"⚠ {e}")
            return
        except Exception as e:                            # noqa: BLE001
            _reset(self.rng_out, f"⚠ 无法解析：{e}")
            return

        blocks = "\n".join(d["blocks"][:40])
        if len(d["blocks"]) > 40:
            blocks += f"\n… 另有 {len(d['blocks']) - 40} 块"
        pairs = [
            ("范围内地址", f"{d['count']:,} 个"),
            ("需要块数", f"{d['block_count']} 个 CIDR"),
            ("汇总", d["summary"]),
            ("块列表", blocks),
        ]
        _fill(self.rng_out, pairs)

    # ---------------------------------------------------------------- #
    @staticmethod
    def _note(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Dim")
        lab.setWordWrap(True)
        return lab

    def task(self):
        """本页不涉及探测。"""
        return (lambda post: None, "")

    def clear(self) -> None:
        for w in (self.sub_in, self.conv_in, self.rng_a, self.rng_b):
            w.clear()
        for w in (self.sub_out, self.conv_out, self.rng_out):
            _reset(w)
