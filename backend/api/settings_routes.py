"""
系统设置路由
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import SystemSettings

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsOut(BaseModel):
    id: str
    key: str
    value: str

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    value: str


@router.get("/settings", response_model=dict)
def get_settings(db: Session = Depends(get_db)):
    settings = db.query(SystemSettings).all()
    return {s.key: s.value for s in settings}


@router.patch("/settings")
def update_settings(data: dict, db: Session = Depends(get_db)):
    for key, value in data.items():
        setting = db.query(SystemSettings).filter(SystemSettings.key == key).first()
        if setting:
            setting.value = str(value)
        else:
            db.add(SystemSettings(key=key, value=str(value)))
    db.commit()
    return {"detail": "设置已更新"}
