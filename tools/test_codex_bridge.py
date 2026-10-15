"""codex_bridge 并发写与自检不回退回归（离线，不访问网络/凭据）。

- 并发 ACK/fetch/reply 通过 atomic_state_update 串行化，不得丢更新；
- 自检 state_rw_probe 用独立临时态文件，不得触碰/回退真实协作游标。
"""
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
