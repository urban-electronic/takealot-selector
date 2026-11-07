"""Order-line and platform receipt evidence. Read-only towards Takealot."""
import json
from datetime import date
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from api.store_routes import get_store_id
from services.seller_ledger import SellerLedgerSnapshot, SOURCE, sync

router = APIRouter(prefix='/api/seller-ledger', tags=['seller-ledger'])


class Period(BaseModel):
    start: date | None = None
    end: date | None = None


@router.post('/sales/sync')
def sync_sales(data: Period, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    return sync(db, store, 'sales', data.start, data.end)


@router.post('/shipments/sync')
def sync_shipments(db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    return sync(db, store, 'shipments')


def listing(db, store, resource, page, page_size, search):
    saved = db.query(SellerLedgerSnapshot).filter_by(store_id=store, resource=resource).first()
    if not saved:
        return dict(items=[], total=0, page=page, page_size=page_size, snapshot=None)
    rows = json.loads(saved.payload)
    if search:
        fields = ('sku', 'order_id', 'order_item_id') if resource == 'sales' else ('shipment_id', 'reference')
        rows = [r for r in rows if any(search.lower() in str(r.get(k, '')).lower() for k in fields)]
    rows.sort(key=lambda r: (r.get('order_date', ''), int(r.get('order_item_id') or r.get('shipment_id'))), reverse=True)
    return dict(items=rows[(page-1)*page_size:page*page_size], total=len(rows), page=page, page_size=page_size,
                snapshot=dict(source=SOURCE, seller_id=saved.seller_id, synced_at=saved.synced_at,
                              period_from=saved.period_from, period_to=saved.period_to,
                              complete=True, version=saved.version))


@router.get('/sales')
def sales(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str = Query('', max_length=100),
          db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    return listing(db, store, 'sales', page, page_size, search)


@router.get('/shipments')
def shipments(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str = Query('', max_length=100),
              db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    return listing(db, store, 'shipments', page, page_size, search)
