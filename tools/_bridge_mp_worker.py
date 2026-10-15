"""codex_bridge 跨进程互斥测试用的独立 worker 子模块。

仅被 tools/test_codex_bridge.py 以 multiprocessing spawn 方式导入，用于在独立进程内
对同一个 StateStore 文件并发执行 atomic_update；验证跨进程文件锁下不丢更新。
不访问网络/凭据，不写生产。
"""
import os
import sys
from pathlib import Path

# 确保能导入 codex_bridge（与测试同目录）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_bridge as cb  # noqa: E402


def run(state_file, iters, timeout, q):
    """对 state_file 累加 iters 次 +1；失败则通过队列回报原因。

    q 为 multiprocessing.Queue（spawn 子进程可 pickle 传递）。
    """
    store = cb.StateStore(Path(state_file))
    for _ in range(iters):
        def _inc(s):
            s["count"] = s.get("count", 0) + 1

        ok, reason = store.atomic_update(_inc, timeout=timeout)
        if not ok:
            q.put(("FAIL", reason))
            return
    q.put(("OK", store.load().get("count")))
