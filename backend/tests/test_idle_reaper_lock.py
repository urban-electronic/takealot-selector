"""Isolated idle watchdog interleavings: no browser or process signals."""
import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'quote_browser.py'

def load_guard():
    tree = ast.parse(SOURCE.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('_reap_idle_browser', '_idle_guard_loop')]
    lock = threading.Lock()
    calls = []
    clock = SimpleNamespace(monotonic=lambda: 1000)
    def kill(profile):
        # Model another collector attempting to start at the exact kill point.
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
        calls.append(acquired)
        return 1
    env = dict(_lock=lock, _last_release_at=0, IDLE_CLOSE_SECONDS=300,
               time=clock, _kill_profile=kill, _collector_profile=lambda: 'isolated')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), env)
    return env, calls, lock

class IdleGuardTests(unittest.TestCase):
    def test_cleanup_owns_lock_against_new_collector(self):
        env, calls, lock = load_guard()
        if '_reap_idle_browser' in env:
            self.assertEqual(env['_reap_idle_browser'](), 1)
        else:
            # Execute the real old loop once, without sleeping.
            def sleep(_): pass
            env['time'].sleep = sleep
            def kill(_):
                acquired = lock.acquire(blocking=False)
                if acquired: lock.release()
                calls.append(acquired)
                raise KeyboardInterrupt
            env['_kill_profile'] = kill
            with self.assertRaises(KeyboardInterrupt): env['_idle_guard_loop']()
        self.assertEqual(calls, [False], 'new collector acquired lock while idle guard killed its browser')
        self.assertFalse(lock.locked())

    def test_active_and_recent_collectors_are_not_reaped(self):
        env, calls, lock = load_guard()
        guard = env['_reap_idle_browser']
        lock.acquire()
        self.assertEqual(guard(), 0)
        self.assertTrue(lock.locked())
        lock.release()
        env['_last_release_at'] = 999
        self.assertEqual(guard(), 0)
        self.assertEqual(calls, [])

    def test_kill_exception_releases_only_owned_lock(self):
        env, _, lock = load_guard()
        def fail(_): raise OSError('isolated')
        env['_kill_profile'] = fail
        with self.assertRaises(OSError): env['_reap_idle_browser']()
        self.assertFalse(lock.locked())

if __name__ == '__main__':
    unittest.main()
