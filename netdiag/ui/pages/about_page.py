"""关于页。

纯静态展示，不做探测，因此覆写 task() 返回空实现，页面级
「开始/停止」会被隐藏。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ... import APP_TITLE, APP_VERSION, REPO_URL
from .base import Page
from .. import theme as T


class AboutPage(Page):
    NAME = "about"
    TITLE = "关于"
    SUBTITLE = f"{APP_TITLE}  v{APP_VERSION}"
    # 纯静态展示，没有任何探测输出。留一个空的深色输出区在页面
    # 下方既没内容也没意义，看起来像出错了。
    HAS_CONSOLE = False

    def _build_form(self, lay: QVBoxLayout) -> None:
        # 内容较长，小窗口下必须能滚。这里刻意**只滚内容区**，
        # 不滚整个页面——整页塞进 QScrollArea 会导致输出区被压扁
        # （QPlainTextEdit 的 sizeHint 只有几行），这里没有输出区所以
        # 可以安全地滚。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setObjectName("PageScroll")
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("CardArea")
        lay.addWidget(scroll)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE_XS + 2)
        scroll.setWidget(inner)

        lay.addWidget(self._row("软件", f"{APP_TITLE}  v{APP_VERSION}"))
        lay.addWidget(self._row("用途",
                                "Windows 网络故障排查工具。全部诊断在本机完成，"
                                "不上传任何数据。"))
        lay.addWidget(self._row("平台", "Windows 10 / 11（x86_64、arm64）"))
        lay.addWidget(self._row("界面", "PySide6 / Qt 6（LGPL-3.0）"))
        lay.addWidget(self._row("核心依赖", "Python 3.10+ 标准库，无其他第三方包"))
        lay.addWidget(self._row("本项目许可", "GPL-3.0-or-later"))

        lay.addWidget(self._gap())

        head = QLabel("开源协议")
        head.setObjectName("SubHead")
        lay.addWidget(head)
        for line in (
            "本项目采用 GPL-3.0-or-later。源码公开，可自由使用、"
            "修改与再分发，但衍生作品也必须以 GPL 发布并公开源码。",
            "界面框架 PySide6 采用 LGPL-3.0。本项目以动态链接方式使用它，"
            "不分发 Qt 源码本身，符合 LGPL 的要求。",
            "图标为纯几何生成（见 tools/make_icon.py），"
            "未使用任何图标库、现成素材或字体渲染，无第三方版权风险。",
            "字体仅引用 Windows 系统自带的 Microsoft YaHei UI 与 Consolas，"
            "不随程序分发。",
        ):
            lab = QLabel(line)
            lab.setObjectName("Dim")
            lab.setWordWrap(True)
            lay.addWidget(lab)

        lay.addWidget(self._gap())

        head2 = QLabel("源码仓库")
        head2.setObjectName("SubHead")
        lay.addWidget(head2)
        link = QLabel(REPO_URL)
        link.setObjectName("Link")
        link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        link.setOpenExternalLinks(True)
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(link)
        tip = QLabel("点击可直接在浏览器打开。仓库地址以发布时的实际地址为准。")
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        lay.addWidget(self._gap())

        head3 = QLabel("致谢")
        head3.setObjectName("SubHead")
        lay.addWidget(head3)
        ack = QLabel(
            "本项目所有图标与界面均为原创设计，没有使用任何现成的"
            "图标库或模板。若你发现问题或想贡献代码，欢迎到仓库提 issue。")
        ack.setObjectName("Dim")
        ack.setWordWrap(True)
        lay.addWidget(ack)

    # ---------------------------------------------------------------- #
    @staticmethod
    def _row(key: str, val: str) -> QLabel:
        lab = QLabel(f"<b>{key}</b>　{val}")
        lab.setObjectName("Dim")
        lab.setWordWrap(True)
        return lab

    @staticmethod
    def _gap() -> QLabel:
        sp = QLabel()
        sp.setFixedHeight(T.SPACE_MD)
        return sp

    def _build_actions(self, lay: QVBoxLayout) -> None:
        """**必须覆写，且一个按钮都不加。**

        不覆写的话基类会建出「开始 / 停止 / 清空」——本页既没有
        探测也没有输出区，这三个按钮全是无意义的（第一次就漏了这个，
        被测试挡下来）。
        """

    def task(self):
        """本页不参与探测。

        基类要求实现它（start() 会调），但本页既没有「开始」按钮，
        也没有输出区。真被调到就什么都不做。
        """
        return (lambda post: None, "")
