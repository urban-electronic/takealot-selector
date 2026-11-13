"""
系统设置路由
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import SystemSettings
from services.takealot_seller_api import SECRET_SETTING, configured_key
from fastapi import Request
import hmac
import os

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsOut(BaseModel):
    id: str
    key: str
    value: str

    class Config:
        from_attributes = True


@router.get('/integrations/takealot-seller/status')
def seller_api_status(db: Session = Depends(get_db), request: Request = None):
    """Expose connection state only; never return the seller API key.

    多店铺（STORE-CRED-01）：状态按请求店铺返回。响应结构保持不变——
    前端已经用 X-Store-ID 指明是哪个店铺，不需要再回一个 store_id。
    """
    from services import store_credentials
    # request 放在末尾且可空：有测试直接调这个函数、只传 db；
    # 生产请求由 FastAPI 注入 Request，即便注入不到也有中间件设好的作用域兜底。
    store_id = store_credentials.scope_from_request(request)
    return {'configured': bool(configured_key(db, store_id)), 'mode': 'product_read_only'}


class SellerIdentity(BaseModel):
    name: str


@router.get('/integrations/takealot-seller/identity')
def seller_identity_status(db: Session = Depends(get_db), request: Request = None):
    """本店在 Takealot 的卖家名。

    调价要靠它判断 Buybox 是不是本店在持位——只比价格会把「对手同价持位」
    误判成本店持位。没填也能用：采集会按不同 SKU 数自动投票学出来。
    """
    from services import seller_identity
    from services import store_credentials
    store_id = store_credentials.scope_from_request(request)
    value = seller_identity.load(db, store_id)
    observed = value.get('observations') if isinstance(value.get('observations'), dict) else {}
    return {
        'name': seller_identity.name_of(db, store_id),
        'source': 'manual' if value.get('source') == 'manual' else 'candidate' if value.get('name') else '',
        # 每个候选名在多少个不同 SKU 上出现过——自动学习的依据，让用户能判断准不准
        'candidates': sorted(
            ({'name': (entry or {}).get('name') or key,
              'skus': len((entry or {}).get('skus') or [])}
             for key, entry in observed.items() if isinstance(entry, dict)),
            key=lambda item: -item['skus'])[:5],
    }


@router.put('/integrations/takealot-seller/identity')
def set_seller_identity(data: SellerIdentity, db: Session = Depends(get_db), request: Request = None):
    """手填本店卖家名（权威，自动学习不会覆盖它）。传空串表示清除。

    多店铺（STORE-CRED-01）：按请求店铺写入，B 店的名字不会覆盖 A 店。
    """
    from services import seller_identity
    from services import store_credentials
    store_id = store_credentials.scope_from_request(request)
    value = seller_identity.set_manual(db, store_id, data.name)
    return {'name': value.get('name') or '', 'source': value.get('source') or ''}


class SettingsUpdate(BaseModel):
    value: str


@router.get("/settings", response_model=dict)
def get_settings(db: Session = Depends(get_db)):
    settings = db.query(SystemSettings).all()
    from services.store_credentials import PORTAL_KEY, SELLER_KEY
    # 凭据键一律不外泄：卖家密钥、门户令牌，以及它们的按店前缀形式
    hidden = (SECRET_SETTING, SELLER_KEY, PORTAL_KEY)
    return {s.key: s.value for s in settings
            if not s.key.startswith(hidden)
            and not s.key.startswith(('operations_profile:', '_operations_', '_pricing_', '_seller_identity:'))}


class SellerApiSetup(BaseModel):
    api_key: str


@router.post('/integrations/takealot-seller')
def setup_seller_api(data: SellerApiSetup, request: Request, db: Session = Depends(get_db)):
    master = os.environ.get('API_KEY', '').strip()
    if not master or not hmac.compare_digest(request.headers.get('X-API-Key', ''), master):
        raise HTTPException(403, '需要服务端访问凭证')
    from services import store_credentials
    store_id = store_credentials.scope_from_request(request)
    # 按店铺判重：A 店配过不再允许覆盖，但 B 店仍然可以配自己的
    if store_credentials.seller_key_configured(db, store_id):
        raise HTTPException(409, '该店铺的卖家密钥已配置；此入口不允许覆盖现有密钥')
    try:
        store_credentials.save_seller_api_key(db, store_id, data.api_key.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {'configured': True, 'mode': 'product_read_only'}


@router.put('/integrations/takealot-seller')
def replace_seller_api(data: SellerApiSetup, request: Request, db: Session = Depends(get_db)):
    """替换**当前店铺**的卖家密钥（需要服务端访问凭证）。

    POST 是首次配置，配过就 409；可密钥填错了必须能改，所以单独开一个 PUT。
    仍然要求服务端访问密钥，且只影响请求头指定的那个店铺。
    """
    master = os.environ.get('API_KEY', '').strip()
    if not master or not hmac.compare_digest(request.headers.get('X-API-Key', ''), master):
        raise HTTPException(403, '需要服务端访问凭证')
    from services import store_credentials
    store_id = store_credentials.scope_from_request(request)
    try:
        store_credentials.save_seller_api_key(db, store_id, data.api_key.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {'configured': True, 'mode': 'product_read_only'}


@router.patch("/settings")
def update_settings(data: dict, db: Session = Depends(get_db)):
    if any(key.startswith(('operations_profile:', '_operations_', '_pricing_')) for key in data):
        raise HTTPException(403, '经营计划请通过店铺商品专属入口修改')
    if any(k.startswith((SECRET_SETTING, '_takealot_portal_token')) for k in data):
        raise HTTPException(403, '卖家密钥与门户令牌不能通过普通设置修改')
    for key, value in data.items():
        setting = db.query(SystemSettings).filter(SystemSettings.key == key).first()
        if setting:
            setting.value = str(value)
        else:
            db.add(SystemSettings(key=key, value=str(value)))
    db.commit()
    return {"detail": "设置已更新"}


@router.put("/settings")
def update_settings_put(data: dict, db: Session = Depends(get_db)):
    if any(key.startswith(('operations_profile:', '_operations_', '_pricing_')) for key in data):
        raise HTTPException(403, '经营计划请通过店铺商品专属入口修改')
    if any(k.startswith((SECRET_SETTING, '_takealot_portal_token')) for k in data):
        raise HTTPException(403, '卖家密钥与门户令牌不能通过普通设置修改')
    for key, value in data.items():
        setting = db.query(SystemSettings).filter(SystemSettings.key == key).first()
        if setting:
            setting.value = str(value)
        else:
            db.add(SystemSettings(key=key, value=str(value)))
    db.commit()
    return {"detail": "设置已更新"}
