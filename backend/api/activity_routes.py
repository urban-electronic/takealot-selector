from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.store_routes import get_store_id
from database import get_db
from models import OperationLog

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("")
def list_activity(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    rows = (
        db.query(OperationLog)
        .filter(OperationLog.store_id == store_id)
        .order_by(OperationLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [{
        "id": row.id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "action": row.action,
        "summary": row.summary,
        "details": row.details,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    } for row in rows]
