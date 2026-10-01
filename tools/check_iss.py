"""检查 packaging/netdiag.iss 的静态问题。

**为什么需要它**：装包脚本的报错全在 CI 上暴露，而 CI 一轮 5 分钟，
一轮红一次版本号。Inno Setup 6 的编译错误分三类，都能在本地静态查出来：

1. **用了 6.7.1 不存在的指令**（如 SourceRoot——那是 Inno 7 才有的）
2. **同一段里重复指令**（Inno 取最后一个还是报错，各版本不一致）
3. **段名拼错**

这三类不需要编译器，只需要「合法指令白名单 + 重复检测」。

**不做语法全检**：花括号是否配对、变量是否已定义这些要真编译才知道。
但上面三类占了实际错误的绝大多数，而它们查起来是十行代码。

用法::

    python tools/check_iss.py
退出码 0 = 通过，1 = 有问题。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows 控制台默认 cp1252，print 一个「✓」就 UnicodeEncodeError。
# 靠 CI 的 PYTHONIOENCODING 环境变量不够稳——它只影响子进程，而
# Actions 的 `run:` 是 pwsh 起的进程，变量传递链上出过问题。**脚本
# 自己保证**：不管谁在什么环境里跑，都不会因为打印而崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ISS = Path(__file__).resolve().parent.parent / "packaging" / "netdiag.iss"

# Inno Setup 6 的合法 [Setup] 指令。**只收我们实际会用的**，不收全集——
# 白名单的意义是「不在表里就是可疑」，收全集就失去意义了。
VALID_SETUP = {
    "AppId", "AppName", "AppVersion", "AppVerName", "AppPublisher",
    "AppPublisherURL", "VersionInfoVersion", "VersionInfoCompany",
    "VersionInfoDescription", "VersionInfoProductName",
    "OutputBaseFilename", "OutputDir", "SetupIconFile",
    "DefaultDirName", "DefaultGroupName", "DisableProgramGroupPage",
    "DisableDirPage", "DisableWelcomePage", "DisableFinishedPage",
    "UninstallDisplayName", "UninstallDisplayIcon", "CreateUninstallRegKey",
    "Compression", "SolidCompression", "WizardStyle", "WizardImageFile",
    "WizardSmallImageFile", "LicenseFile", "InfoBeforeFile", "AppMutex",
    "CloseApplications", "SetupLogging", "Uninstallable",
    "PrivilegesRequired", "PrivilegesRequiredOverridesAllowed",
    "MinVersion", "OnlyBelowVersion", "ArchitecturesAllowed",
    "ArchitecturesInstallIn64BitMode",
}

VALID_SECTIONS = {"[Setup]", "[Languages]", "[Tasks]", "[Files]",
                  "[Icons]", "[Run]", "[Code]", "[Types]", "[Components]",
                  "[Registry]", "[InstallDelete]", "[UninstallDelete]",
                  "[Dirs]", "[INI]"}


def check(text: str) -> list[str]:
    problems: list[str] = []
    section: str | None = None
    seen: dict[tuple[str, str], int] = {}

    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("["):
            section = line
            if section not in VALID_SECTIONS:
                problems.append(f"第 {no} 行: 未知段名 {section}")
            continue
        if not line or line.startswith((";", "#")):
            continue

        if section == "[Setup]" and "=" in line and not line.startswith("#"):
            key = line.split("=")[0].strip()
            if key not in VALID_SETUP:
                problems.append(
                    f"第 {no} 行: [Setup] 指令 {key!r} 不在 Inno 6 白名单里。"
                    f"（若确有其指令，检查是不是 Inno 7 才有的）")
        elif section and "=" in line and not line.startswith("#"):
            # 其它段也查重复：同段同名指令 Inno 的行为各版本不一致
            key = line.split("=")[0].strip()
            k = (section, key)
            if k in seen:
                problems.append(
                    f"第 {no} 行: {section} 的 {key!r} 重复"
                    f"（首次在第 {seen[k]} 行）")
            seen[k] = no

    return problems


def main() -> int:
    if not ISS.is_file():
        print(f"找不到 {ISS}", file=sys.stderr)
        return 1
    # 必须是 UTF-8 BOM，否则 Inno 按系统 ANSI 读，中文全乱码且报错
    # 行号会指向别处
    raw = ISS.read_bytes()
    if raw[:3] != b"\xef\xbb\xbf":
        print("✗ .iss 缺少 UTF-8 BOM——Inno 会按系统代码页读，中文乱码")
        return 1
    text = raw.decode("utf-8-sig")

    problems = check(text)
    if problems:
        for p in problems:
            print("✗", p)
        print(f"\n共 {len(problems)} 个问题")
        return 1
    print(f"✓ {ISS.name} 通过静态检查"
          f"（{len(text.splitlines())} 行，UTF-8 BOM 完好）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
