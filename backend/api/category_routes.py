"""
Fee 品类与映射规则路由
"""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import FeeCategory, FeeMappingRule

router = APIRouter(prefix="/api", tags=["categories"])


# --- Fee Categories ---

class FeeCategoryOut(BaseModel):
    id: str
    name: str
    fee_rate_range: str
    success_fee_rate: float
    active: bool

    class Config:
        from_attributes = True


class FeeCategoryUpdate(BaseModel):
    name: Optional[str] = None
    fee_rate_range: Optional[str] = None
    success_fee_rate: Optional[float] = None
    active: Optional[bool] = None


@router.get("/fee-categories", response_model=List[FeeCategoryOut])
def list_fee_categories(db: Session = Depends(get_db)):
    return db.query(FeeCategory).all()


@router.patch("/fee-categories/{category_id}", response_model=FeeCategoryOut)
def update_fee_category(category_id: str, data: FeeCategoryUpdate, db: Session = Depends(get_db)):
    fc = db.query(FeeCategory).filter(FeeCategory.id == category_id).first()
    if not fc:
        raise HTTPException(status_code=404, detail="品类不存在")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(fc, field, value)

    db.commit()
    db.refresh(fc)
    return fc


# --- Fee Mapping Rules ---

class FeeMappingRuleCreate(BaseModel):
    takealot_category_pattern: Optional[str] = ""
    title_keyword_pattern: Optional[str] = ""
    fee_category: str
    priority: int = 0
    active: bool = True
    created_by_user: bool = False


class FeeMappingRuleOut(BaseModel):
    id: str
    takealot_category_pattern: Optional[str]
    title_keyword_pattern: Optional[str]
    fee_category: str
    priority: int
    active: bool
    created_by_user: bool
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class FeeMappingRuleUpdate(BaseModel):
    takealot_category_pattern: Optional[str] = None
    title_keyword_pattern: Optional[str] = None
    fee_category: Optional[str] = None
    priority: Optional[int] = None
    active: Optional[bool] = None


@router.get("/fee-mapping-rules", response_model=List[FeeMappingRuleOut])
def list_fee_mapping_rules(db: Session = Depends(get_db)):
    return db.query(FeeMappingRule).order_by(FeeMappingRule.priority.desc()).all()


@router.post("/fee-mapping-rules", response_model=FeeMappingRuleOut)
def create_fee_mapping_rule(data: FeeMappingRuleCreate, db: Session = Depends(get_db)):
    rule = FeeMappingRule(**data.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/fee-mapping-rules/{rule_id}", response_model=FeeMappingRuleOut)
def update_fee_mapping_rule(rule_id: str, data: FeeMappingRuleUpdate, db: Session = Depends(get_db)):
    rule = db.query(FeeMappingRule).filter(FeeMappingRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)

    db.commit()
    db.refresh(rule)
    return rule
