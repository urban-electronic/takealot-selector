"""Website-key-only browser controls; collector keys cannot use these routes."""
from typing import Literal
from urllib.parse import urlsplit
import re
import os
import secrets
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from models import Product, DEFAULT_STORE_ID
from api.store_routes import get_store_id
from services.cloud_browser import browser, WIDTH, HEIGHT

def require_browser_access(key: str=Header('',alias='X-API-Key')):
    expected=os.environ.get('API_KEY','').strip()
    if not expected or not secrets.compare_digest(key,expected):
        raise HTTPException(401, '云端浏览器需要有效的网站服务密钥')

router = APIRouter(prefix='/api/buybox-browser', tags=['buybox-browser'], dependencies=[Depends(require_browser_access)])

def store_check(store):
    if store != DEFAULT_STORE_ID: raise HTTPException(400, '当前店铺未绑定云端采集浏览器')

class Open(BaseModel):
    product_id: str = Field(max_length=100)

class Action(BaseModel):
    action: Literal['click', 'text', 'key', 'scroll', 'close']
    x: int = Field(default=0, ge=0, lt=WIDTH)
    y: int = Field(default=0, ge=0, lt=HEIGHT)
    text: str = Field(default='', max_length=160)
    key: Literal['Enter', 'Tab', 'Shift+Tab', 'Backspace', 'Escape', 'ArrowDown', 'ArrowUp', 'Control+A'] = 'Enter'
    delta: int = Field(default=0, ge=-700, le=700)

@router.post('/open')
def open_browser(data: Open, db: Session=Depends(get_db), store: str=Depends(get_store_id)):
    store_check(store)
    p=db.query(Product).filter(Product.id==data.product_id, Product.store_id==store, Product.is_archived==False).first()
    if p is None: raise HTTPException(404, '本店商品不存在')
    url=p.takealot_url or ''; parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('takealot.com','www.takealot.com') or not re.search(r'PLID\d+',parsed.path,re.I):
        raise HTTPException(400, '商品缺少有效Takealot链接')
    return browser().run('open', url=url)

@router.get('/frame')
def frame(store: str=Depends(get_store_id), token: str=Header('',alias='X-Browser-Session')):
    store_check(store)
    return Response(browser().run('frame',token), media_type='image/jpeg',headers={'Cache-Control':'no-store','Pragma':'no-cache'})

@router.post('/action')
def action(data: Action, store: str=Depends(get_store_id), token: str=Header('',alias='X-Browser-Session')):
    store_check(store)
    return browser().run(data.action,token,**data.model_dump(exclude={'action'}))
