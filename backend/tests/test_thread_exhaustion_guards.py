"""线程耗尽的回归测试（2026-10-21 线上：/api/image-proxy 全线 500）。

三条防线各自都要能被单独验证：
  1. 采集超时必须由协程自己取消，不能留给看门狗「放弃一个取消不了的线程」；
  2. 等锁不能再借线程（线程本身就是当时被耗尽的资源）；
  3. 资源不足时代理要报 502（前端走直连兜底），而不是冒泡成 500。
"""
import asyncio
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import diag_routes, image_proxy
from services import buybox_monitor, quote_browser, runtime_health


def test_collect_timeout_cancels_coroutine_and_releases_lock(monkeypatch):
    """卡住的采集要被就地取消：抛 CollectTimeout，且浏览器锁必须放回去。

    锁是关键——它一旦被卡死的任务带走，后续每一轮都只能报 collector_busy，
    而那个线程还占着 Chromium 和 node 驱动。
    """
    started = threading.Event()

    async def slow_collect(product, own, seller_name=None):
        started.set()
        await asyncio.sleep(3600)  # 模拟浏览器卡死
        return {'buyboxPrice': 1}

    monkeypatch.setattr(buybox_monitor, 'collect', slow_collect)
    monkeypatch.setattr(buybox_monitor, 'COLLECT_TIMEOUT', 0.3)

    began = time.time()
    with pytest.raises(buybox_monitor.CollectTimeout):
        buybox_monitor._collect_with_retry(object(), {'sku': 'x'}, {})
    elapsed = time.time() - began

    assert started.is_set()
    assert elapsed < 5, f'超时没有及时生效，用了 {elapsed:.1f}s'
    # 锁必须回到可用状态：模拟的任务根本没拿到锁，但同一路径下也不能被占死。
    assert quote_browser._lock.acquire(blocking=False)
    quote_browser._lock.release()


def test_collect_timeout_is_not_retried(monkeypatch):
    """超时不重试：能卡满上限的任务，再来一次只会再占一个浏览器。"""
    calls = []

    async def slow_collect(product, own, seller_name=None):
        calls.append(1)
        await asyncio.sleep(3600)

    monkeypatch.setattr(buybox_monitor, 'collect', slow_collect)
    monkeypatch.setattr(buybox_monitor, 'COLLECT_TIMEOUT', 0.2)

    with pytest.raises(buybox_monitor.CollectTimeout):
        buybox_monitor._collect_with_retry(object(), {'sku': 'x'}, {})
    assert len(calls) == 1


def test_lock_wait_does_not_borrow_a_thread():
    """等锁走异步睡眠，不占线程池——这正是当时把线程耗光的调用路径。"""
    before = threading.active_count()

    async def scenario():
        assert await quote_browser._acquire_lock(0.3) is True
        quote_browser._lock.release()

    asyncio.run(scenario())
    assert threading.active_count() <= before + 1


def test_runtime_snapshot_reports_threads_and_pids():
    data = runtime_health.snapshot()
    assert data['threads'] >= 1
    assert data['threads_warn'] == runtime_health.THREAD_WARN
    assert sum(data['threads_by_name'].values()) == data['threads']
    # pids 上限在非 cgroup 环境（如本地 Windows）读不到，允许 None，但不能报错。
    assert 'pids_max' in data and 'process_count' in data


def test_diag_runtime_endpoint_is_readable():
    app = FastAPI()
    app.include_router(diag_routes.router)
    resp = TestClient(app).get('/api/diag/runtime')
    assert resp.status_code == 200
    body = resp.json()
    assert body['threads'] >= 1 and 'pids_current' in body


def test_image_proxy_reports_502_when_threads_are_exhausted(monkeypatch):
    """线程起不来时返回 502 而不是 500：前端据此走直连兜底。"""
    class BrokenService:
        async def fetch(self, url):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(image_proxy, '_service', BrokenService())
    monkeypatch.setattr(image_proxy, '_API_KEY', '')

    app = FastAPI()
    app.include_router(image_proxy.router)
    resp = TestClient(app).get(
        '/api/image-proxy',
        params={'url': 'https://media.takealot.com/covers_images/x.jpg'})

    assert resp.status_code == 502
    assert '资源不足' in resp.json()['detail']
