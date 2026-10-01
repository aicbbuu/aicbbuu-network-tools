"""
打包脚本：把项目编成单个 Windows exe。

用法：
    python build.py              # 打包
    python build.py --clean      # 先清理再打包
    python build.py --console    # 保留控制台窗口（调试用）

依赖：pip install pyinstaller PySide6

注意：必须用装了 PySide6 的解释器运行本脚本（它会调用
      sys.executable 里的 PyInstaller）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# 同理：release.yml 在 windows-latest 上跑本脚本，cp1252 控制台会
# 让打包日志里的中文直接崩掉。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "aicbbuu-network-tools"
ICON = ROOT / "netdiag" / "assets" / "icon.ico"


def run(cmd: list[str], **kw) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd, **kw)


def clean() -> None:
    for d in ("build", "dist", "__pycache__"):
        p = ROOT / d
        if p.exists():
            print(f"  删除 {p}")
            shutil.rmtree(p, ignore_errors=True)
    for p in ROOT.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    for p in ROOT.rglob("*.pyc"):
        p.unlink(missing_ok=True)


def build(console: bool = False) -> int:
    if not ICON.is_file():
        print(f"[错误] 找不到图标：{ICON}")
        return 1

    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed" if not console else "--console",
        "--name", APP_NAME,
        "--icon", str(ICON),
        "--add-data", f"{ROOT / 'netdiag' / 'assets'}{sep}assets",
        # 页面是按注册表动态实例化的，PyInstaller 的静态分析看不到
        # 引用，必须逐个声明
        "--hidden-import", "netdiag.ui.pages",
        "--hidden-import", "netdiag.ui.pages.ping_page",
        "--hidden-import", "netdiag.ui.pages.trace_page",
        "--hidden-import", "netdiag.ui.pages.dns_page",
        "--hidden-import", "netdiag.ui.pages.ports_page",
        "--hidden-import", "netdiag.ui.pages.speed_page",
        "--hidden-import", "netdiag.ui.pages.netinfo_page",
    "--hidden-import", "netdiag.ui.pages.sysdiag_page",
    "--hidden-import", "netdiag.ui.pages.subnetscan_page",
    "--hidden-import", "netdiag.ui.pages.diagnose_page",
    "--hidden-import", "netdiag.ui.pages.fix_page",
    "--hidden-import", "netdiag.ui.pages.hopmtu_page",
    "--hidden-import", "netdiag.ui.pages.multiprobe_page",
        # 排除 Tcl/Tk：本项目只用 PySide6，不进包能省不少体积
        "--exclude-module", "tkinter",
        "--exclude-module", "test",
        # Qt 插件：platforms/ 是必须的（否则 exe 启动报
        # "could not find or load the Qt platform plugin"）。
        # imageformats 用不到，排掉省体积。
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "PySide6.QtDataVisualization",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtNetwork",
        "--exclude-module", "PySide6.QtOpenGL",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "PySide6.QtSql",
        "--exclude-module", "PySide6.QtTest",
        "--exclude-module", "PySide6.QtXml",
        "--exclude-module", "PySide6.QtDesigner",
        "--exclude-module", "PySide6.QtHelp",
        # 清理没用的模块
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
        "--exclude-module", "setuptools",
        "--exclude-module", "pip",
        # 不显示打包日志里的可选依赖警告
        "--log-level", "WARN",
        # 入口用 run.py 而不是 netdiag/__main__.py：后者用相对导入，
        # PyInstaller 以单文件脚本方式执行时会丢失包上下文。
        str(ROOT / "run.py"),
    ]

    rc = run(cmd, cwd=ROOT)
    if rc != 0:
        return rc

    exe = ROOT / "dist" / f"{APP_NAME}.exe"
    if not exe.is_file():
        print("[错误] 没有生成 exe")
        return 1

    size_mb = exe.stat().st_size / 1024 / 1024
    print()
    print("=" * 52)
    print(f"  打包完成：{exe}")
    print(f"  体积：{size_mb:.1f} MB")
    print("=" * 52)
    if size_mb < 40:
        print()
        print("提示：体积偏小，Qt 的 platforms 插件可能没打进去。")
        print("     症状是 exe 启动时报「could not find or load the")
        print("     Qt platform plugin」。先确认本脚本是用装了")
        print("     PySide6 的解释器运行的。")
    print()
    print("注意：未签名的 exe 可能被杀毒软件误报。")
    print("     这是 PyInstaller 的已知问题，与本项目代码无关。")
    print("     如需彻底解决，需购买代码签名证书（EV 证书约 $200-400/年）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 aicbbuu network tools")
    ap.add_argument("--clean", action="store_true", help="打包前清理中间产物")
    ap.add_argument("--console", action="store_true", help="保留控制台窗口（调试用）")
    args = ap.parse_args()

    if args.clean:
        clean()
    return build(console=args.console)


if __name__ == "__main__":
    raise SystemExit(main())
