# -*- coding: utf-8 -*-
"""F11 回归（2026-10-15 C 域流畅性审计实测发现）：

POST /api/packing/sync-from-products 并发/重复调用必须幂等——
装箱单页挂载（StrictMode 双跑）、用户重试、双标签页都会触发并发同步；
修复前两个事务都通过内存 seen 检查后重复 INSERT 同一 SKU，
先提交者赢、后提交者 UNIQUE constraint failed: packing_products.sku 500，
且 500 响应不带 CORS 头被浏览器误报为 CORS 错误（F7 同型）。

本文件覆盖：顺序双调幂等、4 线程并发全成功、UNIQUE 冲突安全网、
未处理异常 500 带 CORS 头。零真实出站。
"""

import threading

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import DEFAULT_STORE_ID, PackingProduct, Product
from api.packing_routes import sync_from_products


def _engine_db(tmp_path, name):
    engine = create_engine(f'sqlite:///{tmp_path}/{name}.db', connect_args={'timeout': 30})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_catalog(Session):
    db = Session()
    db.add(Product(id='p1', store_id=DEFAULT_STORE_ID, sku='SKU-1',
                   product_name='测试产品', chinese_product_name='测试产品',
                   actual_weight_kg=0.5, is_archived=False))
    db.commit()
    db.close()


def test_sequential_double_sync_is_idempotent(tmp_path):
    """顺序重复同步：第二次不再插入、不报错，计数保持一致。"""
    Session = _engine_db(tmp_path, 'seq')
    _seed_catalog(Session)
    first = sync_from_products(db=Session(), store_id=DEFAULT_STORE_ID)
    assert first['ok'] is True and first['imported'] == 1
    second = sync_from_products(db=Session(), store_id=DEFAULT_STORE_ID)
    assert second['ok'] is True and second['imported'] == 0
    assert second['packing_products'] == first['packing_products'] == 1
    assert Session().query(PackingProduct).count() == 1


def test_concurrent_sync_calls_all_succeed(tmp_path):
    """4 线程并发同步（复现 StrictMode 双跑 / 双击 / 双标签页）：
    全部成功返回且不重复插入。修复前至少一个线程 IntegrityError 500。"""
    Session = _engine_db(tmp_path, 'conc')
    _seed_catalog(Session)
    results, errors = {}, []

    def worker(i):
        try:
            results[i] = sync_from_products(db=Session(), store_id=DEFAULT_STORE_ID)
        except Exception as e:  # 记录到断言，避免线程异常被静默吞掉
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f'并发同步抛错: {errors}'
    assert len(results) == 4 and all(r['ok'] for r in results.values())
    assert Session().query(PackingProduct).count() == 1


def test_integrity_conflict_absorbed_as_already_synced(tmp_path):
    """跨进程/跨事务竞态的安全网（多 worker 部署）：本会话基于“尚不存在”的
    过期快照仍要 INSERT 同一 SKU，commit 必然 UNIQUE 冲突——修复前以 500
    逃逸，修复后回滚并按“已同步”返回，不残留重复行。"""
    Session = _engine_db(tmp_path, 'conflict')
    _seed_catalog(Session)
    db = Session(autoflush=False)  # 与生产 SessionLocal 一致：挂起行对本会话查询不可见
    db.add(PackingProduct(sku='SKU-1', name='stale'))
    result = sync_from_products(db=db, store_id=DEFAULT_STORE_ID)
    assert result['ok'] is True and result['imported'] == 0
    assert Session().query(PackingProduct).count() == 0  # 冲突双方均已回滚，无重复行


def test_unhandled_error_keeps_cors_headers(monkeypatch):
    """未处理异常的 500 也带 CORS 头：浏览器不再把真实 500 误报为 CORS 错误。"""
    import main
    monkeypatch.setattr(main, '_API_KEY', '')

    @main.app.get('/__f11_test_boom__')
    def _boom():
        raise RuntimeError('boom-for-cors-test')

    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.get('/__f11_test_boom__', headers={'Origin': 'http://127.0.0.1:5173'})
    finally:
        main.app.router.routes = [r for r in main.app.router.routes
                                  if getattr(r, 'path', None) != '/__f11_test_boom__']
    assert resp.status_code == 500
    assert resp.headers['access-control-allow-origin'] == 'http://127.0.0.1:5173'
    assert 'RuntimeError' in resp.json()['detail']
