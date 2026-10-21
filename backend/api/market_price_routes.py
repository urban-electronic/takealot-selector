"""多市场参考价：覆盖率、手工录入、立即同步、兰特汇率。

前端把「中国官网 / 新加坡官网 / 淘宝」三列并排展示，这里提供四件事：

- `GET  /api/market-prices/status` 每家店每个市场填了多少、上次同步什么时候跑；
- `POST /api/market-prices/batch`  批量录入/清除手工价（一次贴十几行不用点十几次）；
- `POST /api/market-prices/sync`   手动触发一次官网重读；
- `GET  /api/market-prices/fx`     把 S$ / ¥ 折成兰特的汇率（展示层用，全局共享）。

前三个接口按 `X-Store-ID` 收窄到当前店铺：A 店的 SKU 不会因为同名被写到 B 店。
汇率与店铺无关，所以 `/fx` 不读店铺头。
"""

from __future__ import annotations

import json
import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.product_routes import _check_market_prices
from api.store_routes import get_store_id
from database import get_db
from models import Product
from services import fx_rates, market_price_sync

router = APIRouter(prefix="/api/market-prices", tags=["market-prices"])

MARKETS = ('CN', 'SG', 'TB')
CURRENCY = {'CN': 'CNY', 'SG': 'SGD', 'TB': 'CNY'}


class PriceInput(BaseModel):
    market: Literal['CN', 'SG', 'TB']
    price: Optional[float] = Field(default=None, ge=0)
    """None 表示清除该市场（录错了要能删掉，而不是只能改成 0）。"""
    cur: Optional[str] = None
    src: str = '手工'
    url: Optional[str] = None
    n: Optional[int] = Field(default=None, ge=0)
    at: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    """报价本身发生的日期，不是录入日期。

    这个字段不是装饰：淘宝/天猫/京东的价格抓不到，只能人工照商品页抄，
    抄来的可能是三个月前的爆料价。写成「今天录入」会把它伪装成实时价，
    所以调用方给什么日期就存什么日期，没给才用今天。
    """
    sku: Optional[str] = None
    product_id: Optional[str] = None


class BatchInput(BaseModel):
    items: list[PriceInput]


def _load(raw: str | None) -> dict:
    try:
        value = json.loads(raw or '{}')
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _matches(db: Session, store_id: str, item: PriceInput) -> list[Product]:
    """按 product_id 或 SKU 找产品，返回**全部**命中行。

    为什么返回列表：同一个 SKU 可能有多行（CK 选品表里 Apfra 就重复了一条），
    只写第一行的话，列表里两条一模一样的记录会一条有价、一条空白，用户会以为没保存。

    SKU 允许「主货号前缀」匹配：库里存 `CK2-90151648-1`，用户手上是带配色的
    `CK2-90151648-1_STO.GR`，精确匹配会漏掉——但只在唯一命中时才认，
    撞上多个候选宁可报错也不猜。
    """
    if item.product_id:
        hit = db.query(Product).filter(Product.id == item.product_id,
                                       Product.store_id == store_id).first()
        return [hit] if hit else []
    sku = (item.sku or '').strip()
    if not sku:
        return []
    hits = db.query(Product).filter(Product.store_id == store_id, Product.sku == sku).all()
    if hits:
        return hits
    candidates = db.query(Product).filter(
        Product.store_id == store_id, Product.sku.like(f'{sku}\\_%', escape='\\')).all()
    return candidates if len(candidates) == 1 else []


@router.get('/status')
def status(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    products = db.query(Product).filter(Product.store_id == store_id).all()
    coverage = {code: {'filled': 0, 'stale': 0} for code in MARKETS}
    filled_products = 0
    for product in products:
        prices = _load(product.market_prices)
        any_filled = False
        for code in MARKETS:
            entry = prices.get(code)
            if isinstance(entry, dict) and entry.get('price'):
                coverage[code]['filled'] += 1
                any_filled = True
                if entry.get('stale'):
                    coverage[code]['stale'] += 1
        filled_products += 1 if any_filled else 0

    last = market_price_sync.last_run(db)
    return {
        'store_id': store_id,
        'products': len(products),
        'products_with_price': filled_products,
        'coverage': coverage,
        'last_sync': last or None,
        'interval_hours': market_price_sync.INTERVAL_HOURS,
        'auto_disabled': market_price_sync.DISABLED,
        'due': market_price_sync.due(last),
        # 只有这里列出的市场会被每日重读。曾经写成 ['SG','MY']，但 MY 不在
        # MARKETS / CURRENCY / 前端 MARKET_ORDER 里（PriceInput.market 是
        # Literal['CN','SG','TB']，客户端照着它提交会直接 422），所以只留 SG。
        'auto_markets': ['SG'],
        'manual_only': {'TB': '淘宝/天猫需登录，不做自动抓取', 'CN': '中国官网已停摆，需人工录入'},
    }


@router.post('/batch')
def batch(payload: BatchInput, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    if not payload.items:
        raise HTTPException(status_code=400, detail='没有要写入的价格')
    if len(payload.items) > 500:
        raise HTTPException(status_code=400, detail='单次最多 500 条')

    results = []
    touched: dict[str, Product] = {}
    for item in payload.items:
        products = _matches(db, store_id, item)
        if not products:
            results.append({'sku': item.sku, 'product_id': item.product_id, 'market': item.market,
                            'status': 'not_found'})
            continue
        written = 0
        outcome = 'updated'
        last_price = None
        for product in products:
            prices = _load(product.market_prices)
            if item.price is None:
                prices.pop(item.market, None)
                outcome = 'cleared'
            else:
                entry = dict(prices.get(item.market) or {})
                entry.update({
                    'price': round(float(item.price), 2),
                    'cur': (item.cur or CURRENCY[item.market]).upper(),
                    'src': item.src or '手工',
                    'at': item.at or market_price_sync._today(),
                    'checked_at': market_price_sync._now_iso(),
                })
                if item.url:
                    entry['url'] = item.url.strip()
                if item.n is not None:
                    entry['n'] = item.n
                entry.pop('stale', None)
                entry.pop('miss', None)
                prices[item.market] = entry
            raw = json.dumps(prices, ensure_ascii=False, sort_keys=True) if prices else ''
            try:
                _check_market_prices(raw)  # 与单条 PATCH 走同一套校验，坏数据不进库
            except ValueError as exc:
                results.append({'sku': item.sku, 'product_id': item.product_id, 'market': item.market,
                                'status': 'invalid', 'error': str(exc)})
                written = -1
                break
            product.market_prices = raw
            touched[product.id] = product
            written += 1
            last_price = prices.get(item.market, {}).get('price')
        if written < 0:
            continue
        results.append({'sku': products[0].sku, 'product_id': products[0].id, 'market': item.market,
                        'status': outcome, 'price': last_price, 'products': written})

    db.commit()
    return {
        'store_id': store_id,
        'saved': sum(1 for r in results if r['status'] in ('updated', 'cleared')),
        'products': len(touched),
        'results': results,
    }


@router.get('/fx')
def fx(db: Session = Depends(get_db)):
    """兰特汇率：`{货币: 1 单位该货币值多少兰特}`。

    展示层拿它把 S$ / ¥ 折算成 R。缓存 24 小时，过期后第一次调用才去上游拉；
    拉不到时先退回旧缓存、再退回内置兜底值，`stale` / `fallback` 会让界面写清来源。
    """
    return fx_rates.get_rates(db)


@router.post('/sync')
def sync_now(store_id: str = Depends(get_store_id)):
    """手动触发一次官网重读。同步在后台线程跑，接口立刻返回，避免前端等一分钟超时。"""
    if market_price_sync.DISABLED:
        raise HTTPException(status_code=400, detail='自动同步已被 MARKET_PRICE_SYNC_DISABLED 关闭')
    thread = threading.Thread(target=market_price_sync.run_once, kwargs={'store_id': store_id},
                              daemon=True, name='market-price-manual')
    thread.start()
    return {'started': True, 'store_id': store_id, 'message': '已开始重读官网价，约 1 分钟出结果'}
