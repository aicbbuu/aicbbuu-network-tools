"""
后台任务调度。

所有耗时操作（ping 4 次、扫 19 个端口、下载 10MB）都不能在 UI 线程里跑，
否则窗口会卡死。探测函数把结果推进一个队列，UI 线程定期 drain。

探测器只需要一个 ``post(kind, payload)`` 回调，剩下的并发细节由这里负责。
"""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Callable

#: 事件类型。UI 层按这个分派。
KIND_LINE = "line"        # 纯文本行
KIND_STAT = "stat"        # 结构化统计
KIND_ERROR = "error"      # 探测过程抛异常
KIND_DONE = "done"        # 探测正常结束

#: 事件回调签名。探测函数收到 post，用它把结果/进度/错误发回 UI。
#: 早期 probes.py 和 probes_ext.py 各自定义了一份同样的别名——三处
#: 重复，改一处忘另一处就出 ImportError。统一放这里。
Post = Callable[[str, object], None]


class Cancelled(BaseException):
    """任务已被取消。

    **刻意继承 BaseException 而不是 Exception。** 探测代码里有 13 处
    ``except Exception`` 用来兜住网络错误并继续（比如测速换下一个源），
    它们会把这个异常一并吞掉——取消后测速循环会「该源不可用」然后
    接着试下一个源，用户点了停止照样把几个源轮着跑一遍。

    继承 BaseException 就让所有 ``except Exception`` 自动放行它，
    而 ``except BaseException`` 只在 runner 自己的 wrapped() 里出现，
    不受影响。语义上也对：取消不是「探测出错」，是控制流。
    
    # 完整说明见下。

    探测函数是长循环（ping 4 次、扫 19 个端口、下载几十 MB）。以前
    cancel 只是让 post 悄悄丢弃输出，循环照跑到底——用户点了「停止」，
    路由追踪还要再跑 77 秒、端口扫描再跑 48 秒，测速更是会把整个
    大文件下完。而且因为 stop() 立刻把「开始」按钮解禁，用户能马上
    再点一次，于是两个任务并发跑。

    现在 post 在取消后抛这个异常，探测循环只要**在每轮开头调一次
    post**（它们本来就在调，用来输出进度）就会立刻跳出。这是唯一
    能在不引入 asyncio / 进程级终止的前提下拿到「点了停止马上停」
    的办法——子线程没法安全地被强杀。
    """


class Task:
    """一次后台探测任务的句柄。"""

    def __init__(self, name: str):
        self.name = name
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, fn: Callable[[Callable], None], post: Callable[[str, object], None]) -> None:
        def wrapped() -> None:
            def emit(kind: str, payload: object) -> None:
                # 取消后不再吐数据；而且要**抛出去**让探测循环自己
                # 停下来。只 return 的话循环会跑完，用户点了「停止」
                # 却眼睁睁看着输出继续刷。
                if self._cancel.is_set():
                    if kind in (KIND_LINE, KIND_STAT):
                        raise Cancelled()
                    return
                post(kind, payload)

            try:
                fn(emit)
            except Cancelled:
                # 取消是用户主动行为，不算错误，也不该往控制台打一段
                # traceback。KIND_DONE 照常在 finally 里发——那正是
                # UI 知道「可以解禁开始按钮了」的信号，不发的话页面
                # 会永久停在「正在停止…」。
                pass
            except Exception:  # noqa: BLE001 - 任何异常都要传回 UI，不能让窗口静默死掉
                emit(KIND_ERROR, traceback.format_exc(limit=3))
            finally:
                # 用直连的 post 而非 emit：emit 在取消后会自己 return，
                # 于是 KIND_DONE 永远发不出去。
                #
                # payload 用 (name, was_cancelled) 二元组。**不要**让 UI
                # 去问 Dispatcher「这个任务还活着吗」——DONE 恰恰是任务
                # 结束**之后**才发的，那个查询永远为 False，于是每一次
                # 正常完成都会被误报成「已停止」。取消与否是任务的属性，
                # 只有任务自己知道。
                post(KIND_DONE, (self.name, self._cancel.is_set()))

        self._thread = threading.Thread(target=wrapped, daemon=True, name=self.name)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class Dispatcher:
    """UI 线程与后台线程之间的单向消息通道。"""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[str, object, str]] = queue.Queue()
        self._tasks: dict[str, Task] = {}

    def post(self, kind: str, payload: object, task: str = "") -> None:
        self._q.put((kind, payload, task))

    def submit(self, name: str, fn: Callable[[Callable], None]) -> None:
        """提交一个探测任务。同名任务会先取消旧的，避免叠加。"""
        if name in self._tasks and self._tasks[name].running:
            self._tasks[name].cancel()
        task = Task(name)
        self._tasks[name] = task
        task.start(fn, lambda k, p: self.post(k, p, name))

    def drain(self, limit: int = 400) -> list[tuple[str, object, str]]:
        """取出待处理事件。limit 防止一次刷新处理太多导致 UI 卡顿。"""
        out: list[tuple[str, object, str]] = []
        for _ in range(limit):
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def cancel(self, name: str) -> None:
        t = self._tasks.get(name)
        if t:
            t.cancel()

    def cancel_all(self) -> None:
        for t in self._tasks.values():
            t.cancel()

    def is_running(self, name: str) -> bool:
        t = self._tasks.get(name)
        return bool(t and t.running)

    def any_running(self) -> bool:
        return any(t.running for t in self._tasks.values())
