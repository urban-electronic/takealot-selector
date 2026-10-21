"""运行时资源观测：线程数、进程数、cgroup 上限。

2026-10-21 线上出过一次「网站还能用、但所有图片 500」的故障：日志里是
`RuntimeError: can't start new thread`，而内存只用了 0.9G/8G、CPU 0.2。
也就是说，真正耗尽的既不是内存也不是 CPU，而是**线程/进程数**——可当时
系统里没有任何地方能回答「现在有多少线程、上限是多少」，只能靠重启试探。

这个模块只做两件事：
  1. `snapshot()`：把线程数、进程数、cgroup pids 用量与上限读成一个 dict，
     由 `/api/diag/runtime` 暴露出来，出问题当场就能看；
  2. `start_watchdog()`：每 5 分钟往日志里打一行。日志能回看，趋势就能看出来
     「是一开始就高，还是跑着跑着涨上去的」——这正是上次缺的证据。

只读，不改任何状态；读不到就如实返回 None，不抛异常。
"""
import os
import threading
import time

# 线程数的告警线。正常运行时这个服务大约 20~40 个线程（uvicorn + 几个后台
# worker + Playwright 的 node 驱动）；接近 100 就说明有东西在漏。
THREAD_WARN = 100
WATCHDOG_INTERVAL = 300


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cgroup_pids():
    """cgroup v2 / v1 两种布局都试一遍。"""
    current = _read("/sys/fs/cgroup/pids.current")
    maximum = _read("/sys/fs/cgroup/pids.max")
    if current is None:
        current = _read("/sys/fs/cgroup/pids/pids.current")
        maximum = _read("/sys/fs/cgroup/pids/pids.max")
    return _int_or_none(current), _int_or_none(maximum)


def _process_count():
    """容器里 /proc 下能看到的进程/线程目录数，读不到就返回 None。"""
    try:
        return len([name for name in os.listdir("/proc") if name.isdigit()])
    except OSError:
        return None


def _process_table():
    """按进程名归类，并数出僵尸进程。

    为什么要分名字：2026-10-21 观测到 pids 从 2 涨到 277 而 Python 线程数一直
    只有 11~12 —— 漏的不是线程，是**子进程**。只有看清是 chromium / node / Xvfb
    里的哪一个在堆积，才能判断是浏览器没关掉，还是关掉了但没人回收（僵尸）。
    """
    names = {}
    zombies = 0
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return {}, 0
    for entry in entries:
        comm = _read(f"/proc/{entry}/comm") or "?"
        names[comm] = names.get(comm, 0) + 1
        stat = _read(f"/proc/{entry}/stat")
        if not stat:
            continue
        # comm 里可能带空格和括号，所以从最后一个 ')' 之后切，第一段就是状态字母。
        try:
            state = stat.rsplit(")", 1)[1].split()[0]
        except IndexError:
            continue
        if state == "Z":
            zombies += 1
    top = dict(sorted(names.items(), key=lambda kv: -kv[1])[:8])
    return top, zombies


def _rlimit():
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        return {"nproc_soft": soft, "nproc_hard": hard}
    except Exception:  # noqa: BLE001
        return {"nproc_soft": None, "nproc_hard": None}


def snapshot():
    """当前运行时资源快照。字段缺失一律 None，不编造。"""
    threads = threading.enumerate()
    by_name = {}
    for thread in threads:
        # 线程名前缀相同的归成一类，输出才看得懂（如 buybox-read_0/1）。
        key = thread.name.split("_")[0]
        by_name[key] = by_name.get(key, 0) + 1
    pids_current, pids_max = _cgroup_pids()
    processes_by_name, zombies = _process_table()
    data = {
        "threads": len(threads),
        "threads_by_name": dict(sorted(by_name.items(), key=lambda kv: -kv[1])),
        "threads_warn": THREAD_WARN,
        "pids_current": pids_current,
        "pids_max": pids_max,
        "process_count": _process_count(),
        "processes_by_name": processes_by_name,
        "zombies": zombies,
        "pid": os.getpid(),
        "ts": time.time(),
    }
    data.update(_rlimit())
    return data


def format_snapshot(data=None):
    """一行日志用的紧凑格式。"""
    data = data or snapshot()
    return (
        f"threads={data['threads']} pids={data['pids_current']}/{data['pids_max']} "
        f"proc={data['process_count']} zombies={data['zombies']} "
        f"top={list(data['processes_by_name'].items())[:4]}"
    )


def start_watchdog(interval=WATCHDOG_INTERVAL):
    """起一个后台线程，定期把资源用量写进日志。重复调用只起一个。"""
    existing = [t for t in threading.enumerate() if t.name == "runtime-watchdog"]
    if existing:
        return existing[0]

    def run():
        while True:
            try:
                data = snapshot()
                line = format_snapshot(data)
                if data["threads"] >= THREAD_WARN:
                    print(f"[runtime] 线程数偏高 {line}", flush=True)
                else:
                    print(f"[runtime] {line}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[runtime] 采集资源用量失败：{type(exc).__name__}: {exc}", flush=True)
            time.sleep(interval)

    thread = threading.Thread(target=run, daemon=True, name="runtime-watchdog")
    thread.start()
    return thread
