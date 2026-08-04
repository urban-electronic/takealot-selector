"""
Takealot 抓取路由
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.takealot_scraper import scrape_product, validate_takealot_url
from services.fee_category_matcher import match_fee_category

router = APIRouter(prefix="/api/products", tags=["scraper"])


class ScrapeRequest(BaseModel):
    url: str


@router.post("/scrape-takealot")
async def scrape_takealot(data: ScrapeRequest, db: Session = Depends(get_db)):
    if not validate_takealot_url(data.url):
        raise HTTPException(status_code=400, detail="请提供有效的 takealot.com 链接")

    result = await scrape_product(data.url)

    if not result["success"] and not result["product_name"]:
        raise HTTPException(
            status_code=422,
            detail=f"无法抓取该商品信息: {'; '.join(result['warnings'])}",
        )

    # 匹配 Fee 品类
    fee_match = match_fee_category(
        db,
        takealot_category_path=result.get("takealot_category_path"),
        product_name=result.get("product_name"),
    )

    return {
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
    }
