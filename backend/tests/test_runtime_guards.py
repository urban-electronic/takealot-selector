"""Run-time guards from independently reproduced PR87 failures; offline only."""
import asyncio
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from api import image_proxy

URL = 'https://media.takealot.com/covers_images/review.jpg'

def test_disconnected_waiter_does_not_cancel_other_waiters():
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        async def handler(request):
            started.set()
            await finish.wait()
            return httpx.Response(200, content=b'img', headers={'content-type':'image/png'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = image_proxy._ImageFetchService(client)
            first = asyncio.create_task(service.fetch(URL))
            await started.wait()
            second = asyncio.create_task(service.fetch(URL))
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            finish.set()
            result = await asyncio.gather(second, return_exceptions=True)
            assert not isinstance(result[0], BaseException), type(result[0]).__name__
    asyncio.run(scenario())

def test_oversized_stream_stops_reading_at_limit(monkeypatch):
    monkeypatch.setattr(image_proxy, 'MAX_IMAGE_BYTES', 8)
    chunks = []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(4):
                chunks.append(i)
                yield b'123456'
    async def scenario():
        def handler(request):
            return httpx.Response(200, stream=Body(), headers={'content-type':'image/png'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = image_proxy._ImageFetchService(client)
            try:
                await service.fetch(URL)
            except HTTPException as exc:
                assert exc.status_code == 502
            else:
                raise AssertionError('oversized stream accepted')
        assert len(chunks) <= 2, f'consumed all {len(chunks)} chunks before size check'
    asyncio.run(scenario())

def test_unhandled_error_does_not_return_exception_payload(monkeypatch):
    import main
    monkeypatch.setattr(main, '_API_KEY', '')
    marker = 'FAKE_REVIEW_CANARY_DO_NOT_RETURN'
    @main.app.get('/__review_error_payload__')
    def boom():
        raise RuntimeError(marker)
    try:
        with_data = TestClient(main.app, raise_server_exceptions=False).get('/__review_error_payload__')
        assert with_data.status_code == 500
        assert marker not in with_data.text, with_data.text
    finally:
        main.app.router.routes = [r for r in main.app.router.routes if getattr(r, 'path', None) != '/__review_error_payload__']

def test_positive_cache_has_total_byte_budget_and_expires(monkeypatch):
    import time
    monkeypatch.setattr(image_proxy, 'POSITIVE_MAX_BYTES', 8)
    service = image_proxy._ImageFetchService()
    service._remember_positive(URL, b'123456', 'image/png')
    service._remember_positive(URL + '?second=1', b'123456', 'image/png')
    assert service._positive_bytes == 6 and len(service._positive) == 1
    key = next(iter(service._positive))
    old = service._positive[key]
    service._positive[key] = (time.monotonic() - 1, old[1], old[2])
    assert service._positive_hit(key) is None
    assert service._positive_bytes == 0 and not service._positive
    assert service._client is None

def test_shutdown_cancels_inflight_and_closes_client():
    async def scenario():
        started = asyncio.Event()
        async def handler(request):
            started.set()
            await asyncio.Event().wait()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = image_proxy._ImageFetchService(client)
        waiter = asyncio.create_task(service.fetch(URL))
        await started.wait()
        await service.aclose()
        await asyncio.gather(waiter, return_exceptions=True)
        assert not service._inflight and client.is_closed
        try:
            await service.fetch(URL)
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError('closed service accepted new request')
    asyncio.run(scenario())

def test_packing_repeated_unique_conflict_is_not_success(monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from sqlalchemy.exc import IntegrityError
    from api import packing_routes
    calls = []
    def conflict(db, store):
        calls.append(store)
        raise IntegrityError('insert', {}, sqlite3.IntegrityError('UNIQUE constraint failed: packing_products.sku'))
    monkeypatch.setattr(packing_routes, '_sync_from_products', conflict)
    try:
        packing_routes.sync_from_products(SimpleNamespace(rollback=lambda: None), 'test-store')
    except HTTPException as exc:
        assert exc.status_code == 409 and len(calls) == 3
    else:
        raise AssertionError('unresolved conflict reported success')

def test_packing_other_integrity_error_is_not_swallowed(monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from sqlalchemy.exc import IntegrityError
    from api import packing_routes
    calls = []
    def invalid(db, store):
        calls.append(store)
        raise IntegrityError('insert', {}, sqlite3.IntegrityError('NOT NULL constraint failed: packing_products.name'))
    monkeypatch.setattr(packing_routes, '_sync_from_products', invalid)
    try:
        packing_routes.sync_from_products(SimpleNamespace(rollback=lambda: None), 'test-store')
    except IntegrityError:
        assert len(calls) == 1
    else:
        raise AssertionError('other constraint error reported success')
