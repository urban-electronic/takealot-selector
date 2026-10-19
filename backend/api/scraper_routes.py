"""
Takealot 抓取路由
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.takealot_scraper import scrape_product, validate_takealot_url, _scrape_with_curl_cffi
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
        try:
            result = await scrape_product(url)
        except Exception as error:
            # 抓取层抛异常时，原来会一路冒到全局处理器变成 500「服务器内部错误」，
            # 用户拿不到任何线索。这里按同一套归因给出可行动的原因。
            from api.product_routes import _read_reason
            raise HTTPException(502, f'官网资料暂时无法读取（{_read_reason(error)}）')
    elif not result.get('product_image_url'):
        # Seller offers may omit images. One bounded public-page read fills only
        # the missing image, never replacing seller SKU, title or own price.
        # Do not escalate a blocked response through additional browsers.
        try:
            page, _ = await asyncio.wait_for(asyncio.to_thread(
                _scrape_with_curl_cffi, url, result['normalized_url']), timeout=32)
        except Exception:
            page = None
        if page and page.get('product_image_url'):
            result['product_image_url'] = page['product_image_url']
            result['warnings'] = [w for w in result.get('warnings', [])
                                  if w != '卖家接口未提供商品图片，请补充。']
            result['warnings'].append('图片补充自官网；售价与 SKU 仍来自本店卖家 API。')
            result['success'] = bool(result.get('actual_sale_price_zar'))
        else:
            result.setdefault('warnings', []).append('官网图片未获取，可从已打开的商品页导入图片。')
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
