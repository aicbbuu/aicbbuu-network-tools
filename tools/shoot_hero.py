"""为README 生成主界面截图（打开即截，不要点「开始」）。

    python tools/shoot_hero.py [输出目录]

**必须「打开即截」，不能跑完再截。** 门面图要展示的是「程序待命」的
状态：输出区显示那句灰色提示文字，统计块是「—」。

如果点了「开始」，输出区会被真实结果填满——本机 ICMP 被网络层拦截，
所以会报「网关通但公网不通」。看图的人会以为这是某次故障的现场，而且
不同机器跑出来的结论不一样（取决于当时网络），截图就无法复现。

反过来，不点开始时那句「点「开始」按顺序跑一遍检测，逐项结论会显示
在这里」恰好说明「我知道下一步该点什么」——比空白强，也不像出了问题。

用 windows 插件真实渲染。offscreen 下 QSS 不生效，量出来的像素不可信。
"""
from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication          # noqa: E402

DEFAULT_OUT = ROOT / "docs/screenshots"


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1] or ["x"])
    print("平台 =", app.platformName())
    if app.platformName() != "windows":
        print("警告：非 windows 插件下QSS 可能不生效，截图不可信。")

    from netdiag.app import App

    w = App("light")
    w.show()

    def pump(n: int = 20) -> None:
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    pump(50)
    w.resize(1460, 900)          # README 里横向比例更合适
    pump(30)

    # **不点「开始」** —— 门面图要的是「程序待命」的状态，不是「刚跑完」。
    # 点了开始，输出区会被真实结果填满（比如「网关通但公网不通」），看图
    # 的人会以为这是某次故障的现场；而且不同机器跑出来的结论不一样
    # （取决于当时网络），截图就无法复现。
    #
    # 不点开始时输出区显示的是那句灰色提示文字——这恰好说明「我知道
    # 下一步该点什么」，比空白强，也不像出了问题。
    w.switch_to("diagnose")
    pump(20)

    dst = out_dir / "main-diagnose.png"
    pm = w.grab()
    pm.save(str(dst))
    print(f"已保存 {dst}  {pm.width()}x{pm.height()}  "
          f"{dst.stat().st_size // 1024} KB")

    # PNG 比 JPEG 小：界面以纯色块为主，PNG 压缩效率高且无损。
    jpg = out_dir / "_tmp.jpg"
    pm.save(str(jpg), "JPG", 90)
    print(f"  对比 JPEG q90: {jpg.stat().st_size // 1024} KB")
    jpg.unlink()

    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())