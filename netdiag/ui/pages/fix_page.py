"""网络修复页（每个操作一页，由侧边栏二级菜单进入）。

**这些操作会修改系统设置。** 设计上做了四道防护，每条操作在页面
上常驻显示后果说明（不是点下去才知道）、危险操作弹二次确认、
执行前自动备份配置、默认焦点落在「取消」。

UAC 弹窗必须由用户本人点击——程序调用 ShellExecuteW 的 runas
动词让 Windows 自己弹窗，既读不到也代替不了用户的选择。这是
Windows 的安全设计，不绕它。

**为什么一页一个操作**
------------------------
最初 8 个操作是 8 张卡片挤在一页里，结果：
输出区被压到 190px、卡片文字截断、窄窗口下第三行还被统计块盖住。
反复调整布局都救不回来——**一页塞 8 件事本身就是超载**。

改成一级菜单「网络修复」+ 二级菜单 8 项之后：
每页只有一件事，说明文字放得全（不用截断），风险等级、命令原文、
断网/重启提示都能常驻显示，输出区拿回整屏。

`FixPage` 用类属性 `FIX_KEY` 区分自己服务哪个操作，8 个子类各一个。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from ...core import netfix
from ...core.runner import KIND_LINE, KIND_STAT
from .base import Page
from .. import theme as T

RISK_CN = {"low": "低风险", "mid": "中风险", "high": "高风险"}


# 风险等级 -> 按钮样式名。
#   low  清 DNS 缓存、看配置这类日常操作 —— 蓝色即可
#   mid  续取/释放 IP：会断网但不是灾难 —— 橙色提醒
#   high 重置 Winsock / TCP-IP：要重启 —— 红色
#
# **键名必须和 netfix.Fix.risk 完全一致**（low/mid/high）。
# 写成 "medium" 会静默落到 Primary，看起来"分类没生效"。
RISK_BTN: dict[str, str] = {
    "low": "Primary",
    "mid": "WarnBtn",
    "high": "DangerBtn",
}


class FixPage(Page):
    """网络修复的基类。子类设好 FIX_KEY 即可。"""

    NAME = "fix"
    TITLE = "网络修复"
    SUBTITLE = "会修改系统设置。执行前请确认你已看懂后果"
    HAS_STATS = True
    HINT = "点「执行」运行这一条修复命令。命令原文、备份路径和结果都会记录在这里"

    #: 本页服务的操作 key（netfix.FIXES 里的 key）
    FIX_KEY = ""

    def _stat_keys(self) -> tuple[str, ...]:
        return ("当前操作", "执行结果", "执行耗时", "配置快照")

    # ---------------------------------------------------------------- #
    #  界面
    # ---------------------------------------------------------------- #
    def _build_form(self, lay: QVBoxLayout) -> None:
        fix = netfix.describe_fix(self.FIX_KEY)
        if fix is None:
            lay.addWidget(QLabel(f"未知的修复操作：{self.FIX_KEY}"))
            return
        # 存到实例上：_build_actions 由基类在 _build_form 之后调用，
        # 拿不到这里的局部变量，但它也要按风险给「执行」按钮上色。
        self._fix = fix

        # ---- 风险等级 + 权限 ----
        # 靠颜色 + 文字双通道表达。只靠颜色对色觉障碍用户无效，
        # 只靠文字在 20 个页面里扫不出来。
        top = QHBoxLayout()
        top.setSpacing(T.SPACE_SM)
        top.addWidget(QLabel("风险等级："))
        badge = QLabel(RISK_CN[fix.risk])
        badge.setObjectName("Dim" if fix.risk == "low" else "Warn")
        top.addWidget(badge)
        top.addStretch(1)
        perm = QLabel("当前权限：管理员（可直接执行）"
                      if netfix.is_privileged() else
                      "当前权限：普通用户（执行时会弹出 Windows 提权请求）")
        perm.setObjectName("Dim")
        top.addWidget(perm)
        lay.addLayout(top)

        # ---- 做什么 / 什么后果 ----
        # 一页一件事，说明文字**不截断**。上一版按卡片宽度压到一行、
        # 鼠标悬停才看得到全文，正是「点了才知道后果」的变体。
        for obj_name, head, body in (
            ("H2", "做什么", fix.summary),
            ("Dim", "会有什么影响", fix.effect),
        ):
            line = QLabel(f"{head}：{body}")
            line.setObjectName(obj_name)
            line.setWordWrap(True)
            line.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(line)

        if fix.offline:
            warn = QLabel("⚠ 执行期间会断网，网页、远程桌面、视频通话都会"
                          "中断。请先保存正在编辑的东西。")
            warn.setObjectName("Warn")
            warn.setWordWrap(True)
            lay.addWidget(warn)

        if fix.reboot:
            warn = QLabel("⚠ 必须重启电脑才生效。重启前网络可能异常。")
            warn.setObjectName("Warn")
            warn.setWordWrap(True)
            lay.addWidget(warn)

        if fix.note:
            note = QLabel(f"备注：{fix.note}")
            note.setObjectName("Dim")
            note.setWordWrap(True)
            lay.addWidget(note)

        # ---- 命令原文 ----
        # 常驻可见，不藏在确认弹窗里。用户有权在点之前看到程序到底
        # 要执行什么，这是「不是黑箱」的基本要求。
        cmd = QLabel(f"将要执行的命令：{fix.command_text}")
        cmd.setObjectName("Mono")
        cmd.setWordWrap(True)
        cmd.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(cmd)

        tip = QLabel(
            "这一条只读取并显示配置，不会修改任何设置，可以放心点。"
            if fix.readonly else
            "执行前会自动把当前配置备份到输出区，出问题可以照着还原。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        lay.addStretch(1)

        # ---- 执行按钮 ----
        # 只读操作用「查看」而不是「执行」：后者听起来像要改配置。
        btn = QPushButton("查看配置" if fix.readonly else "执行")
        # **按钮颜色跟着风险等级走。** 之前不管低风险还是高风险都用
        # 同一个蓝色 Primary，用户在 8 个页面之间来回点，肌肉记忆
        # 会让他顺手点下去——而「重置 Winsock 目录」点了是要重启的。
        # 颜色是最快的风险提示，比文字更早被看到。
        #
        # 风险等级本身在页面顶部已有红/黄徽章，这里是第二道防线：
        # 视线落在按钮上时，红色比蓝色更能拦住手。
        # 注意风险值是 "mid" 不是 "medium"——netfix.Fix.risk 里
        # 就这三个值：low / mid / high。写错大小写会让 mid 掉回
        # Primary 蓝色，看起来"分类没生效"。
        btn.setObjectName(RISK_BTN.get(fix.risk, "Primary"))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(34)
        btn.clicked.connect(self._confirm)
        lay.addWidget(btn)

    def _build_actions(self, lay: QVBoxLayout) -> None:
        # 页面自带一个醒目的执行按钮，基类那套「开始 / 停止 / 清空」
        # 里的「开始」重复了——但「停止」和「清空」仍然有用，保留。
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_SM)
        self.btn_start = QPushButton("执行")
        # 与上面那颗按钮同规则：风险决定颜色。
        # 兜底用 low：万一 _build_actions 先于 _build_form 跑（基类
        # 调换了顺序），至少按钮还在，只是颜色回到安全的蓝色。
        risk = getattr(self, "_fix", None)
        self.btn_start.setObjectName(
            RISK_BTN.get(getattr(risk, "risk", "low"), "Primary"))
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.start)
        self.btn_start.setVisible(False)      # 隐藏但保留，基类要用
        row.addWidget(self.btn_start)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("Ghost")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        row.addWidget(self._status_area())
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("Ghost")
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.clicked.connect(self.clear)
        row.addWidget(self.btn_clear)
        lay.addLayout(row)

    # ---------------------------------------------------------------- #
    #  执行
    # ---------------------------------------------------------------- #
    def _confirm(self) -> None:
        """执行前的二次确认。列出完整命令原文，默认焦点在取消。"""
        fix = netfix.describe_fix(self.FIX_KEY)
        if fix is None:
            return

        # 只读操作不必弹窗
        if fix.readonly:
            self._launch()
            return

        lines = [f"【{fix.title}】", "", f"做什么：{fix.summary}", "",
                 f"影响　：{fix.effect}"]
        if fix.offline:
            lines += ["", "⚠ 这一步会短暂断网。"]
        if fix.reboot:
            lines += ["", "⚠ 必须重启电脑才生效，重启前网络可能异常。"]
        if fix.note:
            lines += ["", f"备注　：{fix.note}"]
        lines += ["", "将要执行的命令：", fix.command_text,
                  "", "当前权限：管理员" if netfix.is_privileged()
                  else "当前权限：普通用户（将弹出 UAC 请求）"]

        box = QMessageBox(self)
        box.setWindowTitle("确认执行网络修复")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("\n".join(lines))
        box.setTextFormat(Qt.TextFormat.PlainText)
        yes = box.addButton("确认执行", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(
            QMessageBox.StandardButton.Cancel)   # 默认是取消
        box.exec()
        if box.clickedButton() is yes:
            self._launch()

    def _launch(self) -> None:
        """把选中的操作交给后台线程执行。"""
        if self._running:
            return
        self._pending = self.FIX_KEY
        self.start()

    def task(self):
        """把选中的操作转成后台任务。"""
        key = getattr(self, "_pending", None)
        if not key:
            raise ValueError("请先点击「执行」")
        self._pending = None
        return (lambda post: netfix.run_fix(post, key),
                f"正在执行：{netfix.describe_fix(key).title}…")

    def on_event(self, kind: str, payload) -> None:
        if kind == KIND_STAT:
            if isinstance(payload, dict):
                return
            for tile in self.tiles:
                if tile.key.text() == str(payload[0]):
                    tile.set_value(str(payload[1]))
                    return
            return
        if kind == KIND_LINE:
            line = str(payload)
            if line.startswith("  ✓"):
                self.console.write(line, "ok")
            elif line.startswith("  ✗") or "⚠" in line:
                self.console.write(line, "warn")
            elif line.startswith("═") or line.startswith("▸"):
                self.console.head(line)
            else:
                self.console.info(line)
            return
        super().on_event(kind, payload)


# ---------------------------------------------------------------- #
#  8 个操作各一页
# ---------------------------------------------------------------- #
#  顺序与 netfix.FIXES 一致：**风险从低到高**，二级菜单按同样顺序
#  排列，用户从上往下试就行。
FIX_PAGE_CLASSES = []
for _fix in netfix.FIXES:
    # netfix.FIXES 里出现一个 describe_fix 认不出的 key 时，宁可跳过
    # 也不要生成一个 TITLE=None 的类——它会在建侧边栏时炸在一个
    # 完全看不出原因的地方。
    if _fix is None:
        continue

    def _mk(f=_fix):
        return type(
            "FixPage_" + f.key,
            (FixPage,),
            {
                "FIX_KEY": f.key,
                "NAME": f.key,
                "TITLE": f.title,
                "SUBTITLE": f.summary,
                "__doc__": f"网络修复 · {f.title}（{RISK_CN[f.risk]}）",
            })
    FIX_PAGE_CLASSES.append(_mk())