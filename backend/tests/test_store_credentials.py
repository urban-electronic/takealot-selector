# -*- coding: utf-8 -*-
"""多店铺凭据隔离（STORE-CRED-01）。

验证四件事：
1. 默认店铺仍能读到旧的全局键（存量部署零回归、可回滚）。
2. 两个店铺各用各的卖家密钥 / 门户令牌，互不覆盖。
3. 没配凭据的店铺返回空，**不回落**到别的店铺（防串店）。
4. 请求中间件能把 X-Store-ID 传到凭据层（ContextVar 要穿过线程池）。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import SystemSettings
from api import settings_routes
from services import store_credentials as creds
from services import takealot_seller_api as seller

KEY_A = 'a' * 128
KEY_B = 'b' * 128
MASTER = 'test-application-access-key-' + 'x' * 32
TOKEN_A = 'eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.sigA'
TOKEN_B = 'eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjE4ODg4ODg4ODh9.sigB'
STORE_A = 'default-store'
STORE_B = 'store-b'


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv('API_KEY', MASTER)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def db():
        with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_db] = db
    return app, factory


def headers(store=None):
    head = {'X-API-Key': MASTER}
    if store:
        head['X-Store-ID'] = store
    return head


# ---------------------------------------------------------------- 回归
def test_legacy_global_key_still_read_for_default_store(env):
    """老部署只有一行全局键，升级后默认店铺必须照常读到。"""
    _, factory = env
    with factory() as db:
        db.add(SystemSettings(key=seller.SECRET_SETTING,
                              value=seller.encrypt_key(KEY_A)))
        db.commit()
        assert creds.seller_api_key(db, STORE_A) == KEY_A
        # 旧键还在，没被改写也没被删
        row = db.query(SystemSettings).filter(
            SystemSettings.key == seller.SECRET_SETTING).first()
        assert row is not None


def test_unconfigured_store_does_not_fall_back_to_another_store(env):
    """最关键的一条：B 店没配钥匙时必须是空，不能拿到 A 店的。"""
    _, factory = env
    with factory() as db:
        db.add(SystemSettings(key=seller.SECRET_SETTING,
                              value=seller.encrypt_key(KEY_A)))
        db.commit()
        assert creds.seller_api_key(db, STORE_A) == KEY_A
        assert creds.seller_api_key(db, STORE_B) == ''
        assert creds.seller_api_key(db, 'store-never-configured') == ''


# ---------------------------------------------------------------- 隔离
def test_two_stores_keep_their_own_seller_key(env):
    app, factory = env
    client = TestClient(app)
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(), json={'api_key': KEY_A}).status_code == 200
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(STORE_B), json={'api_key': KEY_B}).status_code == 200

    with factory() as db:
        assert creds.seller_api_key(db, STORE_A) == KEY_A
        assert creds.seller_api_key(db, STORE_B) == KEY_B


def test_setup_endpoint_is_per_store(env):
    """A 店配过不再允许覆盖，但 B 店仍能配自己的（原来这里是全局 409）。"""
    app, _ = env
    client = TestClient(app)
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(), json={'api_key': KEY_A}).status_code == 200
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(), json={'api_key': KEY_A}).status_code == 409
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(STORE_B), json={'api_key': KEY_B}).status_code == 200


def test_status_follows_store_header(env):
    app, _ = env
    client = TestClient(app)
    client.post('/api/integrations/takealot-seller', headers=headers(), json={'api_key': KEY_A})

    assert client.get('/api/integrations/takealot-seller/status',
                      headers=headers()).json()['configured'] is True
    assert client.get('/api/integrations/takealot-seller/status',
                      headers=headers(STORE_B)).json()['configured'] is False


def test_portal_token_isolated_per_store(env):
    _, factory = env
    with factory() as db:
        creds.save_portal_token(db, STORE_A, TOKEN_A)
        creds.save_portal_token(db, STORE_B, TOKEN_B)
        assert creds.portal_token(db, STORE_A) == TOKEN_A
        assert creds.portal_token(db, STORE_B) == TOKEN_B
        # 空串 = 删行，只影响本店
        creds.save_portal_token(db, STORE_B, '')
        assert creds.portal_token(db, STORE_B) == ''
        assert creds.portal_token(db, STORE_A) == TOKEN_A


def test_identity_written_per_store(env):
    """B 店填卖家名不能写进 A 店。"""
    app, factory = env
    client = TestClient(app)
    client.put('/api/integrations/takealot-seller/identity',
               headers=headers(), json={'name': 'OwnStoreName'})
    client.put('/api/integrations/takealot-seller/identity',
               headers=headers(STORE_B), json={'name': 'AgencyName'})

    from services import seller_identity
    with factory() as db:
        assert seller_identity.name_of(db, STORE_A) == 'OwnStoreName'
        assert seller_identity.name_of(db, STORE_B) == 'AgencyName'


# ---------------------------------------------------------------- 安全
def test_credentials_never_exposed_by_settings(env):
    app, factory = env
    client = TestClient(app)
    client.post('/api/integrations/takealot-seller', headers=headers(), json={'api_key': KEY_A})
    with factory() as db:
        creds.save_portal_token(db, STORE_A, TOKEN_A)

    payload = client.get('/api/settings').json()
    joined = ' '.join(payload.keys())
    assert seller.SECRET_SETTING not in joined
    assert creds.SELLER_KEY not in joined
    assert creds.PORTAL_KEY not in joined
    assert KEY_A not in str(payload)


def test_settings_cannot_overwrite_credentials(env):
    app, _ = env
    client = TestClient(app)
    for method in ('patch', 'put'):
        for key in (seller.SECRET_SETTING, f'{creds.SELLER_KEY}:{STORE_B}',
                    f'{creds.PORTAL_KEY}:{STORE_B}'):
            resp = getattr(client, method)('/api/settings', json={key: 'x'})
            assert resp.status_code == 403, (method, key)


# ---------------------------------------------------------------- 作用域穿透
def test_middleware_carries_store_into_credential_layer(env):
    """ContextVar 必须穿过 async 中间件 → 线程池，否则按店取凭据会静默失效。"""
    app = FastAPI()

    @app.middleware('http')
    async def store_scope(request, call_next):
        creds.set_current_store(request.headers.get('X-Store-ID') or '')
        return await call_next(request)

    @app.get('/probe')
    def probe():
        # 同步路由，跑在线程池里——正是生产代码的形态
        return {'store': creds.current_store()}

    client = TestClient(app)
    assert client.get('/probe').json()['store'] == STORE_A
    assert client.get('/probe', headers={'X-Store-ID': STORE_B}).json()['store'] == STORE_B


# ---------------------------------------------------------------- 替换密钥
def test_replace_endpoint_updates_only_current_store(env):
    """填错了要能改：PUT 覆盖本店，不碰别店；POST 仍然拒绝覆盖。"""
    app, factory = env
    client = TestClient(app)
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(), json={'api_key': KEY_A}).status_code == 200
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(STORE_B), json={'api_key': KEY_B}).status_code == 200

    new_b = 'c' * 128
    assert client.put('/api/integrations/takealot-seller',
                      headers=headers(STORE_B), json={'api_key': new_b}).status_code == 200

    with factory() as db:
        assert creds.seller_api_key(db, STORE_B) == new_b
        assert creds.seller_api_key(db, STORE_A) == KEY_A
    # POST 仍然不允许覆盖已配置的店铺
    assert client.post('/api/integrations/takealot-seller',
                       headers=headers(STORE_B), json={'api_key': KEY_B}).status_code == 409


def test_replace_endpoint_requires_server_key(env):
    app, factory = env
    client = TestClient(app)
    resp = client.put('/api/integrations/takealot-seller',
                      headers={'X-Store-ID': STORE_B}, json={'api_key': KEY_B})
    assert resp.status_code == 403
    with factory() as db:
        assert creds.seller_api_key(db, STORE_B) == ''


def test_replace_endpoint_validates_key_shape(env):
    app, _ = env
    client = TestClient(app)
    resp = client.put('/api/integrations/takealot-seller',
                      headers=headers(STORE_B), json={'api_key': 'too-short'})
    assert resp.status_code == 400


# ---------------------------------------------------------------- 并发 / 旧响应
def _file_backed_factory(tmp_path):
    """并发用例必须用文件库。

    上面的 `env` 夹具用 `StaticPool` + 内存库，全部线程共用一个 sqlite 连接；
    并发下 cursor 会互相踩（`InterfaceError: bad parameter or other API misuse`）。
    那是夹具的限制，不是产品缺陷——真实部署是 PostgreSQL/连接池，每个请求各拿一条。
    换成文件库就能让每个线程各开一条连接，测出来的才是产品行为。
    """
    engine = create_engine(f'sqlite:///{tmp_path / "concurrent.db"}',
                           connect_args={'check_same_thread': False, 'timeout': 10})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _store_with_keys(factory):
    """两店各配一把钥匙，供并发用例读。"""
    with factory() as db:
        db.add(SystemSettings(key=f'{creds.SELLER_KEY}:{STORE_A}',
                              value=seller.encrypt_key(KEY_A)))
        db.add(SystemSettings(key=f'{creds.SELLER_KEY}:{STORE_B}',
                              value=seller.encrypt_key(KEY_B)))
        db.commit()


def test_concurrent_requests_do_not_cross_stores(tmp_path, monkeypatch):
    """两店并发 + 旧响应晚到：每个响应必须是它自己店铺的钥匙。

    按生产形态搭：async 中间件写作用域 → 同步路由跑在线程池（会复用线程）。
    A 店的请求故意更慢，所以「先发起、后返回」的旧响应真的会排在 B 店响应之后。
    如果作用域是线程局部或者进程全局，这里必然串店。
    """
    import asyncio

    import httpx

    monkeypatch.setenv('API_KEY', MASTER)
    factory = _file_backed_factory(tmp_path)
    _store_with_keys(factory)

    app = FastAPI()

    @app.middleware('http')
    async def store_scope(request, call_next):
        creds.set_current_store(request.headers.get('X-Store-ID') or '')
        # 制造乱序返回：A 慢、B 快
        await asyncio.sleep(0.02 if creds.current_store() == STORE_A else 0)
        return await call_next(request)

    @app.get('/probe')
    def probe():
        with factory() as db:
            return {'store': creds.current_store(), 'key': creds.seller_api_key(db)}

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            calls = []
            for i in range(24):
                store = STORE_A if i % 2 == 0 else STORE_B
                calls.append(client.get('/probe', headers={'X-Store-ID': store}))
            return await asyncio.gather(*calls)

    responses = asyncio.run(scenario())
    assert len(responses) == 24
    for i, resp in enumerate(responses):
        store = STORE_A if i % 2 == 0 else STORE_B
        expected_key = KEY_A if store == STORE_A else KEY_B
        body = resp.json()
        assert body['store'] == store, f'第 {i} 个请求串店了：{body}'
        assert body['key'] == expected_key, f'第 {i} 个请求拿到了别店的钥匙'


def test_late_response_for_store_a_still_reports_store_a(tmp_path, monkeypatch):
    """旧响应本身的内容不能变。

    切店铺只是让前端丢弃它，服务端不能因为「现在切到 B 了」就把 A 的响应改写成 B 的
    —— 那会让审计日志和排查都失真。
    """
    import asyncio

    import httpx

    monkeypatch.setenv('API_KEY', MASTER)
    factory = _file_backed_factory(tmp_path)
    _store_with_keys(factory)
    app = FastAPI()

    started = asyncio.Event()
    release = asyncio.Event()
    completed = []

    @app.middleware('http')
    async def store_scope(request, call_next):
        creds.set_current_store(request.headers.get('X-Store-ID') or '')
        if creds.current_store() == STORE_A:
            started.set()
            await release.wait()
        response = await call_next(request)
        completed.append(creds.current_store())
        return response

    @app.get('/probe')
    def probe():
        with factory() as db:
            return {'store': creds.current_store(), 'key': creds.seller_api_key(db)}

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            slow = asyncio.create_task(client.get('/probe', headers={'X-Store-ID': STORE_A}))
            await asyncio.wait_for(started.wait(), timeout=2)
            fast = await client.get('/probe', headers={'X-Store-ID': STORE_B})
            assert not slow.done()
            release.set()
            result = await asyncio.wait_for(slow, timeout=2)
            assert completed == [STORE_B, STORE_A]
            return result, fast

    slow_resp, fast_resp = asyncio.run(scenario())
    assert fast_resp.json() == {'store': STORE_B, 'key': KEY_B}
    # A 的响应仍然是 A 的，没有因为「当前店铺已经是 B」而被改写
    assert slow_resp.json() == {'store': STORE_A, 'key': KEY_A}

@pytest.mark.parametrize('kind', ['seller', 'portal'])
@pytest.mark.parametrize('replace_first', [False, True])
def test_default_clear_revokes_legacy_fallback(env, kind, replace_first):
    _, factory = env
    from services.takealot_portal_api import _cipher
    base = creds.SELLER_KEY if kind == 'seller' else creds.PORTAL_KEY
    legacy = KEY_A if kind == 'seller' else TOKEN_A
    with factory() as db:
        encrypted = seller.encrypt_key(legacy) if kind == 'seller' else _cipher().encrypt(legacy.encode()).decode()
        db.add(SystemSettings(key=base, value=encrypted)); db.commit()
        if replace_first:
            if kind == 'seller': creds.save_seller_api_key(db, STORE_A, KEY_B)
            else: creds.save_portal_token(db, STORE_A, TOKEN_B)
        if kind == 'seller': creds.clear_seller_api_key(db, STORE_A)
        else: creds.save_portal_token(db, STORE_A, '')
        read = creds.seller_api_key if kind == 'seller' else creds.portal_token
        assert read(db, STORE_A) == ''
        assert db.query(SystemSettings).filter(SystemSettings.key == base).first().value == encrypted
        # Repeated clears stay revoked; explicit reconfiguration restores only this store.
        if kind == 'seller':
            creds.clear_seller_api_key(db, STORE_A)
            assert read(db, STORE_A) == ''
            creds.save_seller_api_key(db, STORE_A, KEY_B)
            assert read(db, STORE_A) == KEY_B
        else:
            creds.save_portal_token(db, STORE_A, '')
            assert read(db, STORE_A) == ''
            creds.save_portal_token(db, STORE_A, TOKEN_B)
            assert read(db, STORE_A) == TOKEN_B
