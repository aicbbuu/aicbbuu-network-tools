"""
子进程输出编码处理。

Windows 的命令行工具（ping / tracert / nslookup / ipconfig / netstat）
输出的是 GBK（CP936），不是 UTF-8。按 UTF-8 硬解会得到一堆乱码，
这��本项目实际踩过的坑。

本模块的策略：不猜，先探测。读一小段字节，依次尝试候选编码，
第一个能合法解码的就是它。
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from typing import Callable, Iterable

IS_WIN = sys.platform == "win32"

#: 按优先级排列的候选编码。UTF-8 在前是因为现代工具（如 tracert -4
#: 的部分英文输出、Python 自己产生的输出）确实是 UTF-8。
CANDIDATES = ("utf-8", "gbk", "cp936", "latin-1")

#: 判定「样本足以做决定」所需的字节数
_SNIFF_BYTES = 512

#: 全局终止信号。关窗时 set，所有正在跑的子进程都会被 kill。
#:
#: 以前没有这个。后果实测得到：启动路由追踪后直接关窗口，线程还活着，
#: ``tracert.exe`` 变成**孤儿进程**留在后台继续跑（用户看到任务管理器里
#: 有个 tracert 一直不消失）。而重新打包时残留的 exe 进程会锁住输出
#: 文件，导致 ``PermissionError: [WinError 5] 拒绝访问``。
_SHUTDOWN = threading.Event()

#: 子进程默认超时（秒）。按命令不同，调用方可覆盖。
#:
#: ``proc.wait()`` 原本是无限等的——tracert 遇到不回 ICMP 的黑洞跳点会
#: 一直等下去（Windows 默认每跳超时 1s、3 次重试，30 跳就是 90s+，
#: 遇到黑洞更长），用户既看不到进展也停不下来。
_DEFAULT_TIMEOUT = 45.0


def request_shutdown() -> None:
    """通知所有正在跑的子进程退出。关窗时调用。"""
    _SHUTDOWN.set()


def clear_shutdown() -> None:
    """清除终止信号。重新启动任务前调用。"""
    _SHUTDOWN.clear()


def detect_encoding(sample: bytes) -> str:
    """探测一段字节最可能的编码。

    纯本地推理，不联网、不写文件。latin-1 永不失败，所以它一定在
    候选列表里作为最后的兜底。
    """
    if not sample:
        return "utf-8"

    for enc in CANDIDATES[:-1]:
        try:
            sample.decode(enc)
        except UnicodeDecodeError:
            continue
        # 能解出来，但如果是 gbk 解出来的、里面却没有任何高位字节，
        # 说明这段其实是纯 ASCII，utf-8 和 gbk 结果一样，无所谓。
        return enc

    return CANDIDATES[-1]


def iter_lines(
    cmd: Iterable[str],
    on_line: Callable[[str], None],
    *,
    creationflags: int = 0,
    timeout: float = _DEFAULT_TIMEOUT,
) -> int:
    """运行 cmd，逐行调用 on_line。返回子进程的退出码。

    不使用 ``text=True``——那样只能指定一种编码，而我们需要在看过
    实际字节之后再决定用哪种。改为按字节读，自己解码。

    **缓冲读而不是逐字节读。** 逐字节读（``proc.stdout.read(1)``）：
    每读一个字节都要过一次 Python 层调用，tracert 30 跳 × 3 次探测
    的输出量下这是纯 CPU 浪费，而且没有任何好处——我们要的是「凑齐
    一行就回调」，不是「立刻知道每��字节」。改成读 4096，再自己按
    ``\n`` 拆行，语义完全一样但快两个数量级。

    **异常与超时都保证清理。** 若 ``proc.wait()`` 无限等待，
    管道读出错时也直接抛出去，Popen 对象泄漏、进程变孤儿。现在
    try/except/finally 三重保证：异常时 kill、finally 里关管道、
    wait 带超时。
    """
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    assert proc.stdout is not None

    # 读管道必须在**另一个线程**里。
    #
    # 直觉写法是在主循环里 `chunk = proc.stdout.read(4096)`，然后检查
    # _SHUTDOWN——但那是阻塞调用：它会一直等到攒满 4096 字节或进程
    # 结束才返回。tracert 的输出是「等一跳、打印一行、等下一跳」，
    # 4096 字节可能要几十秒才凑齐，于是关窗信号根本来不及被检查。
    # 实测就是这个：request_shutdown() 之后线程 10 秒都没退出，
    # tracert.exe 还在后台跑。
    #
    # 改成「后台线程只管读、往队列里塞；主线程等队列或等信号」。
    q: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
    stop_reading = threading.Event()

    def _pump() -> None:
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                if stop_reading.is_set():
                    break
                while not stop_reading.is_set():
                    try:
                        q.put(chunk, timeout=0.2)
                        break
                    except queue.Full:
                        continue
        except (OSError, ValueError):
            pass
        finally:
            try:
                q.put_nowait(None)      # 哨兵：管道读完
            except queue.Full:
                pass

    reader = threading.Thread(target=_pump, daemon=True, name="proc-reader")
    reader.start()

    buf = bytearray()
    enc: str | None = None
    killed = False
    eof = False

    def _kill() -> None:
        nonlocal killed
        if killed:
            return
        killed = True
        stop_reading.set()
        try:
            proc.kill()
        except OSError:
            pass                      # 已经退出了

    try:
        while not eof:
            # 等「来了一块数据」或「要收工了」或「管道关了」
            while True:
                if _SHUTDOWN.is_set():     # 关窗：立刻停，别留孤儿进程
                    _kill()
                    break
                try:
                    chunk = q.get(timeout=0.2)
                    break
                except queue.Empty:
                    if not reader.is_alive() and q.empty():
                        chunk = None
                        break
            if chunk is None:
                eof = True
                break

            buf += chunk
            if enc is None and len(buf) >= 16:
                enc = detect_encoding(bytes(buf[:_SNIFF_BYTES]))
            # 拆行：最后一个元素可能是未完成的尾巴，留到下一轮
            *lines, tail = bytes(buf).split(b"\n")
            buf = bytearray(tail)
            for raw in lines:
                on_line(raw.decode(enc or "utf-8", errors="replace").rstrip())
    except Exception:
        _kill()
        raise
    finally:
        stop_reading.set()
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            return_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 超时了：杀掉并返回非零码，而不是让调用方永远等在这里
            _kill()
            try:
                return_code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return_code = -1

    # 管道关闭时可能还有没读到的尾巴
    if buf.strip() and not killed:
        on_line(bytes(buf).decode(enc or "utf-8", errors="replace").rstrip())

    return return_code


# ``iter_lines`` 接受 cmd，但上面的 creationflags 在调用方计算更方便。
# 提供一个包装，替调用方填上 Windows 的 CREATE_NO_WINDOW。
def run(cmd: Iterable[str], on_line: Callable[[str], None]) -> int:
    """同 iter_lines，但自动隐藏子进程控制台窗口。"""
    # 清掉可能残留的终止信号。这个 Event 是「一次性」的——它只在关窗
    # 时被 set，而关窗之后本来就不该再有新命令跑起来。但如果因为某种
    # 原因（测试里反复创建/关闭窗口）Event 残留了下来，后续所有命令都会
    # 刚启动就被 kill，表现为「所有探测都秒失败」。主动运行命令本身
    # 就说明程序还活着，所以这里清掉是安全的。
    _SHUTDOWN.clear()
    return iter_lines(
        cmd,
        on_line,
        creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
    )
