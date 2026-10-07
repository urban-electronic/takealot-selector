# -*- coding: utf-8 -*-
"""库存余额、人工调整和发货历史。"""

import datetime as dt
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import (
    InventoryAdjustment, PackingProduct, ProcurementRecord, Product,
    Shipment, ShipmentLine, Store, SystemSettings,
)
from services.inbound_excel import generate_inbound
from api.store_routes import get_store_id

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


def _start_date(db: Session) -> str:
    setting = db.query(SystemSettings).filter(SystemSettings.key == "inventory_start_date").first()
    return setting.value if setting and setting.value else "2026-09-24"


def _procurement_by_product(db: Session, store_id: str) -> dict:
    start_date = _start_date(db)
    result = {}
    for product_id, quantity in (
        db.query(ProcurementRecord.product_id, func.coalesce(func.sum(ProcurementRecord.quantity), 0))
        .filter(ProcurementRecord.store_id == store_id)
        .filter(ProcurementRecord.product_id.isnot(None))
        .filter(ProcurementRecord.recorded_at >= start_date)
        .group_by(ProcurementRecord.product_id)
        .all()
    ):
        result[product_id] = int(quantity or 0)
    return result


def _find_product_for_sku(db: Session, sku: str, store_id: str):
    exact = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False, Product.sku == sku).first()
    if exact:
        return exact, False
    for product in db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False, Product.sku.isnot(None)).all():
        variants = [x for x in re.split(r'[\s,，;；]+', (product.sku or '').strip()) if x]
        if len(variants) > 1 and sku in variants:
            return product, True
    return None, False


def inventory_balance_for_sku(db: Session, sku: str, store_id: str) -> int:
    start_date = _start_date(db)
    product, is_variant = _find_product_for_sku(db, sku, store_id)
    purchased = 0
    if product and not is_variant:
        purchased = int(
            db.query(func.coalesce(func.sum(ProcurementRecord.quantity), 0))
            .filter(ProcurementRecord.store_id == store_id)
            .filter(
                (ProcurementRecord.product_id == product.id)
                | ((ProcurementRecord.product_id.is_(None)) & (ProcurementRecord.product_no == product.product_no))
            )
            .filter(ProcurementRecord.recorded_at >= start_date)
            .scalar() or 0
        )
    adjusted = int(
        db.query(func.coalesce(func.sum(InventoryAdjustment.quantity_delta), 0))
        .filter(InventoryAdjustment.sku == sku, InventoryAdjustment.store_id == store_id)
        .filter(InventoryAdjustment.occurred_at >= start_date)
        .scalar() or 0
    )
    shipped = int(
        db.query(func.coalesce(func.sum(ShipmentLine.total_quantity), 0))
        .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
        .filter(ShipmentLine.sku == sku, Shipment.status == "confirmed", Shipment.store_id == store_id)
        .filter(Shipment.shipment_date >= start_date)
        .scalar() or 0
    )
    return purchased + adjusted - shipped


@router.get("")
def list_inventory(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    start_date = _start_date(db)
    products = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False).order_by(Product.product_no.asc()).all()
    purchased_by_id = _procurement_by_product(db, store_id)
    purchased_by_no = dict(
        db.query(ProcurementRecord.product_no, func.coalesce(func.sum(ProcurementRecord.quantity), 0))
        .filter(ProcurementRecord.store_id == store_id, ProcurementRecord.product_id.is_(None), ProcurementRecord.product_no.isnot(None))
        .filter(ProcurementRecord.recorded_at >= start_date)
        .group_by(ProcurementRecord.product_no).all()
    )
    adjusted_by_sku = dict(
        db.query(InventoryAdjustment.sku, func.coalesce(func.sum(InventoryAdjustment.quantity_delta), 0))
        .filter(InventoryAdjustment.store_id == store_id, InventoryAdjustment.occurred_at >= start_date)
        .group_by(InventoryAdjustment.sku).all()
    )
    shipped_by_sku = dict(
        db.query(ShipmentLine.sku, func.coalesce(func.sum(ShipmentLine.total_quantity), 0))
        .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
        .filter(Shipment.status == "confirmed", Shipment.store_id == store_id)
        .filter(Shipment.shipment_date >= start_date)
        .group_by(ShipmentLine.sku).all()
    )
    rows = []
    seen_skus = set()
    for product in products:
        raw_sku = (product.sku or "").strip()
        variants = [x for x in re.split(r'[\s,，;；]+', raw_sku) if x] or [""]
        zh_names = [x for x in re.split(r'\s{1,}', (product.chinese_product_name or "").strip()) if x]
        for idx, sku in enumerate(variants):
            if sku and sku in seen_skus:
                continue
            if sku:
                seen_skus.add(sku)
            # 多颜色历史记录无法可靠分摊采购总数，分支库存从人工调整开始，避免重复放大库存。
            purchased = 0 if len(variants) > 1 else int(purchased_by_id.get(product.id, 0) or 0) + int(purchased_by_no.get(product.product_no, 0) or 0)
            adjusted = int(adjusted_by_sku.get(sku, 0) or 0) if sku else 0
            shipped = int(shipped_by_sku.get(sku, 0) or 0) if sku else 0
            variant_name = zh_names[idx] if len(zh_names) == len(variants) else (product.chinese_product_name or product.product_name or "未命名产品")
            rows.append({
                "product_id": f"{product.id}:{idx}" if len(variants) > 1 else product.id,
                "product_no": product.product_no,
                "sku": sku,
                "name": variant_name,
                "name_en": product.product_name or "",
                "image_url": product.product_image_url or "",
                "purchased": purchased,
                "adjusted": adjusted,
                "shipped": shipped,
                "available": purchased + adjusted - shipped,
                "variant_group": product.id if len(variants) > 1 else "",
                "is_primary_variant": idx == 0,
            })
    return rows


@router.get("/stores-overview")
def stores_inventory_overview(db: Session = Depends(get_db)):
    """管理视角的跨店库存摘要；不改变当前店铺请求作用域。"""
    result = []
    for store in db.query(Store).order_by(Store.created_at.asc()).all():
        rows = list_inventory(db=db, store_id=store.id)
        purchased = sum(row["purchased"] for row in rows)
        adjusted = sum(row["adjusted"] for row in rows)
        shipped = sum(row["shipped"] for row in rows)
        available = sum(row["available"] for row in rows)
        result.append({
            "store_id": store.id,
            "store_name": store.name,
            "status": store.status,
            "sku_count": sum(1 for row in rows if row["sku"]),
            "purchased": purchased,
            "adjusted": adjusted,
            "shipped": shipped,
            "available": available,
            "exception_count": sum(1 for row in rows if row["sku"] and row["available"] <= 0),
            "last_synced_at": store.last_synced_at.isoformat() if store.last_synced_at else None,
        })
    return result


class AdjustmentIn(BaseModel):
    sku: str
    quantity_delta: int
    reason: str
    notes: str = ""
    occurred_at: Optional[str] = None


@router.post("/adjustments")
def create_adjustment(data: AdjustmentIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    sku = data.sku.strip()
    if not sku or data.quantity_delta == 0:
        raise HTTPException(status_code=400, detail="SKU 不能为空，调整数量不能为 0")
    product, _ = _find_product_for_sku(db, sku, store_id)
    if not product:
        raise HTTPException(status_code=404, detail="找不到该 SKU")
    current = inventory_balance_for_sku(db, sku, store_id)
    if data.quantity_delta < 0 and current + data.quantity_delta < 0:
        raise HTTPException(status_code=400, detail=f"库存不足：当前可用 {current}，不能减少 {abs(data.quantity_delta)}")
    row = InventoryAdjustment(
        id=str(uuid.uuid4()),
        store_id=store_id,
        sku=sku,
        quantity_delta=data.quantity_delta,
        reason=data.reason.strip() or "人工调整",
        notes=data.notes.strip(),
        occurred_at=data.occurred_at or dt.date.today().isoformat(),
    )
    db.add(row)
    db.commit()
    return {"ok": True, "id": row.id, "available": inventory_balance_for_sku(db, sku, store_id)}


@router.get("/adjustments")
def list_adjustments(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    rows = db.query(InventoryAdjustment).filter(InventoryAdjustment.store_id == store_id).order_by(InventoryAdjustment.created_at.desc()).limit(500).all()
    return [{
        "id": r.id, "sku": r.sku, "quantity_delta": r.quantity_delta,
        "reason": r.reason, "notes": r.notes, "occurred_at": r.occurred_at,
        "reversed_by": r.reversed_by, "created_at": r.created_at.isoformat() if r.created_at else "",
    } for r in rows]


@router.post("/adjustments/{adjustment_id}/reverse")
def reverse_adjustment(adjustment_id: str, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    original = db.query(InventoryAdjustment).filter(InventoryAdjustment.id == adjustment_id, InventoryAdjustment.store_id == store_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="调整记录不存在")
    if original.reversed_by:
        raise HTTPException(status_code=400, detail="该调整已经撤销")
    reversal_delta = -original.quantity_delta
    current = inventory_balance_for_sku(db, original.sku, store_id)
    if reversal_delta < 0 and current + reversal_delta < 0:
        raise HTTPException(
            status_code=400,
            detail=f"撤销后库存会变为负数：当前可用 {current}，请先撤回相关发货或补录库存",
        )
    reversal = InventoryAdjustment(
        id=str(uuid.uuid4()), store_id=store_id, sku=original.sku, quantity_delta=reversal_delta,
        reason="撤销库存调整", notes=f"撤销记录 {original.id}",
        occurred_at=dt.date.today().isoformat(),
    )
    db.add(reversal)
    db.flush()
    original.reversed_by = reversal.id
    db.commit()
    return {"ok": True}


@router.get("/shipments")
def list_shipments(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    rows = db.query(Shipment).filter(Shipment.store_id == store_id).order_by(Shipment.created_at.desc()).limit(500).all()
    result = []
    for row in rows:
        lines = db.query(ShipmentLine).filter(ShipmentLine.shipment_id == row.id).all()
        result.append({
            "id": row.id, "shipment_no": row.shipment_no, "shipment_date": row.shipment_date,
            "mark": row.mark, "shipping": row.shipping, "address": row.address,
            "status": row.status, "version": row.version, "void_reason": row.void_reason,
            "total_skus": len(lines), "total_quantity": sum(x.total_quantity for x in lines),
            "lines": [{"sku": x.sku, "name": x.name_zh or x.name_en, "cartons": x.cartons,
                       "count_per_carton": x.count_per_carton, "total_quantity": x.total_quantity}
                      for x in lines],
        })
    return result


class InboundExportIn(BaseModel):
    shipment_ids: list[str] = []


@router.post("/shipments/inbound-template")
def export_inbound_template(data: InboundExportIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    if not data.shipment_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个装箱单")
    rows = []
    shipment_numbers = []
    for identifier, shipment_id in enumerate(data.shipment_ids, start=1):
        shipment = db.query(Shipment).filter(Shipment.id == shipment_id, Shipment.store_id == store_id).first()
        if not shipment:
            raise HTTPException(status_code=404, detail=f"第 {identifier} 个发货记录不存在")
        if shipment.status != "confirmed":
            raise HTTPException(status_code=400, detail=f"{shipment.shipment_no} 已撤回，不能生成入仓文件")
        lines = db.query(ShipmentLine).filter(ShipmentLine.shipment_id == shipment.id).order_by(ShipmentLine.sku).all()
        if not lines:
            raise HTTPException(status_code=400, detail=f"{shipment.shipment_no} 没有产品明细")
        rows.extend((identifier, line) for line in lines)
        shipment_numbers.append(shipment.shipment_no)
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "inbound.xlsx"
        generate_inbound(rows, output)
        content = output.read_bytes()
    filename = f"inbound_{shipment_numbers[0]}_{len(shipment_numbers)}lists.xlsx"
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


class VoidIn(BaseModel):
    reason: str = "装箱单有误，撤回修改"


@router.post("/shipments/{shipment_id}/void")
def void_shipment(shipment_id: str, data: VoidIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    row = db.query(Shipment).filter(Shipment.id == shipment_id, Shipment.store_id == store_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="发货记录不存在")
    if row.status != "confirmed":
        raise HTTPException(status_code=400, detail="该发货记录已经撤回")
    row.status = "void"
    row.void_reason = data.reason.strip()
    row.voided_at = dt.datetime.utcnow()
    db.commit()
    return {"ok": True}
