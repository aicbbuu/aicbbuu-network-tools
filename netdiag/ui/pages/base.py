"""
页面基类。

每个页面的骨架是固定的：

    标题 + 副标题
    ┌ 操作卡片：表单行 + 按钮 ────────────┐
    ┌ 统计块（可选）────────────────────┐
    ┌ 输出面板（Console）───────────────┐

页面只需要实现 ``_build_form()``（表单控件）和 ``_stat_keys()``
（统计块），其余交给基类。

操作区带自己的滚动条、输出区有高度下限——这两条都是为了让
「网络修复」这类表单很长的页面不会把输出区挤没。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QComboBox, QLabel, QSizePolicy, QVBoxLayout,
    QWidget,
)

from ...core.runner import KIND_DONE, KIND_ERROR, Dispatcher
from .. import theme as T
from ..widgets import (
    Card, Console, FieldRow, GhostButton, PrimaryButton, StatTile, state_dot,
)


class Page(QWidget):
    """所有功能页的基类。"""

    #: 页面标识，必须与 netdiag.ui.pages 中的注册名一致
    NAME = ""
    #: 侧边栏显示名
    TITLE = ""
    #: 副标题
    SUBTITLE = ""
    #: 是否需要统计块
    HAS_STATS = False
    # 空状态提示语（显示在输出区里，任何内容写入后自动消失）。
    # 页面可以覆盖成更贴切的一句；None 表示用通用文案。
    HINT: str | None = None
    #: 是否需要输出区。纯展示页（如「关于」）设 False——留一个空的
    #: 深色框在页面下方既没内容也没意义，看起来像出错了。
    HAS_CONSOLE = True

    def __init__(self, dispatcher: Dispatcher,
                 theme: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("PageArea")
        self.d = dispatcher
        self.t = theme or T.LIGHT
        self._running = False
        #: 本次运行是否已出错。KIND_DONE 不能覆盖它。
        self._failed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE_XL, T.SPACE_XL, T.SPACE_XL, T.SPACE_XL)
        root.setSpacing(T.SPACE_LG)

        # ---- 标题区 ----
        head = QVBoxLayout()
        head.setSpacing(3)
        t1 = QLabel(self.TITLE)
        t1.setObjectName("H1")
        t2 = QLabel(self.SUBTITLE)
        t2.setObjectName("Dim")
        t2.setWordWrap(True)
        head.addWidget(t1)
        head.addWidget(t2)
        root.addLayout(head)

        # ---- 操作区：内容多时可滚动 ----
        #
        # 这里**只给操作区套滚动条，不给整页套**。原因：
        #
        # · 整页套滚动 → QScrollArea 按内部 widget 的 sizeHint 定高，
        #   而 QPlainTextEdit 的 sizeHint 只有几行，撑不开，症状是
        #   「输出区被压扁、整页错乱下移」。
        # · 只套操作区 → 输出区仍然用 stretch=1 独占剩余高度，
        #   永远是页面里最大的一块；操作区内容超出时自己滚。
        #
        # 「网络修复」页有 8 张操作卡片，装不下时如果没有滚动条，
        # Qt 会压缩卡片把输出区挤到只剩几十像素——用户看不到操作
        # 过程和结果。这个滚动条就是为它准备的。
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(T.SPACE_MD)
        root.addLayout(self._body)

        # ---- 操作卡片 ----
        #
        # 刻意**不套 QScrollArea**。这个页面要保持现有的布局，改动时
        # 只调细节，不要重构整体结构。
        #
        # 之前加滚动区是想让长表单自己滚，但代价是：滚动区一固定
        # 高度，Qt 就会从旁边的可压缩项（统计块）身上扣，扣完向上
        # 溢出盖住卡片；为了再救回来，又得写 _fit_form 算比例、
        # 给 tile 设固定高度——越修越复杂，最后界面还不如原来。
        #
        # 现在：标题、操作卡、统计块固定在上方，输出区独占剩余
        # 空间（stretch=1），长内容由输出区自己的滚动条处理。
        # 各页操作区都控制在一屏内，装不下的话是那一页自己的问题。
        self.card = Card(parent=self, theme=self.t)
        self._form_layout = QVBoxLayout()
        self._form_layout.setSpacing(T.SPACE_MD)
        self.card.add_layout(self._form_layout)
        self._body.addWidget(self.card)

        # ---- 统计块 ----
        self.tiles: list[StatTile] = []
        if self.HAS_STATS:
            tile_row = QHBoxLayout()
            tile_row.setSpacing(T.SPACE_SM)
            for key in self._stat_keys():
                tile = StatTile(key, self.t)
                self.tiles.append(tile)
                tile_row.addWidget(tile, 1)
            self._body.addLayout(tile_row)

        # ---- 输出面板：占满剩余空间 ----
        #
        # **给一个下限。** stretch=1 意味着「有多余空间就都给你」，
        # 但页面上方（卡片）一旦变高，输出区会被压到几十像素——
        # 用户看不到执行过程和结果，界面上还「看不出哪里出错」。
        # 150px 约 7 行等宽字，够看清一次操作的结论。
        #
        # 这不是改 v1.0.0 的布局：结构、顺序、stretch 全都没动，
        # 只是给输出区一条「不能再矮」的底线。
        if self.HAS_CONSOLE:
            self.console = Console(parent=self, theme=self.t,
                                   hint=self.HINT)
            self.console.setMinimumHeight(150)
            self._body.addWidget(self.console, 1)
        else:
            # 仍然建一个但不显示：基类的 start/clear/on_event 都会
            # 访问 self.console，直接置 None 会到处 AttributeError。
            # 用 setVisible(False) 而不是 hide()：后者等价，但前者
            # 在控件尚未 show 时也可靠。
            self.console = Console(parent=self, theme=self.t)
            self.console.setVisible(False)

        self._build_form(self._form_layout)
        self._build_actions(self._form_layout)

    # ---------------------------------------------------------------- #
    #  子类实现
    # ---------------------------------------------------------------- #
    def _stat_keys(self) -> tuple[str, ...]:
        return ()

    def set_tiles(self, keys: tuple[str, ...]) -> None:
        """按**顺序**改写统计块标签并清空取值。

        比按名字查找可靠：core 改了标签名、或本页有两种形态（如 WiFi
        的两个子页）时，按名字匹配会静默失效——这已经发生过一次，
        表现是四个统计块永远是「—」而界面毫无异常。
        """
        for tile, key in zip(self.tiles, keys):
            tile.set_key(key)
            tile.set_value("—")

    def retheme(self, theme: dict[str, Any]) -> None:
        """换主题。不重建页面（理由见 App.toggle_theme 的注释）。

        QSS 是应用级的，`QApplication.setStyleSheet()` 一换，所有
        控件都会按新样式重绘——这部分不用管。

        要管的是自绘组件：它们把主题字典当画笔用（填充色、
        描边色），QSS 对它们完全无效。所以这里把新字典逐个递下去，
        漏掉任何一个，切主题后那个角落就会留着旧颜色。
        """
        self.t = theme
        # 下拉框的 delegate 是 Python 对象，QSS 管不到它的颜色。
        # polish_combo() 挂上来的 retint 就是干这个的——不调的话，
        # 深色主题下选中项会是一块浅蓝，在深底上非常刺眼。
        for cb in self.findChildren(QComboBox):
            retint = getattr(cb, "retint", None)
            if callable(retint):
                retint(theme)
        # 只认「有 retheme 方法」的控件。注意判断依据是方法本身，
        # 不是有没有 t 属性——Console 把主题存在 _t 上（_t 是它的
        # 内部缓存），早期版本写 hasattr(w, "t") 结果漏掉了它，
        # 切主题后输出区配色还是旧的。
        for w in self.findChildren(QWidget):
            fn = getattr(w, "retheme", None)
            if callable(fn):
                fn(theme)
        # 状态点与状态文字是内联着色，QSS 同样换不掉。
        # 状态文字必须**清空**内联样式而不是重设成新色——它只在
        # 「出错」时才该是红的，平时应该是普通灰。旧色残留的表现是
        # 浅色下点过一次开始（蓝色）后，切到深色主题仍然是浅蓝
        # #2563eb，而新主题的 accent 已经是 #3b82f6 了。
        if hasattr(self, "_status"):
            self._status.setStyleSheet("")
        self._set_running(self._running)

    def _build_form(self, lay: QVBoxLayout) -> None:
        """构造表单控件。"""

    def _build_actions(self, lay: QVBoxLayout) -> None:
        """构造按钮行（默认给「开始 / 停止 / 清空」）。"""
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_SM)
        self.btn_start = PrimaryButton("开始")
        self.btn_start.clicked.connect(self.start)
        self.btn_stop = GhostButton("停止")
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        self.btn_clear = GhostButton("清空")
        self.btn_clear.clicked.connect(self.clear)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        row.addWidget(self._status_area())
        row.addWidget(self.btn_clear)
        lay.addLayout(row)

    def _status_area(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self._dot = state_dot(self.t)
        self._status = QLabel("就绪")
        self._status.setObjectName("Dim")
        row.addWidget(self._dot)
        row.addWidget(self._status)
        return w

    def task(self):
        """返回 (fn, 提示语) —— fn 是 probes 模块里的探测函数。

        Dispatcher.submit(name, fn) 会在后台线程里调 fn(post)，
        post 是「把事件塞回 UI 队列」的回调。子类必须实现。
        """
        raise NotImplementedError

    # ---------------------------------------------------------------- #
    #  运行控制
    # ---------------------------------------------------------------- #
    def start(self) -> None:
        if self._running:
            return
        self._failed = False
        self.console.clear_all()
        try:
            fn, hint = self.task()
        except Exception as e:                     # noqa: BLE001
            self.console.err(f"参数有误：{e}")
            self._set_running(False)
            return
        self._set_running(True)
        self._set_status(hint, self.t["accent"])
        self.console.info(f"▸ {hint}")
        self.d.submit(self.NAME, fn)

    def stop(self) -> None:
        """请求停止。

        刻意**不**在这里 ``_set_running(False)``。cancel 只是设置
        一个 Event，真正的中止要等后台线程跑到下一轮 post 时抛
        Cancelled、由 finally 发来 KIND_DONE，UI 才在 on_event 里
        解禁。

        提前解禁会让「开始」按钮立刻可点，用户马上再点一次就
        submit 出第二个任务——而旧任务其实还在跑，两个任务的输出
        混在一起。实测下路由追踪 77 秒、端口扫描 48 秒都能撞上。
        """
        if not self._running:
            return
        self.d.cancel(self.NAME)
        self.console.warn("正在停止…")
        # 保持 _running=True，「开始」仍然禁用、「停止」变灰，
        # 提示用户取消已请求但还没生效。
        if hasattr(self, "btn_stop"):
            self.btn_stop.setEnabled(False)

    def clear(self) -> None:
        self.console.clear_all()
        for tile in self.tiles:
            tile.set_value("—")

    def _set_running(self, running: bool) -> None:
        self._running = running
        # 页级按钮是可选的：子页面可以覆写 _build_actions 换成自己的
        # 一组按钮（比如四个只读检查）。**不能无条件访问**——早期版本
        # 直接 self.btn_start.setEnabled()，结果在这些页面上一点就
        # AttributeError，用户看到的是「点 WiFi 就报错」。
        if hasattr(self, "btn_start"):
            self.btn_start.setEnabled(not running)
        if hasattr(self, "btn_stop"):
            self.btn_stop.setEnabled(running)
        if hasattr(self, "_dot"):
            self._dot.setStyleSheet(
                f"color: {self.t['accent'] if running else self.t['text_mute']};"
                " font-size: 9px; background: transparent;")

    def _set_status(self, text: str, color: str | None = None) -> None:
        """更新状态文字。

        这里的内联 setStyleSheet 是安全的：QLabel 是叶子节点，
        它下面没有子控件，不存在「打断子树 QSS 继承」的问题。
        真正要避免的是给 QScrollArea / QWidget 这类**有子控件的
        容器**设内联样式——那会让整棵子树的全局 QSS 失效。
        """
        if hasattr(self, "_status"):
            self._status.setText(text)
            # color 为 None 时也要**清掉**内联样式，否则上一次着色的
            # 颜色会留在这里——实测表现为「红字显示『完成』」。
            self._status.setStyleSheet(
                f"color: {color}; font-size: 12px;" if color else "")

# ③ 补一个 force_reset，供「出错 → 取消完成」时统一清理

    # ---------------------------------------------------------------- #
    #  事件
    # ---------------------------------------------------------------- #
    def on_show(self) -> None:
        """页面被切到前台时调用。"""

    def on_event(self, kind: str, payload) -> None:
        """收到后台线程事件。子类按需覆写。"""
        if kind == KIND_DONE:
            # 后台线程真的退出了才解禁——见 stop() 的注释。
            # payload 是 (name, was_cancelled)；兼容早期只发 name 的形式。
            self._set_running(False)
            was_cancelled = False
            if isinstance(payload, tuple) and len(payload) == 2:
                was_cancelled = bool(payload[1])
            # ERROR 和 DONE 通常在**同一轮** drain 里被一起取出来。
            # 无条件写"完成"会把"出错"立刻盖掉——用户被告知任务成功，
            # 实际早就崩了（traceback 还没显示的时候尤其误导）。
            if self._failed:
                return
            self._set_status("已停止" if was_cancelled else "完成")
        elif kind == KIND_ERROR:
            # ↓↓↓ payload 必须落到输出区 ↓↓↓
            # 若只改状态栏文字而不输出 payload，则 core 层的
            # 34 处 post(KIND_ERROR, ...)，每一条都是写给用户看的可操作
            # 建议——比如局域网页那句「网关不通 —— 问题在本地，不用联系
            # 运营商」，是整页存在的理由。用户一个字都看不到。
            # 这些提示全部依赖此处输出。
            self._failed = True
            self.console.err(str(payload))
            self._set_running(False)
            self._set_status("出错", self.t["err"])
