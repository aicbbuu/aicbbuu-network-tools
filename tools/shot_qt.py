"""PySide6 界面的截图工具。子进程 + 超时，避免卡住会话。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT.parent

CHILD = r'''
import sys, time
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
sys.path.insert(0, r"{root}")
from PySide6.QtWidgets import QApplication
from netdiag.app import App

theme, page = sys.argv[1], sys.argv[2]
app = QApplication(sys.argv[:1])
w = App(theme)
w.resize(1120, 760)
w.show()
for _ in range(30):
    app.processEvents(); time.sleep(0.03)
if page and page != "first":
    w.switch_to(page)
for _ in range(20):
    app.processEvents(); time.sleep(0.03)
w.grab().save(r"{out}")
print("saved", page)
w._on_close()
'''

theme = sys.argv[1] if len(sys.argv) > 1 else "light"
page = sys.argv[2] if len(sys.argv) > 2 else "ping"
out = OUT_DIR / f"_qt_{theme}_{page}.png"

print(f"截图 {theme}/{page} …", flush=True)
try:
    r = subprocess.run(
        [sys.executable, "-c", CHILD.format(root=str(ROOT).replace("\\", "\\\\"),
                                            out=str(out).replace("\\", "\\\\")),
         theme, page],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    print(r.stdout.strip() or "(无输出)")
    if r.returncode != 0:
        print("STDERR:", (r.stderr or "").strip()[-1500:])
except subprocess.TimeoutExpired:
    print("TIMEOUT：60s 未返回")
    sys.exit(1)

if out.exists():
    print(f"已保存：{out}  ({out.stat().st_size} 字节)")
else:
    print("未生成截图")
    sys.exit(1)
