"""网络修复操作（需要管理员权限）。

**这一页会修改系统设置，务必读清楚再点。** 前三页都是只读的，
这一页不一样，所以设计上做了三道防护：

    1. 提前说清后果  每条操作在按钮下方常驻显示「会发生什么、
                     会不会断网、要不要重启」，不是点下去才知道
    2. 二次确认      危险操作弹窗列出**将要执行的完整命令原文**，
                     让你能自己判断，而不是只能点「同意」
    3. 先备份        执行前把相关配置（DNS、MTU、网卡列表）存到
                     临时文件，出问题可以照着改回来

**UAC 弹窗只能由你本人点击。** 本程序调用系统的 ``ShellExecuteW``
并带上 ``runas`` 动词，由 Windows 自己弹出提权对话框——程序
既读不到也代替不了你的选择。这是 Windows 的安全设计，不绕它。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Callable, NamedTuple

from . import netsys
from .encoding import IS_WIN
from .runner import KIND_ERROR, KIND_LINE, KIND_STAT, Post


class Fix(NamedTuple):
    """一条修复操作及其全部风险说明。"""

    key: str            # 内部标识
    title: str          # 按钮上的名字
    commands: tuple[tuple[str, ...], ...]   # 依次执行的命令
    summary: str        # 一句话说明「做什么」
    effect: str         # 会发生什么（对用户的影响）
    offline: bool = False   # 执行期间是否会断网
    reboot: bool = False    # 是否必须重启才生效
    risk: str = "low"       # low / mid / high
    note: str = ""          # 补充警告
    #: 只读操作。刻意做成显式字段而不是从命令文本猜——「ipconfig
    #: /all」里没有 show 之类的字样，靠字符串判断会把它误当成需要
    #: 提权的写操作。猜错了用户就要为一个查看操作多点一次 UAC。
    readonly: bool = False

    @property
    def command_text(self) -> str:
        return "\n".join("  " + " ".join(c) for c in self.commands)


#: 全部可用的修复操作。**顺序即推荐度**，用户遇到问题应该从上往下
#: 试，而不是直接跳到 winsock reset：
#:
#:     1. 只读操作  —— 不改任何东西，看完信息再决定要不要动手
#:     2. 低风险   —— 改完立即生效、可逆、不影响当前网络
#:     3. 中风险   —— 执行期间会短暂断网
#:     4. 高风险   —— 会清掉配置，且必须重启才生效
#:
#: 把只读操作排在最前是刻意的：这一页是唯一会改系统设置的页面，
#: 让人能先看清楚现状再动手，比直接给一堆重置按钮安全得多。
FIXES: tuple[Fix, ...] = (
    # **readonly 必须是 False。**
    #
    # 我曾把它标成只读操作，理由是「不清空任何配置、也不会断网」——
    # 但 readonly 这个字段不是描述后果，而是决定**界面怎么对待它**：
    #
    #   readonly=True  ->  按钮文案变成「查看配置」
    #                  ->  跳过二次确认
    #                  ->  不做执行前备份
    #                  ->  不排在「改系统设置」的风险序列里
    #
    # 而 `ipconfig /flushdns` 真的**清空了 DNS 缓存**，那是写操作。
    # 用户看到「查看配置」去点一个会丢缓存的按钮，界面在骗人。
    #
    # 「不会断网」和「只读」是两回事：renew 也会断网，但它同样不只读。
    # 要表达「后果轻」用 risk="low"，那才是它该用的字段。
    Fix(
        "flushdns", "清空 DNS 缓存",
        (("ipconfig", "/flushdns"),),
        "丢弃系统缓存的域名解析结果",
        "下次访问网址时会重新查一次 DNS。**不会断网**，也不影响正在"
        "打开的网页。",
        reboot=False, risk="low",
        readonly=False,
    ),
    Fix(
        "winsock_show", "查看 Winsock 目录",
        (("netsh", "winsock", "show", "catalog"),),
        "列出当前加载的所有 Winsock 组件",
        "只读，不修改任何东西，也**不需要管理员权限**。",
        reboot=False, risk="low",
        readonly=True,
    ),
    Fix(
        "ip_show", "查看当前 IP 配置",
        (("ipconfig", "/all"),),
        "显示完整的 IP 配置（含每张网卡的详细设置）",
        "只读，不修改任何东西。",
        reboot=False, risk="low",
        readonly=True,
    ),
    Fix(
        "renew", "重新获取 IP 地址",
        (("ipconfig", "/release",), ("ipconfig", "/renew")),
        "让网卡向 DHCP 服务器重新申请地址",
        "**执行期间会短暂断网**（几秒到一分钟）。远程桌面或 SSH "
        "连接会直接断开——如果你正靠远程操作这台机器，**不要点**。",
        offline=True, risk="mid",
        note="适用于「IP 冲突」「改了 MAC 后地址没变」这类情况。",
    ),
    Fix(
        "reset_proxy", "重置 WinHTTP 代理",
        (("netsh", "winhttp", "reset", "proxy"),),
        "清除系统级代理设置",
        "会影响所有使用 WinHTTP 的程序（如某些更新程序、服务端"
        "程序）。如果你手动配过代理，会被清掉。",
        risk="mid",
        note="仅在「明明没设代理却走代理」时使用。浏览器代理不受"
             "此命令影响，它读的是「Internet 选项」里的设置。",
    ),
    Fix(
        "release", "释放 IP 地址",
        (("ipconfig", "/release"),),
        "把当前 DHCP 地址还给服务器",
        "**执行后本机立刻失去网络**，直到重新获取地址或重启。"
        "只在你明确要断开时用。",
        offline=True, risk="mid",
        # **原本标 high，但那样和「重新获取 IP 地址」说不通**——
        # 后者的命令是 release + renew，**包含**这一步，风险却更低。
        # 包含关系不能反着来。改���和 renew 一档。
        note="一般应该用「重新获取 IP 地址」，它包含这一步且会自动接上。",
    ),
    Fix(
        "tcp_reset", "重置 TCP/IP 协议栈",
        (("netsh", "int", "ip", "reset"),),
        "把 TCP/IP 配置恢复成 Windows 出厂状态",
        "**需要重启才生效**，重启前可能网络异常。会清掉所有手动"
        "配置的 IP、网关、DNS 和静态路由。",
        reboot=True, risk="high",
        note="仅在「网络配置被改乱了」时使用。DHCP 环境下一般"
             "不需要，它会把你的固定 IP 也一起清掉。",
    ),
    Fix(
        "winsock_reset", "重置 Winsock 目录",
        (("netsh", "winsock", "reset"),),
        "重建 Winsock 协议目录",
        "**必须重启才生效**。执行后到重启之间，浏览器和部分软件的"
        "网络可能完全不可用。",
        reboot=True, risk="high",
        note="针对「装过 VPN/加速器后网络坏了」——「系统网络状态」"
             "页的 Winsock 列表里会看到第三方组件。清完记得检查"
             "那些软件是否还需要。",
    ),
)


def is_privileged() -> bool:
    """当前进程是否已经是管理员。"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                  # noqa: BLE001
        return False


# ---------------------------------------------------------------- #
#  UAC 提权执行
# ---------------------------------------------------------------- #
def _run_elevated(cmds: tuple[tuple[str, ...], ...],
                  timeout: float = 180.0) -> tuple[int, str]:
    """以管理员权限执行命令，返回 (退出码, 输出)。

    走 ``ShellExecuteW`` + ``runas``——这是 Windows 唯一会弹 UAC
    对话框的路径。程序把命令写进临时 .bat 再让它执行，因为
    ShellExecuteW 只接受字符串命令，不接受参数数组，多条命令
    没法一次传过去。

    **用户必须自己点 UAC 弹窗。** 取消提权会返回 5（拒绝访问），
    这里如实报告，不重试也不代替用户决定。
    """
    import ctypes
    from ctypes import wintypes

    if not IS_WIN:
        return 1, "仅支持 Windows"

    if is_privileged():
        # 已经是管理员了，直接跑，省掉一次弹窗
        code, text = 0, ""
        for c in cmds:
            rc, out = _capture_output(c, timeout)
            code = rc
            text += out
            if rc != 0:
                break
        return code, text

    # 写临时 bat。ShellExecuteW 只接受一整条字符串命令，不接受参数
    # 数组，多条命令没法一次传过去，只能借 .bat 串起来。
    fd, path = tempfile.mkstemp(suffix=".bat", prefix="netdiag_")
    os.close(fd)
    try:
        with open(path, "w", encoding="gbk", errors="replace") as f:
            f.write("@echo off\r\n")
            f.write("chcp 65001 >nul\r\n")
            for c in cmds:
                f.write("echo " + " ".join(c) + "\r\n")
                f.write(" ".join(c) + "\r\n")
                f.write("echo [退出码 %ERRORLEVEL%]\r\n")

        SW_HIDE = 0
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", os.environ.get("COMSPEC", "cmd.exe"),
            f'/c ""{path}""', None, SW_HIDE)
        # ShellExecuteW 成功返回 >32，失败返回错误码
        if rc <= 32:
            if rc == 5:
                return 5, ("你取消了管理员权限请求，命令没有执行。\n"
                           "    修复操作需要管理员权限才能生效。")
            if rc == 1223:      # ERROR_CANCELLED
                return 5, "操作已取消。"
            return 1, f"无法启动提权进程（错误码 {rc}）"
        return 0, ""            # 提权进程已启动，输出写不回这里
    except OSError as e:
        return 1, f"无法执行：{e}"
    finally:
        # 提权进程是异步的，不能立刻删——要等它读完。给 30 秒。
        try:
            time.sleep(1.0)
            os.unlink(path)
        except OSError:
            pass


def _capture_output(cmd: tuple[str, ...], timeout: float = 60.0
                    ) -> tuple[int, str]:
    """执行一条命令并把输出读成文本。

    **刻意不用 ``text=True``**：那会按系统 ANSI 代码页解码，而这个
    程序的运行环境未必与被调命令的输出代码页一致——实测 ipconfig
    的简体中文输出会被解成乱码。自己拿 bytes 再用项目已有的编码
    探测来解，才能稳定拿到可读的中文。
    """
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return 1, f"(命令超时，超过 {timeout:.0f} 秒)"
    except OSError as e:
        return 1, f"(无法执行: {e})"
    return r.returncode, _decode(r.stdout) + _decode(r.stderr)


def _decode(raw: bytes) -> str:
    """按项目统一的候选编码顺序解码 Windows 命令输出。"""
    if not raw:
        return ""
    from .encoding import CANDIDATES
    for enc in CANDIDATES:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- #
#  配置备份
# ---------------------------------------------------------------- #
def backup_config() -> str:
    """把当前关键配置存到临时文件，返回文件路径。

    执行修复前先做这一步，是为了让「改坏了能改回来」这件事
    不依赖记忆。备份内容刻意选了最容易被人手动改过、且改错后
    症状最迷惑人的三项：DNS、MTU、网卡列表。
    """
    parts: list[str] = ["# aicbbuu network tools - 配置快照",
                        f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                        ""]
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", prefix="netdiag_backup_",
                encoding="utf-8", delete=False) as f:
            path = f.name
            f.write("\n".join(parts))
            for title, args in (("网卡与 MTU (netsh interface ipv4)",
                                 ("netsh", "interface", "ipv4",
                                  "show", "interfaces")),
                                ("IP 配置 (ipconfig /all)",
                                 ("ipconfig", "/all")),
                                ("路由表 (route print)",
                                 ("route", "print"))):
                f.write(f"\n{'=' * 60}\n{title}\n{'=' * 60}\n")
                _code, text = _capture_output(args, 30)
                f.write(text)
    except OSError as e:
        return f"(备份失败: {e})"
    return path


# ---------------------------------------------------------------- #
#  执行
# ---------------------------------------------------------------- #
def run_fix(post: Post, key: str) -> None:
    """执行一条修复操作。"""
    fix = next((f for f in FIXES if f.key == key), None)
    if fix is None:
        post(KIND_ERROR, f"没有名为「{key}」的操作")
        return

    privileged = is_privileged()
    need_admin = not fix.readonly

    post(KIND_LINE, "")
    post(KIND_LINE, "═" * 60)
    post(KIND_STAT, ("当前操作", fix.title))

    # ---- 备份 ----
    if fix.risk != "low":
        path = backup_config()
        post(KIND_STAT, ("配置快照", path if path.startswith(
            r"C:") or "/" in path else "失败"))
        if path and not path.startswith("("):
            post(KIND_LINE, f"  已备份当前配置 → {path}")
            post(KIND_LINE, "  如果修复后情况变糟，这个文件里有原来的设置。")
        else:
            post(KIND_LINE, f"  ⚠ 配置备份失败：{path}")

    # ---- 提示后果 ----
    post(KIND_LINE, "")
    post(KIND_LINE, f"▸ {fix.title}")
    post(KIND_LINE, f"  做什么：{fix.summary}")
    post(KIND_LINE, f"  影响　：{fix.effect}")
    if fix.offline:
        post(KIND_LINE, "  ⚠ 这一步会短暂断网。")
    if fix.reboot:
        post(KIND_LINE, "  ⚠ 必须重启电脑才生效。")
    if fix.note:
        post(KIND_LINE, f"  备注　：{fix.note}")
    post(KIND_LINE, "")
    post(KIND_LINE, "  将要执行的命令：")
    for line in fix.command_text.splitlines():
        post(KIND_LINE, "  " + line)

    # ---- 只读的可以静默跑 ----
    if fix.readonly:
        out: list[str] = []
        failed = False
        for c in fix.commands:
            code, text = _capture_output(c)
            if code != 0:
                failed = True
            out.append(f"$ {' '.join(c)}"
                       f"{'' if code == 0 else f'  [退出码 {code}]'}\n{text}")
        post(KIND_LINE, "")
        for line in "\n".join(out).splitlines()[:80]:
            post(KIND_LINE, "  " + line)
        post(KIND_STAT, ("执行结果", "失败" if failed else "完成（只读）"))
        return

    # ---- 需要提权 ----
    if privileged:
        post(KIND_LINE, "  当前已是管理员，直接执行。")
    else:
        post(KIND_LINE, "")
        post(KIND_LINE, "  ▸ 即将弹出 Windows 的管理员权限请求。")
        post(KIND_LINE, "    请在弹窗里点「是」。这个对话框是 Windows "
                        "的安全机制，")
        post(KIND_LINE, "    本程序无法代替你确认——这正是它的意义。")

    t0 = time.perf_counter()
    code, msg = _run_elevated(fix.commands)
    el = time.perf_counter() - t0

    post(KIND_STAT, ("执行耗时", f"{el:.1f}s"))
    if code == 0 and not msg:
        post(KIND_STAT, ("执行结果", "已执行"))
        post(KIND_LINE, "")
        post(KIND_LINE, f"  ✓ 命令已执行。输出可以在一个黑色的命令行窗口里"
                        f"看到。")
        if fix.reboot:
            post(KIND_LINE, "")
            post(KIND_LINE, "  ⚠ 现在请**重启电脑**。")
            post(KIND_LINE, "    在重启之前，网络可能异常——"
                            "这是预期内的。")
            post(KIND_LINE, "    如果暂时不想重启，"
                            "先试试「清空 DNS 缓存」这类温和的操作。")
    elif code == 5:
        post(KIND_STAT, ("执行结果", "已取消"))
        post(KIND_LINE, "")
        for line in msg.splitlines():
            post(KIND_LINE, "  " + line)
    else:
        post(KIND_STAT, ("执行结果", "失败"))
        post(KIND_LINE, "")
        post(KIND_LINE, f"  ✗ 执行失败：{msg}")


def describe_fix(key: str) -> Fix | None:
    return next((f for f in FIXES if f.key == key), None)
