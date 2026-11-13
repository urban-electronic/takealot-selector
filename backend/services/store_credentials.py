# -*- coding: utf-8 -*-
"""按店铺隔离的 API 凭据。

## 为什么需要它

原来卖家 API 密钥和卖家门户令牌在 `system_settings` 里各只有**一行**，全局唯一。
接第二家店铺（代运营 CK）之后，两个店铺会抢同一把钥匙：

- 密钥：`configured_key(db)` 没有 store 参数，26 处调用点全拿同一把 → B 店读不到自己的报价。
- 门户令牌：24 小时过期的 JWT 也只有一行，谁后写谁生效 → A 店抓图会用 B 店的令牌。

## 怎么解决

1. 键名带上店铺：`<旧键>:<store_id>`。
2. **默认店铺双读**：新键优先，没有就回落旧键。旧键不删、不改写，回滚到旧版本还能跑。
3. 作用域解析：显式 `store_id` > 当前请求作用域 > `DEFAULT_STORE_ID`。

## 作用域是怎么来的

`main.api_key_check` 中间件从 `X-Store-ID` 头把店铺写进 ContextVar。
FastAPI 的同步路由跑在线程池里，`run_in_threadpool` 会**复制**当前 context，
所以中间件里设的值能被下游看到；而常驻后台线程没有请求 context，
会拿到默认值 `DEFAULT_STORE_ID` —— 这正是我们要的：本期后台任务只跑自营店铺。

**注意**：不要指望在同步依赖（`get_store_id`）里设这个变量。同步依赖本身也在
线程池里执行，它改的是那份副本，改完就丢了，不会传回请求。
"""
from contextvars import ContextVar

from models import DEFAULT_STORE_ID, SystemSettings

_CURRENT_STORE: ContextVar[str] = ContextVar("current_store_id", default=DEFAULT_STORE_ID)

# 旧键名（全局唯一，历史部署在用）。新键名 = 旧键名 + ':' + store_id
SELLER_KEY = "_takealot_seller_api_secret"
PORTAL_KEY = "_takealot_portal_token"


def set_current_store(store_id) -> None:
    """由请求中间件调用。传空/None 视为默认店铺。"""
    _CURRENT_STORE.set((store_id or "").strip() or DEFAULT_STORE_ID)


def current_store() -> str:
    return _CURRENT_STORE.get() or DEFAULT_STORE_ID


def _resolve(store_id) -> str:
    return ((store_id or current_store()) or DEFAULT_STORE_ID).strip() or DEFAULT_STORE_ID


def _read(db, base_key: str, store_id):
    """新键优先；默认店铺回落旧键。旧键只读，绝不改写。"""
    sid = _resolve(store_id)
    row = db.query(SystemSettings).filter(SystemSettings.key == f"{base_key}:{sid}").first()
    if row is not None:
        # An explicit empty scoped value revokes legacy fallback.
        return row.value or ""
    if sid == DEFAULT_STORE_ID:
        row = db.query(SystemSettings).filter(SystemSettings.key == base_key).first()
        if row and row.value:
            return row.value
    return ""


def _write(db, base_key: str, store_id, value: str) -> None:
    sid = _resolve(store_id)
    key = f"{base_key}:{sid}"
    row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSettings(key=key, value=value))
    db.commit()


# ---------------------------------------------------------------- 卖家 API 密钥
def seller_api_key(db, store_id=None) -> str:
    """解密后的卖家 API 密钥；没配过返回空串。"""
    from services.takealot_seller_api import _cipher
    raw = _read(db, SELLER_KEY, store_id)
    if not raw:
        return ""
    try:
        return _cipher().decrypt(raw.encode()).decode()
    except Exception:
        # 换过 master 或密文损坏：按未配置处理，不要抛给调用方
        return ""


def seller_key_configured(db, store_id=None) -> bool:
    return bool(seller_api_key(db, store_id))


def save_seller_api_key(db, store_id, key: str) -> None:
    from services.takealot_seller_api import encrypt_key
    _write(db, SELLER_KEY, store_id, encrypt_key((key or "").strip()))


def clear_seller_api_key(db, store_id) -> None:
    _write(db, SELLER_KEY, store_id, "")


# ---------------------------------------------------------------- 门户令牌
def portal_token(db, store_id=None) -> str:
    """解密后的门户 JWT；没配过返回空串。"""
    from services.takealot_portal_api import _cipher
    raw = _read(db, PORTAL_KEY, store_id)
    if not raw:
        return ""
    try:
        return _cipher().decrypt(raw.encode()).decode()
    except Exception:
        return ""


def save_portal_token(db, store_id, token: str) -> None:
    """Empty scoped values revoke credentials without reviving legacy keys."""
    from services.takealot_portal_api import _cipher
    value = (token or "").strip()
    encrypted = _cipher().encrypt(value.encode()).decode() if value else ""
    _write(db, PORTAL_KEY, store_id, encrypted)


# ---------------------------------------------------------------- 状态汇总
def credential_state(db, store_id=None) -> dict:
    """给前端/诊断看的凭据状态。**不含任何明文。**"""
    import time
    from services.takealot_portal_api import token_expiry
    sid = _resolve(store_id)
    token = portal_token(db, store_id)
    expires_at = token_expiry(token) if token else None
    now = int(time.time())
    return {
        "store_id": sid,
        "seller_api": {"configured": bool(seller_api_key(db, store_id))},
        "portal_token": {
            "configured": bool(token),
            "expires_at": expires_at,
            "expired": bool(expires_at and expires_at <= now),
            "days_left": round((expires_at - now) / 86400, 1) if expires_at else None,
        },
    }


def scope_from_request(request, store_id=None) -> str:
    """请求里的店铺归属：显式参数 > X-Store-ID 头 > 当前作用域 > 默认店铺。

    中间件已经设过 ContextVar，这里再读一次头是为了两种情况：
    一是路由被单独挂在没有中间件的 app 上（测试就是这么做的）；
    二是有人绕过中间件直接调路由。两条路给同一个答案，不依赖中间件一定存在。
    """
    if store_id:
        return _resolve(store_id)
    header = ""
    try:
        header = (request.headers.get("X-Store-ID") or "").strip() if request is not None else ""
    except Exception:
        header = ""
    return _resolve(header or None)
