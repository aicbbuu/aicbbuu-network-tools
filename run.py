#!/usr/bin/env python3
"""
aicbbuu network tools — 启动入口

用法：
    python run.py

为什么不用 netdiag/__main__.py 作为打包入口：PyInstaller 会把入口脚本
当作独立文件执行，相对导入（from .app import main）会因丢失包上下文而失败。
run.py 先把项目根目录塞进 sys.path，再走绝对导入，两种方式都能跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 保证从任意目录调用都能找到 netdiag 包
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from netdiag.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
