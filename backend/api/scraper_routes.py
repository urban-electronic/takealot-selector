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

# 交互式抓取的预算。前端这一路是 `AbortSignal.timeout(90000)`：90 秒一到浏览器就断开，
# 用户看到的是「请求等待超时，请稍后刷新列表确认结果，避免重复提交」——可抓取请求并没有
# 列表可刷新，也没说清官网读到哪一步；而服务端还会继续跑下去，把采集浏览器那把锁多占
# 一分钟（采集循环每 5 分钟就要用一次）。这里在客户端放弃之前收尾，把原因如实写进
# warnings：前端拿到 success=false 会直接展开「导入保存的网页」面板，用户不必干等。
# 75 秒覆盖正常路径（curl_cffi 快速 403 → Firefox/Chromium 各约 15 秒 → 持久化浏览器
# 约 15~30 秒）；收尾本身也被 quote_browser 的 CLEANUP_TIMEOUT 限住，不会无限拖。
SCRAPE_BUDGET_SECONDS = 75
SELLER_LOOKUP_BUDGET_SECONDS = 20


class ScrapeRequest(BaseModel):
    # 同时兼容旧前端 productUrl 与新前端 url，避免部署先后造成抓取中断。
    url: str = ""
    productUrl: str = ""

    @property
    def effective_url(self) -> str:
        return (self.url or self.productUrl or "").strip()


@router.post("/scrape-takealot")
async def scrape_takealot(data: ScrapeRequest, db: Session = Depends(get_db), request: Request = None):
    deadline = asyncio.get_running_loop().time() + SCRAPE_BUDGET_SECONDS
    def remaining():
        return max(0, deadline - asyncio.get_running_loop().time())
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
                result = await asyncio.wait_for(asyncio.to_thread(seller_product, url, key),
                                                timeout=min(SELLER_LOOKUP_BUDGET_SECONDS, remaining()))
            except asyncio.TimeoutError:
                # to_thread cancellation stops waiting, not the underlying read-only HTTP call.
                seller_warning = '卖家资料读取等待超时，已停止等待，改用官网资料来源。'
            except Exception as error:
                # 这句会直接出现在商品页的警告里。原来不分成因：密钥无效（401）和
                # 限流（429）都是「暂时不可用，已暂停重复请求」，用户不知道该改
                # 密钥还是等一会儿（SELLER-WARN-REASON-01）。
                from api.product_routes import _read_reason
                seller_warning = f'卖家 API 读取失败，改用现有资料来源：{_read_reason(error)}'
    if result is None:
        partial_result = {}
        try:
            result = await asyncio.wait_for(scrape_product(url, partial_result=partial_result), timeout=remaining())
        except asyncio.TimeoutError:
            # 超时不是「服务器内部错误」，也不是「没找到商品」：它是「官网这次太慢」。
            # 用可编辑的空结果 + 原因返回，前端会展开保存网页导入面板，用户还能继续。
            result = {
                'normalized_url': url,
                'variants': [],
                **partial_result,
                'success': False,
                'data_source': partial_result.get('data_source') or 'manual',
            }
            retained = any(result.get(k) for k in ('product_name', 'product_image_url', 'actual_sale_price_zar'))
            missing = [label for key, label in (('product_name', '标题'), ('product_image_url', '图片'), ('actual_sale_price_zar', '售价')) if not result.get(key)]
            if retained and result['data_source'] == 'manual':
                result['data_source'] = 'takealot'
            result['warnings'] = list(result.get('warnings', [])) + [
                f'资料读取达到本次 {SCRAPE_BUDGET_SECONDS} 秒预算已中止。'
                + (('已保留读到的商品资料；尚未取得' + '、'.join(missing) + '。' if missing else '已保留读到的商品资料；剩余核对未完成。') if retained else '标题、图片、售价都没有拿到。')
                + '可稍后重试；或等官网正常显示商品后按 Ctrl+S 保存为「网页，仅 HTML」，用下方导入保存的网页直接读取。'
            ]
            result['diagnostics'] = list(result.get('diagnostics', [])) + [{'stage': 'budget', 'reason': 'timeout'}]
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
                _scrape_with_curl_cffi, url, result['normalized_url']), timeout=min(32, remaining()))
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
