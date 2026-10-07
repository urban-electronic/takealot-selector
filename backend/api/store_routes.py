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
    external_store_ref: str = ""
    sync_method: str = "manual"
    sync_interval_minutes: int = 60


class StoreUpdate(BaseModel):
    name: str | None = None
    owner_name: str | None = None
    platform: str | None = None
    status: str | None = None
    external_store_ref: str | None = None
    sync_method: str | None = None
    sync_interval_minutes: int | None = None


def serialize_store(row: Store):
    return {
        "id": row.id, "name": row.name, "platform": row.platform,
        "status": row.status, "owner_name": row.owner_name,
        "sync_method": row.sync_method,
        "sync_interval_minutes": row.sync_interval_minutes or 60,
        "external_store_ref": row.external_store_ref or "",
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
    }


@router.get("")
def list_stores(db: Session = Depends(get_db)):
    rows = db.query(Store).order_by(Store.created_at.asc()).all()
    return [serialize_store(row) for row in rows]


@router.post("")
def create_store(data: StoreCreate, db: Session = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="店铺名称不能为空")
    if data.sync_method not in {"manual", "api", "import"}:
        raise HTTPException(status_code=400, detail="不支持的同步方式")
    if data.sync_interval_minutes < 15:
        raise HTTPException(status_code=400, detail="同步间隔不能少于 15 分钟")
    row = Store(
        id=str(uuid.uuid4()), name=name, owner_name=data.owner_name.strip(),
        platform=data.platform.strip() or "Takealot",
        external_store_ref=data.external_store_ref.strip(), sync_method=data.sync_method,
        sync_interval_minutes=data.sync_interval_minutes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_store(row)


@router.patch("/{store_id}")
def update_store(store_id: str, data: StoreUpdate, db: Session = Depends(get_db)):
    row = db.query(Store).filter(Store.id == store_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="店铺不存在")
    values = data.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = (values["name"] or "").strip()
        if not values["name"]:
            raise HTTPException(status_code=400, detail="店铺名称不能为空")
    if values.get("status") not in {None, "active", "paused"}:
        raise HTTPException(status_code=400, detail="店铺状态不正确")
    if store_id == DEFAULT_STORE_ID and values.get("status") == "paused":
        raise HTTPException(status_code=400, detail="默认店铺不能停用")
    if values.get("sync_method") not in {None, "manual", "api", "import"}:
        raise HTTPException(status_code=400, detail="不支持的同步方式")
    if values.get("sync_interval_minutes") is not None and values["sync_interval_minutes"] < 15:
        raise HTTPException(status_code=400, detail="同步间隔不能少于 15 分钟")
    for field in ("owner_name", "platform", "external_store_ref"):
        if field in values:
            values[field] = (values[field] or "").strip()
    for key, value in values.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return serialize_store(row)
