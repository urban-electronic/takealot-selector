"""站外货源路由：1688 / Takealot 上看到的外部商品，登记后按关键词挂到商品详情页。

这里**不做任何站外抓取**。实测两个平台的搜索页都有人机校验（Takealot 返回
Cloudflare 挑战页、1688 返回跳转壳），绕过去不是本项目要做的事，所以数据靠登记。
"""

from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from database import get_db
from models import ExternalOffer, Product
from api.store_routes import get_store_id
from services.external_offers import (
    CURRENCY_OF, DEFAULT_LIMIT, MAX_LIMIT, PLATFORM_1688, PLATFORM_TAKEALOT,
    PLATFORMS, parse_keywords, recommend_external, serialize,
)

router = APIRouter(prefix="/api/external-offers", tags=["external-offers"])

# 各平台认的域名。粘错链接（比如把商品链接粘成搜索结果页）在这里就挡掉，
# 免得详情页出现一条点开是 404 的「货源」。
HOSTS = {
    PLATFORM_1688: ("1688.com",),
    PLATFORM_TAKEALOT: ("takealot.com",),
}


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


def _check_url(platform: str, url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        # 只放 http/https：这个字段最终会渲染成可点链接，
        # `javascript:` 之类的 scheme 混进来就是一个可执行的注入点。
        raise ValueError('链接必须是 http/https')
    host = _host_of(url)
    allowed = HOSTS.get(platform, ())
    if not any(host == suffix or host.endswith('.' + suffix) for suffix in allowed):
        raise ValueError('%s 的链接域名应为 %s，实际是 %s' % (platform, ' / '.join(allowed), host or '空'))
    return url


class ExternalOfferIn(BaseModel):
    platform: str = Field(default=PLATFORM_1688)
    url: str
    title: str = ''
    title_cn: str = ''
    price: Optional[float] = None
    moq: Optional[int] = None
    image_url: str = ''
    keywords: str = ''
    usage: str = ''
    scenario: str = ''
    reason: str = ''

    @field_validator('platform')
    @classmethod
    def _platform(cls, value: str) -> str:
        value = (value or '').strip().lower()
        if value not in PLATFORMS:
            raise ValueError('platform 只能是 %s' % ' / '.join(PLATFORMS))
        return value

    @field_validator('url')
    @classmethod
    def _url(cls, value: str) -> str:
        value = (value or '').strip()
        if not value:
            raise ValueError('链接不能为空')
        return value

    @field_validator('price')
    @classmethod
    def _price(cls, value):
        # 0 不是价格：1688 上「价格待议」常被填成 0，留着会在界面上显示 ¥0
        if value is not None and value <= 0:
            return None
        return value

    @field_validator('moq')
    @classmethod
    def _moq(cls, value):
        if value is not None and value <= 0:
            return None
        return value


class ExternalOfferPatch(BaseModel):
    platform: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    title_cn: Optional[str] = None
    price: Optional[float] = None
    moq: Optional[int] = None
    image_url: Optional[str] = None
    keywords: Optional[str] = None
    usage: Optional[str] = None
    scenario: Optional[str] = None
    reason: Optional[str] = None
    is_active: Optional[bool] = None


def _payload(offer: ExternalOffer) -> dict:
    data = serialize(offer)
    data['is_active'] = bool(offer.is_active)
    return data


@router.get('')
def list_offers(
    platform: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    query = db.query(ExternalOffer).filter(ExternalOffer.store_id == store_id)
    if not include_inactive:
        query = query.filter(ExternalOffer.is_active.is_(True))
    if platform:
        query = query.filter(ExternalOffer.platform == platform.strip().lower())
    rows = query.order_by(ExternalOffer.created_at.desc()).all()

    items = [_payload(row) for row in rows]
    if keyword:
        needle = keyword.strip().lower()
        items = [
            item for item in items
            if needle in ' '.join([
                item['title'], item['title_cn'], item['reason'], item['usage'],
                ' '.join(item['keywords']),
            ]).lower()
        ]

    counts = {}
    for item in items:
        counts[item['platform']] = counts.get(item['platform'], 0) + 1
    return {'items': items, 'total': len(items), 'counts': counts}


@router.post('', status_code=201)
def create_offer(
    data: ExternalOfferIn,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    try:
        url = _check_url(data.platform, data.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    exists = (
        db.query(ExternalOffer)
        .filter(ExternalOffer.store_id == store_id, ExternalOffer.url == url)
        .first()
    )
    if exists:
        # 同一条链接重复登记会让详情页出现两条一模一样的货源
        raise HTTPException(409, '这条链接已经登记过了')

    offer = ExternalOffer(
        store_id=store_id,
        platform=data.platform,
        url=url,
        title=(data.title or '').strip(),
        title_cn=(data.title_cn or '').strip(),
        price=data.price,
        currency=CURRENCY_OF[data.platform],
        moq=data.moq,
        image_url=(data.image_url or '').strip(),
        keywords=','.join(parse_keywords(data.keywords)),
        usage=(data.usage or '').strip(),
        scenario=(data.scenario or '').strip(),
        reason=(data.reason or '').strip(),
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return _payload(offer)


@router.patch('/{offer_id}')
def update_offer(
    offer_id: str,
    data: ExternalOfferPatch,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    offer = (
        db.query(ExternalOffer)
        .filter(ExternalOffer.id == offer_id, ExternalOffer.store_id == store_id)
        .first()
    )
    if not offer:
        raise HTTPException(404, '站外货源不存在')

    platform = (data.platform or offer.platform or '').strip().lower()
    if platform not in PLATFORMS:
        raise HTTPException(400, 'platform 只能是 %s' % ' / '.join(PLATFORMS))

    url = (data.url or offer.url or '').strip()
    try:
        url = _check_url(platform, url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if url != offer.url:
        dup = (
            db.query(ExternalOffer)
            .filter(ExternalOffer.store_id == store_id, ExternalOffer.url == url,
                    ExternalOffer.id != offer.id)
            .first()
        )
        if dup:
            raise HTTPException(409, '这条链接已经登记过了')

    offer.platform = platform
    offer.url = url
    offer.currency = CURRENCY_OF[platform]
    if data.title is not None:
        offer.title = data.title.strip()
    if data.title_cn is not None:
        offer.title_cn = data.title_cn.strip()
    if 'price' in data.model_fields_set:
        offer.price = data.price if data.price is not None and data.price > 0 else None
    if 'moq' in data.model_fields_set:
        offer.moq = data.moq if data.moq is not None and data.moq > 0 else None
    if data.image_url is not None:
        offer.image_url = data.image_url.strip()
    if data.keywords is not None:
        offer.keywords = ','.join(parse_keywords(data.keywords))
    if data.usage is not None:
        offer.usage = data.usage.strip()
    if data.scenario is not None:
        offer.scenario = data.scenario.strip()
    if data.reason is not None:
        offer.reason = data.reason.strip()
    if data.is_active is not None:
        offer.is_active = bool(data.is_active)

    db.commit()
    db.refresh(offer)
    return _payload(offer)


@router.delete('/{offer_id}')
def archive_offer(
    offer_id: str,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """下线，不是物理删除：录错过一条不该连带把理由也一起弄丢。"""
    offer = (
        db.query(ExternalOffer)
        .filter(ExternalOffer.id == offer_id, ExternalOffer.store_id == store_id)
        .first()
    )
    if not offer:
        raise HTTPException(404, '站外货源不存在')
    offer.is_active = False
    db.commit()
    return {'ok': True, 'id': offer_id, 'is_active': False}


@router.get('/for-product/{product_id}')
def offers_for_product(
    product_id: str,
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """详情页调这个：按关键词命中把站外货源分两栏返回。"""
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.store_id == store_id)
        .first()
    )
    if not product:
        raise HTTPException(404, '商品不存在')
    return recommend_external(db, store_id, product, limit=limit)
