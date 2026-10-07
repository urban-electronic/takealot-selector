"""多店铺阶段一：店铺清单、默认店铺和请求作用域。"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import DEFAULT_STORE_ID, Store

router = APIRouter(prefix="/api/stores", tags=["stores"])


def get_store_id(request: Request, db: Session = Depends(get_db)) -> str:
    store_id = (request.headers.get("X-Store-ID") or DEFAULT_STORE_ID).strip()
    store = db.query(Store).filter(Store.id == store_id, Store.status == "active").first()
    if not store:
        raise HTTPException(status_code=400, detail="店铺不存在或已停用")
    return store.id


class StoreCreate(BaseModel):
    name: str
    owner_name: str = ""
    platform: str = "Takealot"


@router.get("")
def list_stores(db: Session = Depends(get_db)):
    rows = db.query(Store).order_by(Store.created_at.asc()).all()
    return [{
        "id": row.id, "name": row.name, "platform": row.platform,
        "status": row.status, "owner_name": row.owner_name,
        "sync_method": row.sync_method,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
    } for row in rows]


@router.post("")
def create_store(data: StoreCreate, db: Session = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="店铺名称不能为空")
    row = Store(id=str(uuid.uuid4()), name=name, owner_name=data.owner_name.strip(), platform=data.platform.strip() or "Takealot")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "platform": row.platform, "status": row.status, "owner_name": row.owner_name, "sync_method": row.sync_method, "last_synced_at": None}
