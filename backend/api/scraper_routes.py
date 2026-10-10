"""
Takealot 抓取路由
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.takealot_scraper import scrape_product, validate_takealot_url
from services.fee_category_matcher import match_fee_category
from services.takealot_seller_api import configured_key, seller_product
from fastapi import Request
import asyncio

router = APIRouter(prefix="/api/products", tags=["scraper"])


class ScrapeRequest(BaseModel):
    # 同时兼容旧前端 productUrl 与新前端 url，避免部署先后造成抓取中断。
    url: str = ""
    productUrl: str = ""

    @property
    def effective_url(self) -> str:
        return (self.url or self.productUrl or "").strip()


@router.post("/scrape-takealot")
async def scrape_takealot(data: ScrapeRequest, db: Session = Depends(get_db), request: Request = None):
    url = data.effective_url
    if not validate_takealot_url(url):
        raise HTTPException(status_code=400, detail="请提供有效的 takealot.com 链接")

    result = None
    seller_warning = None
    # This key belongs to the existing Urban Electronics store only.
    if db is not None and request is not None and request.headers.get('X-Store-Id', 'default-store') == 'default-store':
        key = configured_key(db)
        if key:
            try:
                result = await asyncio.to_thread(seller_product, url, key)
            except Exception:
                seller_warning = '卖家 API 暂时不可用，已暂停重复请求；改用现有资料来源。'
    if result is None:
        result = await scrape_product(url)
    if seller_warning:
        result.setdefault('warnings', []).append(seller_warning)

    # 匹配 Fee 品类
    fee_match = match_fee_category(
        db,
        takealot_category_path=result.get("takealot_category_path"),
        product_name=result.get("product_name"),
    )

    return {
        "success": result.get("success", False),
        "data_source": result.get("data_source", "takealot" if result.get("success") else "manual"),
        "variants": result.get("variants", []),
        "in_stock_price": result.get("in_stock_price"),
        "normalized_url": result["normalized_url"],
        "tsin": result.get("tsin"),
        "product_name": result.get("product_name"),
        "product_image_url": result.get("product_image_url"),
        "actual_sale_price_zar": result.get("actual_sale_price_zar"),
        "takealot_category_path": result.get("takealot_category_path"),
        "recommended_fee_category": fee_match["fee_category"],
        "fee_category_confidence": fee_match["confidence"],
        "fee_match_reason": fee_match.get("match_reason", ""),
        "warnings": result.get("warnings", []),
        "diagnostics": result.get("diagnostics", []),
    }
