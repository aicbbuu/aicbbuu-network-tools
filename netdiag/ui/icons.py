"""界面里用到的矢量小素材（勾号）。

**为什么自己生成而不引第三方图标库**：成品要求无版权侵权
风险。第三方图标库（Font Awesome、Material Icons 等）各有自己的
授权，MIT 也要保留版权声明，混进项目里是负担。而勾号就三条线段，
用几何画出来是零成本、零授权、可参数化的。

**为什么用 PNG 而不是 SVG**：SVG 理论上更优（矢量、跟着主题色
变只写一份文件），但 ``QCheckBox::indicator`` 的 ``image:`` 对 SVG
的支持不可靠——实测 QtSvg 明明已安装、SVG 文件也确实生成了，勾号
就是不画，QSS 解析器对 indicator 的 image 处理和普通控件不一样。
PNG 走 Qt 的原生图像加载，任何环境都画得出来。

代价是每种主题色要各生成一张 PNG，但这本来就是**按主题色生成**
的，不存在「多生成一份」的浪费：同一主题色永远复用同一个文件。
"""
from __future__ import annotations

import os

#: 素材缓存目录。放临时目录而不是包内：素材是**运行时按主题生成**
#: 的产物，不该进版本库，也不该被 PyInstaller 打进包里（打进去的
#: 是上一次构建时的旧颜色）。
_CACHE_DIR: str | None = None


def cache_dir() -> str:
    """返回素材缓存目录，不存在则创建。

    用系统临时目录而不是包目录：包目录在打包后是只读的（PyInstaller
    的 _MEIPASS），写不进去。
    """
    global _CACHE_DIR
    if _CACHE_DIR and os.path.isdir(_CACHE_DIR):
        return _CACHE_DIR
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "netdiag_ui_icons")
    os.makedirs(d, exist_ok=True)
    _CACHE_DIR = d
    return d


def check_png(color: str, size: int = 24) -> str:
    """生成（或复用）一张「白底蓝勾」的勾号 PNG，返回绝对路径。

    **自己画，不引 Qt 绘制**：QImage + QPainter 在无显示环境
    （CI 的 offscreen、单元测试）里也能用，不依赖 QApplication 实例。
    """
    safe = color.lstrip("#")
    path = os.path.join(cache_dir(), f"check_{safe}_{size}.png")
    if os.path.isfile(path):
        return path

    try:
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtGui import (QColor, QImage, QPainter, QPen,
                                   QPainterPath)
    except ImportError:
        return path          # 没有 Qt：QSS 里那行 url() 自然不生效

    # 4x 超采样再降采样，否则 15px 显示时线宽 2px 会锯齿明显
    ss = 4
    S = size * ss
    img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 勾号：左下 → 拐点 → 右上，两段折线。用 stroke 不��� fill——
    # 填充多边形表达不了「笔画」。
    path_ = QPainterPath(QPointF(0.215 * S, 0.505 * S))
    path_.lineTo(0.405 * S, 0.700 * S)
    path_.lineTo(0.790 * S, 0.295 * S)
    pen = QPen(QColor(f"#{safe}"))
    pen.setWidthF(0.140 * S)          # 15px 显示时约 2.1px
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPath(path_)
    p.end()

    img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    img.save(path, "PNG")
    return path


def check_svg(color: str) -> str:
    """生成（或复用）一个「白底蓝勾」的勾号 SVG，返回绝对路径。

    勾号的画法是两条线段组成的不闭合折线，用 ``stroke`` 而不是
    ``fill``——填充多边形没法表达「笔画」。

    参数
    ----
    color
        勾号颜色，通常传主题的 accent。

    形状要点：短线从左下往右上，长线从那里往右下，两段在拐点相接。
    线宽取 2.1（在 15px 的方块里约占 14%，Qt 会按设备像素比缩放，
    不会糊）。圆头圆角让 15px 下看起来是「手写勾」而不是「折线」。
    """
    safe = color.lstrip("#")
    path = os.path.join(cache_dir(), f"check_{safe}.svg")
    if os.path.isfile(path):
        return path

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 15 15">'
        f'<path d="M3.2 7.6 L6.1 10.6 L11.9 4.4" fill="none" '
        f'stroke="#{safe}" stroke-width="2.1" stroke-linecap="round" '
        'stroke-linejoin="round"/>'
        "</svg>"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return path


def qss_path(p: str) -> str:
    """把路径转成 QSS 里能用的形式。

    QSS 的 ``url()`` 里反斜杠是转义符。Qt 文档说正斜杠在 Windows 上
    也能用，但保险起见统一换成正斜杠——反正 Qt 两种都认，正斜杠
    一定不会踩到转义。
    """
    return p.replace("\\", "/")

def chevron_png(color: str, size: int = 24) -> str:
    """生成（或复用）一张下拉箭头 PNG，返回绝对路径。

    **为什么不用 QSS 的 border 三角。** 网上常见的写法是

        QComboBox::down-arrow { border-left: 4px solid transparent; ... }

    这在 Windows 风格下渲染不稳——实测会变成实心方块或根本不画
    （和 QSpinBox 的 up-arrow 同一个坑）。与其在不同机器上赌，
    不如生成 PNG：Qt 的图像加载在任何平台都画得出来。

    同理 `image:` 属性对 SVG 不可靠（见 check_svg 的注释），
    所以这里和勾号一样走 PNG。
    """
    safe = color.lstrip("#")
    path = os.path.join(cache_dir(), f"chevron_{safe}_{size}.png")
    if os.path.isfile(path):
        return path

    try:
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtGui import (QColor, QImage, QPainter, QPen,
                                   QPainterPath)
    except ImportError:
        return path

    ss = 4
    S = size * ss
    img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 一个向下的「⌄」，两段直线，笔画比勾号细一点
    path_ = QPainterPath(QPointF(0.255 * S, 0.400 * S))
    path_.lineTo(0.500 * S, 0.635 * S)
    path_.lineTo(0.745 * S, 0.400 * S)
    pen = QPen(QColor(f"#{safe}"))
    pen.setWidthF(0.115 * S)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPath(path_)
    p.end()

    img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    img.save(path, "PNG")
    return path
