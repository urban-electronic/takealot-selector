"""
采购记录 CRUD 路由
行为对齐本地 Rust 实现 src-tauri/src/commands/procurement.rs：
- 采购记录仅做记录，不联动产品数据
- create 时校验 product_id 非空且产品存在
- unit_price = total_amount / quantity（四舍五入 2 位）
- create 时 recorded_at 使用服务端当前时间（与本地 Rust 一致，忽略前端传入）
- update 时不修改 recorded_at（与本地 Rust 一致）
"""

import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, cast, String
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import ProcurementRecord, Product
from api.store_routes import get_store_id
from services.operation_log import record_operation

router = APIRouter(prefix="/api/procurement", tags=["procurement"])


# --- Pydantic Schemas ---

class ProcurementRecordCreate(BaseModel):
    product_id: Optional[str] = None
    product_no: Optional[int] = None
    product_name: Optional[str] = ""
    quantity: int = 1
    total_amount: float = 0.0
    notes: Optional[str] = ""
    status: str = "in_transit"
    recorded_at: Optional[str] = None


class ProcurementRecordUpdate(BaseModel):
    product_id: Optional[str] = None
    product_no: Optional[int] = None
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    total_amount: Optional[float] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    recorded_at: Optional[str] = None


class ProcurementRecordOut(BaseModel):
    id: str
    product_id: Optional[str] = None
    product_no: Optional[int] = None
    product_name: str = ""
    quantity: int = 0
    total_amount: float = 0.0
    unit_price: float = 0.0
    notes: str = ""
    status: str = "received"
    status_updated_at: Optional[str] = None
    recorded_at: str = ""


class ProductBriefOut(BaseModel):
    id: str
    product_no: Optional[int] = None
    product_name: str = ""


# --- Helpers ---

def _to_out(rec: ProcurementRecord) -> ProcurementRecordOut:
    return ProcurementRecordOut(
        id=rec.id,
        product_id=rec.product_id,
        product_no=rec.product_no,
        product_name=rec.product_name or "",
        quantity=rec.quantity or 0,
        total_amount=rec.total_amount or 0.0,
        unit_price=rec.unit_price or 0.0,
        notes=rec.notes or "",
        status=rec.status or "received",
        status_updated_at=rec.status_updated_at.isoformat() if rec.status_updated_at else None,
        recorded_at=rec.recorded_at or "",
    )


def _calc_unit_price(quantity: int, total_amount: float) -> float:
    if quantity and quantity > 0:
        return round(total_amount / quantity, 2)
    return 0.0


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- Routes ---

@router.get("", response_model=List[ProcurementRecordOut])
def list_procurement_records(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    records = (
        db.query(ProcurementRecord)
        .filter(ProcurementRecord.store_id == store_id)
        .order_by(ProcurementRecord.recorded_at.desc())
        .all()
    )
    return [_to_out(r) for r in records]


def _find_product_by_no(db: Session, product_no: int, store_id: str):
    """按 product_no 查找产品，兼容远程库 INTEGER / REAL / TEXT 三种存储类型：
    - INTEGER/REAL（372 / 372.0）：数值比较命中
    - TEXT（'372'）：cast 字符串比较命中
    """
    return (
        db.query(Product)
        .filter(
            Product.store_id == store_id,
            Product.is_archived == False,
            or_(
                Product.product_no == int(product_no),
                cast(Product.product_no, String) == str(product_no),
            )
        )
        .first()
    )


@router.get("/by-no/{product_no}", response_model=List[ProductBriefOut])
def search_product_by_no(product_no: int, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """按 product_no 搜索产品（采购表单自动带出名称），对齐本地 search_products_by_no"""
    p = _find_product_by_no(db, product_no, store_id)
    if not p:
        return []
    return [ProductBriefOut(id=p.id, product_no=p.product_no, product_name=p.product_name or "")]


@router.post("", response_model=ProcurementRecordOut)
def create_procurement_record(data: ProcurementRecordCreate, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    # 校验 product_id 非空且产品存在（对齐本地 Rust create 行为）；
    # 若前端只传了 product_no（手动输入序号场景），按 product_no 反查产品填充
    product_id = data.product_id
    product = None
    if product_id:
        product = db.query(Product).filter(Product.id == product_id, Product.store_id == store_id, Product.is_archived == False).first()
        if not product:
            raise HTTPException(status_code=400, detail=f"产品不存在（id: {product_id}）")
    elif data.product_no is not None:
        product = _find_product_by_no(db, data.product_no, store_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"产品不存在（序号: {data.product_no}）")
        product_id = product.id
    else:
        raise HTTPException(status_code=400, detail="请选择产品")

    status = data.status if data.status in {"in_transit", "received", "cancelled"} else "in_transit"
    rec = ProcurementRecord(
        id=str(uuid.uuid4()),
        store_id=store_id,
        product_id=product_id,
        product_no=data.product_no if data.product_no is not None else product.product_no,
        product_name=data.product_name or (product.product_name or ""),
        quantity=data.quantity or 0,
        total_amount=data.total_amount or 0.0,
        unit_price=_calc_unit_price(data.quantity, data.total_amount),
        notes=data.notes or "",
        status=status,
        status_updated_at=datetime.utcnow(),
        recorded_at=_now_str(),
    )
    db.add(rec)
    status_label = {"in_transit": "在途", "received": "已入库", "cancelled": "已取消"}[status]
    record_operation(db, store_id, "procurement", rec.id, "create", f"新增采购：{rec.product_name} × {rec.quantity}（{status_label}）")
    db.commit()
    db.refresh(rec)
    return _to_out(rec)


@router.patch("/{record_id}", response_model=ProcurementRecordOut)
def update_procurement_record(
    record_id: str,
    data: ProcurementRecordUpdate,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    rec = db.query(ProcurementRecord).filter(ProcurementRecord.id == record_id, ProcurementRecord.store_id == store_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="采购记录不存在")

    if data.product_id is not None:
        rec.product_id = data.product_id
    if data.product_no is not None:
        rec.product_no = data.product_no
    if data.product_name is not None:
        rec.product_name = data.product_name
    if data.quantity is not None:
        rec.quantity = data.quantity
    if data.total_amount is not None:
        rec.total_amount = data.total_amount
    if data.notes is not None:
        rec.notes = data.notes
    previous_status = rec.status
    if data.status is not None:
        if data.status not in {"in_transit", "received", "cancelled"}:
            raise HTTPException(status_code=400, detail="采购状态无效")
        if data.status != rec.status:
            rec.status = data.status
            rec.status_updated_at = datetime.utcnow()
    # recorded_at 不更新（对齐本地 Rust update 行为）

    rec.unit_price = _calc_unit_price(rec.quantity, rec.total_amount)
    if rec.status != previous_status:
        status_label = {"in_transit": "在途", "received": "确认入库", "cancelled": "取消"}[rec.status]
        record_operation(db, store_id, "procurement", rec.id, "status", f"采购 {rec.product_name}：{status_label}", {
            "from": previous_status, "to": rec.status, "quantity": rec.quantity,
        })
    db.commit()
    db.refresh(rec)
    return _to_out(rec)


@router.delete("/{record_id}")
def delete_procurement_record(record_id: str, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    rec = db.query(ProcurementRecord).filter(ProcurementRecord.id == record_id, ProcurementRecord.store_id == store_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="采购记录不存在")
    record_operation(db, store_id, "procurement", rec.id, "delete", f"删除采购记录：{rec.product_name} × {rec.quantity}")
    db.delete(rec)
    db.commit()
    return "已删除"
