"""校验 README / CONTRIBUTING 里的每一句功能描述都和代码对得上。

这是文档最容易腐化的地方：代码改了、文档没改，而没人发现。所以把「文档里
提到的具体数字 / 名称」提取出来，逐个对着运行时断言。

    python tests/check_docs.py

除了文档一致性，还查三件容易漏的事：仓库里有没有临时文件、.gitignore
有没有挡住证书类文件、git 作者是不是只有仓库所有者。这三项单看都不
难，但漏了都得事后补救，所以放进同一个自查脚本里一次跑完。

**这不是第四个测试套件。** 三个套件是 run_tests / test_qt_ui / test_qt_window，
那个必须全绿才算能提交。本脚本是写文档时的自查工具，跑一次确认「我写的和
代码一致」就够了，不进 CI。

**为什么值得单独写**：开发期间改过测速时长（30 MB 固定 -> 按时间）、
主题按钮位置（侧边栏 -> 标题栏），README 和 CONTRIBUTING 全都没跟着改，
一路带着过三个版本。靠人记靠不住。
"""
from __future__ import annotations

import sys

# 和其余三个脚本保持一致：CI 的 windows-latest 控制台是 cp1252，
# print 中文直接 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication

q = QApplication(sys.argv[:1] or ["x"])   # 需要 QApplication 才能实例化 Page

fails = []

def check(desc, ok, detail=""):
    print(f"  {'OK  ' if ok else 'FAIL'} {desc}" + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(desc)

# ---------- README ----------
rd = (ROOT / "README.md").read_text(encoding="utf-8")
cb = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

print("=== README ===")

# 1) 主题按钮位置
check("README 不再说「侧边栏底部」切换主题",
      "侧边栏底部" not in rd)
check("README 说主题按钮在标题栏/最小化左边",
      "标题栏右上角" in rd and "最小化按钮左边" in rd)

# 2) 体积
import re as _re
m = _re.search(r'体积约 (\d+) MB（免安装）/ (\d+) MB（安装包）', rd)
check("体积写成两个数（免安装/安装包）", m is not None,
      f"{m.group(1)}MB / {m.group(2)}MB" if m else "没匹配到")

# 3) 测速时长：文档写的选项必须等于代码里的
from netdiag.core import probes
opts = tuple(probes.TIME_CHOICES)
doc_opts = tuple(int(x) for x in _re.findall(r'(\d+)\s*/\s*(\d+)|(\d+)', "") )if 0 else None
m2 = _re.search(r'（\s*(\d+(?:\s*/\s*\d+)+)\s*秒，默认\s*(\d+)\s*秒\s*）', rd)
check("README 列出测速时长选项", m2 is not None)
if m2:
    listed = tuple(int(x) for x in _re.split(r'\s*/\s*', m2.group(1)))
    dflt = int(m2.group(2))
    check("README 的时长选项 == 代码 TIME_CHOICES",
          listed == opts, f"文档 {listed} vs 代码 {opts}")
    check("README 的默认时长 == 代码 _DEFAULT_SECONDS",
          dflt == probes._DEFAULT_SECONDS,
          f"文档 {dflt} vs 代码 {probes._DEFAULT_SECONDS}")

# 4) 网络修复：README 列的 8 项必须和代码一致，且顺序一致
from netdiag.core import netfix
from netdiag.ui import pages as P
real_titles = []
for cls in P.GROUPS["网络修复"]:
    t = getattr(cls, "TITLE", None)
    if t is None:
        for a in ("title", "NAV_TITLE"):
            v = getattr(cls, a, None)
            if isinstance(v, str) and v:
                t = v; break
    real_titles.append(t or cls.__name__)
row = [l for l in rd.splitlines() if l.startswith("| **网络修复**")]
check("README 有网络修复那一行", len(row) == 1)
if row:
    body = row[0]
    for t in real_titles:
        check(f"README 网络修复含「{t}」", t in body)

# 5) 三色按钮的说法要和实际风险值对得上
risks = {f.key: f.risk for f in netfix.FIXES}
check("存在 low / mid / high 三档风险",
      set(risks.values()) == {"low", "mid", "high"},
      str(sorted(set(risks.values()))))
check("README 解释了蓝/橙/红三色",
      "蓝色是低风险" in rd and "橙色是中风险" in rd and "红色是高风险" in rd)
# 「清空 DNS 缓存」必须是 low 且非只读（它是修改操作）
fd = next(f for f in netfix.FIXES if f.key == "flushdns")
check("清空 DNS 缓存 = 低风险但会改系统（低风险 ≠ 只读）",
      fd.risk == "low" and not fd.readonly,
      f"risk={fd.risk} readonly={fd.readonly}")

# 6) 版本号在标题栏
# 版本号**不要写死**。写死的话每次 bump 版本都会红，而这个检查的
# 本意是「README 提到了标题栏版本号」，不是「README 写的是某个
# 具体版本号」。
from netdiag import APP_VERSION as _ver
check(f"README 说明标题栏有版本号（当前 v{_ver}）",
      f"v{_ver}" in rd,
      f"README 里没有 v{_ver}")

# 7) 页面清单：README 表格每行都要有对应的真实页面
from netdiag.ui.pages import PAGES      # 只查一级页面
real_pages = set()
for cls in PAGES:
    t = getattr(cls, "TITLE", None) or cls.__name__
    real_pages.add(t)
doc_pages = set(_re.findall(r'^\| \*\*(.+?)\*\*', rd, _re.M))
missing = real_pages - doc_pages
check("README 覆盖全部页面", not missing, f"漏: {sorted(missing)}" if missing else "")

# ---------- CONTRIBUTING ----------
print("\n=== CONTRIBUTING ===")
check("CONTRIBUTING 的 Python 版本 == pyproject",
      _re.search(r'Python (\d+\.\d+)', cb).group(1)
      == _re.search(r'requires-python\s*=\s*"[>=]*(\d+\.\d+)',
                    (ROOT/"pyproject.toml").read_text(encoding="utf-8")).group(1),
      f"CONTRIBUTING {_re.search(r'Python (\d+\.\d+)', cb).group(1)} vs "
      f"pyproject {_re.search(r'requires-python = \"[>=]*(\d+\.\d+)', (ROOT/'pyproject.toml').read_text(encoding='utf-8')).group(1)}")

check("CONTRIBUTING 不再说「30 MB / 3 秒最短」",
      "30 MB / 3 秒最短" not in cb)
check("CONTRIBUTING 提到 _RANGE_CAP 是护栏", "_RANGE_CAP" in cb)

check("CONTRIBUTING 有「点按钮」通则", "点按钮" in cb)
check("CONTRIBUTING 有「真画一次」通则", "真画一次" in cb)
check("PR 清单里有这两条",
      "点按钮" in cb[cb.find("## 提交 PR 之前"):]
      and "真画一次" in cb[cb.find("## 提交 PR 之前"):])

# ---------- README 引用的图片必须真的存在 ----------
# README 里写 ![]() 的话，GitHub 渲染不出图就是一片空白和碎图标。
# 链接写错路径（大小写、目录名改了）时本地预览看不出来，只有推上去才发现。
print("\n=== README 图片链接 ===")
import re as _re2
imgs = _re2.findall(r'!\[[^\]]*\]\(([^)\s]+)\)', rd)
for src in imgs:
    if src.startswith(("http://", "https://")):
        check(f"远程图片 {src[:40]}…（跳过存在性检查）", True)
        continue
    p_img = ROOT / src
    check(f"图片存在: {src}", p_img.exists(),
          f"{p_img.stat().st_size // 1024} KB" if p_img.exists()
          else "文件不存在，README 会显示裂图")
check("README 至少有一张主界面截图",
      any("screenshots/" in s for s in imgs),
      f"共 {len(imgs)} 张图")

# ---------- 三个测试套件真的存在 ----------
print("\n=== 测试套件 ===")
for t in ("run_tests.py", "test_qt_ui.py", "test_qt_window.py"):
    check(f"tests/{t} 存在", (ROOT / "tests" / t).exists())

# ---------- 没有临时 / 草稿文件 ----------
# 这类文件通常是本地调试留下的，忘了删就会被 git add -A 一起提交。
print("\n=== 临时文件 ===")
SKIP_DIRS = {".git", "__pycache__", "dist", "build", ".pytest_cache"}
JUNK = ("*.tmp", "*.bak", "*.log", "*.orig", "*.rej", "*draft*", "*TODO*")
junk = []
for pat in JUNK:
    for f in ROOT.rglob(pat):
        if not any(part in SKIP_DIRS for part in f.parts):
            junk.append(str(f.relative_to(ROOT)))
check("仓库无临时与草稿文件", not junk, ", ".join(junk[:5]))

# ---------- .gitignore 挡住凭据类文件 ----------
# 证书和 base64 文本一旦提交进公开仓库就是泄露，事后删 commit 也算泄露。
print("\n=== 凭据防护 ===")
gi = ROOT / ".gitignore"
have = gi.read_text(encoding="utf-8") if gi.exists() else ""
missing = [n for n in ("*.pfx", "*.pem", "*.key", "base64") if n not in have]
check(".gitignore 忽略证书与 base64 文本", bool(have) and not missing,
      "缺少: " + ", ".join(missing) if missing else ("未找到 .gitignore" if not have else ""))

# ---------- git 作者身份 ----------
# 提交历史是公开的，作者名会出现在每个 commit 页面。
print("\n=== git 作者 ===")
try:
    import subprocess
    log = subprocess.run(["git", "log", "--format=%an%n%cn"],
                         capture_output=True, text=True,
                         cwd=str(ROOT), timeout=30).stdout
    authors = sorted({a for a in log.split() if a})
    check("git 作者只有仓库所有者", authors == ["aicbbuu"],
          ", ".join(authors) if authors else "无提交记录")
except (OSError, ImportError, Exception):
    print("  跳过 git 作者检查（非 git 仓库或 git 不可用）")

print("\n" + ("FAILS: " + str(fails) if fails else "FAILS: 无"))
sys.exit(1 if fails else 0)