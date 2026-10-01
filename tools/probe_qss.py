"""二分定位「让某条目标规则失效」的那条 QSS 规则。

为什么需要它：Qt 遇到解析不了的选择器（如 :not()）会**静默丢弃
该规则及其之后的全部内容**，而 theme.qss() 生成的文本看起来完全
正常、花括号也配平。唯一可靠的判据是让 Qt 真的渲染再量像素，
而定位到具体哪条只能靠二分。

用法（必须在 windows 插件下跑，offscreen 的像素不可信）：
    python tools/probe_qss.py

思路反过来做：完整 QSS 已被污染（目标控件不生效）。逐条**删除**
每条规则，看删掉哪条之后目标控件恢复正常 —— 那条就是元凶。

Qt 遇到无法解析的声明时会丢弃该规则，但这里更可能是
选择器匹配范围过宽（比如 QScrollArea 这种内含控件的容器），
所以逐条删除比逐条累加可靠。
"""
import sys, os
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import (QApplication, QPushButton, QLineEdit,
                               QPlainTextEdit, QFrame)
from netdiag.ui import theme as T

q = QApplication(sys.argv[:1:] or ["x"])
full = T.qss(T.LIGHT)
chunks = full.split("}")
chunks = [c + "}" for c in chunks[:-1]]


def probe(css, key):
    q.setStyleSheet(css)
    if key == "QPushButton#Primary":
        b = QPushButton(); b.setObjectName("Primary")
    elif key == "QLineEdit":
        b = QLineEdit()
    elif key == "QPlainTextEdit#Console":
        b = QPlainTextEdit(); b.setObjectName("Console")
    else:
        b = QFrame(); b.setObjectName("Card")
    b.resize(100, 40)
    b.show(); q.processEvents()
    img = b.grab().toImage()
    got = img.pixelColor(img.width()//2, img.height()//2).name()
    b.hide()
    return got


WANT = {"QPushButton#Primary": "#2563eb", "QLineEdit": "#eef2f7",
        "QPlainTextEdit#Console": "#111827", "QFrame#Card": "#ffffff"}

print(f"完整 QSS：{len(chunks)} 条规则\n")
base = {k: probe(full, k) for k in WANT}
for k, v in base.items():
    print(f"  现状 {k:<24} {v}  期望 {WANT[k]}  "
          f"{'OK' if v.lower() == WANT[k].lower() else '❌'}")

bad_keys = [k for k in WANT if base[k].lower() != WANT[k].lower()]
if not bad_keys:
    print("\n全部正常，无需排查"); sys.exit(0)

key = bad_keys[0]
print(f"\n=== 排查 {key}（期望 {WANT[key]}）===")
found = []
for i, c in enumerate(chunks):
    if key in c:          # 不能删目标规则自己
        continue
    got = probe("".join(chunks[:i] + chunks[i+1:]), key)
    if got.lower() == WANT[key].lower():
        found.append((i, got, " ".join(c.split())[:170]))
        print(f"\n  删掉第 {i+1} 条后恢复：")
        print(f"    {found[-1][2]}")
        if len(found) >= 4:
            break

if not found:
    print("  删任何单条都恢复不了 —— 是多条共同作用，或问题在 QSS 之外")
q.setStyleSheet("")