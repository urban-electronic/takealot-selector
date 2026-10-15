"""codex_bridge 并发写与自检不回退回归（离线，不访问网络/凭据）。

- 并发 ACK/fetch/reply 通过 atomic_state_update 串行化，不得丢更新；
- 自检 state_rw_probe 用独立 StateStore（临时态文件），不得触碰/回退真实协作游标；
- WB-103-R5：锁不可用时 fail closed（不写文件、不谎报成功）；probe 与并发 ACK 交错
  不丢真实游标；跨进程（multiprocessing/spawn，含 Windows）文件锁下不丢更新。
"""
import importlib.util
import multiprocessing as mp
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import codex_bridge as cb  # noqa: E402


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    sp = tmp_path / "codex_bridge_state.json"
    sp.write_text("{}")
    lock = tmp_path / "codex_bridge_state.json.lock"
    monkeypatch.setattr(cb, "STATE_FILE", sp)
    monkeypatch.setattr(cb, "_STATE_LOCK", lock)
    return sp


def test_atomic_concurrent_updates_no_lost(isolated_state):
    # 50 个并发写各自 +1：若无跨进程互斥，读-改-写交错会丢更新（最终 < 50）。
    N = 50

    def worker():
        def inc(s):
            s["cnt"] = s.get("cnt", 0) + 1

        assert cb.atomic_state_update(inc)

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = cb.load_state()
    assert state.get("cnt") == N, f"并发写应无丢失更新，实际 cnt={state.get('cnt')}"


def test_selftest_probe_does_not_rollback_cursor(isolated_state):
    # 真实协作游标先推进到 issue=99
    def set_cursor(s):
        cur = s.get("103")
        if not isinstance(cur, dict):
            cur = {}
        cur["issue"] = 99
        s["103"] = cur

    cb.atomic_state_update(set_cursor)
    assert cb.load_state().get("103", {}).get("issue") == 99

    # 运行自检 probe（内部使用完全独立的临时态文件）
    ok, detail = cb.state_rw_probe()
    assert ok, f"state_rw_probe 应成功：{detail}"

    # 真实游标必须保持 99，未被自检回退（PR103 桥接并发状态修复）
    assert cb.load_state().get("103", {}).get("issue") == 99, "自检 probe 不得回退真实协作游标"


# ---------------- WB-103-R5 新增 ----------------

def test_atomic_fail_closed_when_lock_unavailable(isolated_state, monkeypatch):
    """WB-103-R5 第 1 条：跨进程锁获取失败时 atomic_state_update 必须 fail closed——
    返回 False 且**绝不写文件**、绝不谎称串行化成功。"""
    def fake_acquire(lockf, timeout=10.0, step=0.05):
        return False, "msvcrt"  # 模拟锁获取失败

    monkeypatch.setattr(cb, "_acquire_lock", fake_acquire)
    before = isolated_state.read_text()
    def mut(s):
        s["cnt"] = s.get("cnt", 0) + 1

    result = cb.atomic_state_update(mut)
    assert result is False, "锁不可用时必须返回 False（fail closed）"
    after = isolated_state.read_text()
    assert before == after, "fail closed 时不得写入任何状态"
    assert '"cnt"' not in after, "fail closed 时不得出现本次改动"


def test_acquire_lock_returns_none_backend_when_unavailable():
    """WB-103-R5 第 1 条：两个平台锁后端均不可用时，_acquire_lock 必须返回 (False, None)，
    不得误报成功（原实现在无可用后端时仍 return True）。"""
    import codex_bridge as _cb

    class _NoLock:
        pass

    saved_m, saved_f = _cb._msvcrt, _cb._fcntl
    _cb._msvcrt, _cb._fcntl = None, None
    try:
        lockf = open(str(HERE / "tmp_none_backend.lock"), "a+")
        try:
            acquired, backend = _cb._acquire_lock(lockf)
        finally:
            lockf.close()
        assert acquired is False
        assert backend is None
    finally:
        _cb._msvcrt, _cb._fcntl = saved_m, saved_f
        try:
            (HERE / "tmp_none_backend.lock").unlink()
        except Exception:
            pass


def test_probe_acks_interleave_no_lost_cursor(isolated_state, monkeypatch):
    """WB-103-R5 第 2 条：state_rw_probe 用独立临时态文件，即便本进程 ACK 在 probe 中途
    交错执行，真实游标也不丢更新（原实现会临时改写全局 STATE_FILE → ACK 写临时文件被删 → 丢更新）。
    用 Event 门闩精确控制 probe 第一次写与 ACK 交错顺序。"""
    # 真实游标先推进到 6199526037
    def set_cursor(s):
        cur = s.get("103")
        if not isinstance(cur, dict):
            cur = {}
        cur["issue"] = 6199526037
        s["103"] = cur

    cb.atomic_state_update(set_cursor)
    assert cb.load_state().get("103", {}).get("issue") == 6199526037

    allow_ack = threading.Event()
    ack_done = threading.Event()
    real_store = cb.StateStore(cb.STATE_FILE)

    def ack_thread():
        allow_ack.wait()
        def _adv(s):
            cur = s.get("103")
            if not isinstance(cur, dict):
                cur = {}
            cur["issue"] = 6200797492
            s["103"] = cur
        real_store.atomic_update(_adv)
        ack_done.set()

    t = threading.Thread(target=ack_thread)
    t.start()

    # 仅在 probe 的临时态写（path != STATE_FILE）第一次落盘时放行 ACK，待其完成后才写第二次
    orig_save_state = cb.save_state

    def patched_save_state(state, path=None):
        target = path or cb.STATE_FILE
        if target != cb.STATE_FILE:
            allow_ack.set()
            ack_done.wait(timeout=5)
        return orig_save_state(state, path)

    monkeypatch.setattr(cb, "save_state", patched_save_state)

    ok, detail = cb.state_rw_probe()
    assert ok, f"state_rw_probe 应成功：{detail}"
    t.join(timeout=5)

    # 真实游标必须为 ACK 推进后的值，未被 probe 临时态干扰（证明不丢更新）
    assert cb.load_state().get("103", {}).get("issue") == 6200797492, \
        "probe 与 ACK 交错时真实游标不应丢更新"


def test_cross_process_atomic_no_lost_updates(tmp_path):
    """WB-103-R5 第 3 条：跨进程（multiprocessing，Windows 默认 spawn）并发 atomic_update
    对同一 StateStore 文件，不得丢更新。N 进程各 +iters，最终 count == N*iters。"""
    ctx = mp.get_context("spawn")  # Windows 默认；显式保证跨进程文件锁语义
    state_file = tmp_path / "mp_state.json"
    store = cb.StateStore(state_file)
    store.save({"count": 0})

    n_proc, iters, timeout = 4, 25, 30
    q = ctx.Queue()

    worker_path = HERE / "_bridge_mp_worker.py"
    spec = importlib.util.spec_from_file_location("_bridge_mp_worker", worker_path)
    worker_mod = importlib.util.module_from_spec(spec)
    # 注册到 sys.modules，使 target=worker_mod.run 能被 pickle 为 `_bridge_mp_worker.run`，
    # 供 Windows spawn 子进程按限定名重新导入（否则 PicklingError）。
    sys.modules["_bridge_mp_worker"] = worker_mod
    spec.loader.exec_module(worker_mod)

    procs = []
    for _ in range(n_proc):
        p = ctx.Process(target=worker_mod.run, args=(str(state_file), iters, timeout, q))
        procs.append(p)
        p.start()

    results = []
    for _ in range(n_proc):
        results.append(q.get(timeout=60))
    for p in procs:
        p.join(timeout=10)
        assert p.exitcode == 0, f"worker 进程异常退出 exitcode={p.exitcode}"

    fails = [r for r in results if r[0] != "OK"]
    assert not fails, f"worker 失败：{fails}"
    final = store.load().get("count")
    expected = n_proc * iters
    assert final == expected, f"跨进程丢更新：期望 {expected}，实际 {final}"
