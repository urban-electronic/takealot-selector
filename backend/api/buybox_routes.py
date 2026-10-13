"""Independent read-only collection API. No price rule or range requirement."""
from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from database import get_db
from models import DEFAULT_STORE_ID
from api.store_routes import get_store_id
from api.pricing_routes import MonitorConfig,MonitorBulk,monitor_configure,monitor_bulk,collect_now
from services.buybox_monitor import catalog
from services.takealot_seller_api import configured_key

router=APIRouter(prefix='/api/buybox',tags=['buybox'])

@router.get('')
def listing(db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return {'rows':catalog(db,store),'configured':bool(configured_key(db))if store==DEFAULT_STORE_ID else False}

@router.post('/bulk')
def bulk(data:MonitorBulk,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return monitor_bulk(data,db,store)

@router.put('/{product_id}/{offer_id}')
def configure(product_id:str,offer_id:str,data:MonitorConfig,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return monitor_configure(product_id,offer_id,data,db,store)

@router.post('/{product_id}/{offer_id}/collect-now')
def once(product_id:str,offer_id:str,db:Session=Depends(get_db),store:str=Depends(get_store_id)):
    return collect_now(product_id,offer_id,db,store)
