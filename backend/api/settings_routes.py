"""
系统设置路由
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import SystemSettings
from services.takealot_seller_api import SECRET_SETTING, encrypt_key, configured_key
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
def seller_api_status(db: Session = Depends(get_db)):
    """Expose connection state only; never return the seller API key."""
    return {'configured': bool(configured_key(db)), 'mode': 'product_read_only'}


class SellerIdentity(BaseModel):
    name: str


@router.get('/integrations/takealot-seller/identity')
def seller_identity_status(db: Session = Depends(get_db)):
    """本店在 Takealot 的卖家名。

    调价要靠它判断 Buybox 是不是本店在持位——只比价格会把「对手同价持位」
    误判成本店持位。没填也能用：采集会按不同 SKU 数自动投票学出来。
    """
    from services import seller_identity
    from models import DEFAULT_STORE_ID
    value = seller_identity.load(db, DEFAULT_STORE_ID)
    observed = value.get('observations') if isinstance(value.get('observations'), dict) else {}
    return {
        'name': value.get('name') or '',
        'source': value.get('source') or '',
        # 每个候选名在多少个不同 SKU 上出现过——自动学习的依据，让用户能判断准不准
        'candidates': sorted(
            ({'name': (entry or {}).get('name') or key,
              'skus': len((entry or {}).get('skus') or [])}
             for key, entry in observed.items() if isinstance(entry, dict)),
            key=lambda item: -item['skus'])[:5],
    }


@router.put('/integrations/takealot-seller/identity')
def set_seller_identity(data: SellerIdentity, db: Session = Depends(get_db)):
    """手填本店卖家名（权威，自动学习不会覆盖它）。传空串表示清除。"""
    from services import seller_identity
    from models import DEFAULT_STORE_ID
    value = seller_identity.set_manual(db, DEFAULT_STORE_ID, data.name)
    return {'name': value.get('name') or '', 'source': value.get('source') or ''}


class SettingsUpdate(BaseModel):
    value: str


@router.get("/settings", response_model=dict)
def get_settings(db: Session = Depends(get_db)):
    settings = db.query(SystemSettings).all()
    return {s.key: s.value for s in settings if s.key != SECRET_SETTING and not s.key.startswith(('operations_profile:', '_operations_', '_pricing_', '_seller_identity:'))}


class SellerApiSetup(BaseModel):
    api_key: str


@router.post('/integrations/takealot-seller')
def setup_seller_api(data: SellerApiSetup, request: Request, db: Session = Depends(get_db)):
    master = os.environ.get('API_KEY', '').strip()
    if not master or not hmac.compare_digest(request.headers.get('X-API-Key', ''), master):
        raise HTTPException(403, '需要服务端访问凭证')
    if db.query(SystemSettings).filter(SystemSettings.key == SECRET_SETTING).first():
        raise HTTPException(409, '卖家密钥已配置；此入口不允许覆盖现有密钥')
    try:
        encrypted = encrypt_key(data.api_key.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.add(SystemSettings(key=SECRET_SETTING, value=encrypted))
    db.commit()
    return {'configured': True, 'mode': 'product_read_only'}


@router.patch("/settings")
def update_settings(data: dict, db: Session = Depends(get_db)):
    if any(key.startswith(('operations_profile:', '_operations_', '_pricing_')) for key in data):
        raise HTTPException(403, '经营计划请通过店铺商品专属入口修改')
    if SECRET_SETTING in data:
        raise HTTPException(403, '卖家密钥不能通过普通设置修改')
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
    if SECRET_SETTING in data:
        raise HTTPException(403, '卖家密钥不能通过普通设置修改')
    for key, value in data.items():
        setting = db.query(SystemSettings).filter(SystemSettings.key == key).first()
        if setting:
            setting.value = str(value)
        else:
            db.add(SystemSettings(key=key, value=str(value)))
    db.commit()
    return {"detail": "设置已更新"}
