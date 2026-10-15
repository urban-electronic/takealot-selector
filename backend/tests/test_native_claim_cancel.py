"""Deterministic native HTTP claim cancellation; no network or browser."""
import asyncio
import importlib.util
from pathlib import Path
import threading

spec=importlib.util.spec_from_file_location('claim_runner',Path(__file__).resolve().parents[2]/'tools/native-collector/collector.py')
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

def test_claim_failure_is_not_idle():
    class Api:
        def call(self,*args): raise RuntimeError('connection failed')
    async def scenario():
        try: await runner.claim_batch(Api())
        except RuntimeError: return
        raise AssertionError('failed claim must propagate')
    asyncio.run(scenario())

def test_cancel_during_claim_waits_for_late_lease_and_releases():
    entered=threading.Event();respond=threading.Event();released=[]
    class Api:
        def call(self,path,body=None):
            if path.endswith('products'):
                entered.set()
                assert respond.wait(5), 'test failed to release server gate'
                return {'items':[{'productId':'late-lease','sku':'sku'}]}
            released.extend(body['product_ids'])
            return {'released':1}
    async def scenario():
        task=asyncio.create_task(runner.claim_batch(Api()))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            task.cancel()
            # This callback must run while cancellation waits for the HTTP result.
            await asyncio.sleep(0)
            task.cancel()  # repeated pause/close must not cancel the underlying call
            await asyncio.sleep(0)
        finally: respond.set()
        try: await asyncio.wait_for(task,3)
        except asyncio.CancelledError: pass
        else: raise AssertionError('cancellation must remain cancellation')
    asyncio.run(scenario())
    assert released==['late-lease']

def test_cancel_with_empty_claim_does_not_release():
    entered=threading.Event();respond=threading.Event();calls=[]
    class Api:
        def call(self,path,body=None):
            calls.append(path);entered.set()
            assert respond.wait(5)
            return {'items':[]}
    async def scenario():
        task=asyncio.create_task(runner.claim_batch(Api()))
        assert await asyncio.to_thread(entered.wait,2)
        task.cancel();await asyncio.sleep(0);respond.set()
        try: await task
        except asyncio.CancelledError: pass
    asyncio.run(scenario())
    assert calls==['/api/collector/products']

def test_cancel_cleanup_error_is_controlled(capsys):
    entered=threading.Event();respond=threading.Event()
    class Api:
        def call(self,path,body=None):
            if path.endswith('products'):
                entered.set();assert respond.wait(5)
                return {'items':[{'productId':'late-lease'}]}
            raise RuntimeError('SECRET_SENTINEL')
    async def scenario():
        task=asyncio.create_task(runner.claim_batch(Api()))
        assert await asyncio.to_thread(entered.wait,2)
        task.cancel();await asyncio.sleep(0);respond.set()
        try: await task
        except asyncio.CancelledError: pass
    asyncio.run(scenario())
    output=capsys.readouterr().out
    assert 'SECRET_SENTINEL' not in output
    assert '清理未获服务确认' in output
