"""
选品池路由
==========

把「上传一份表 → 抓主图/类目 → 填重尺和成本 → 算出利润率 → 一键进产品列表」
做成一条流水线。四个端点对应四步：

    POST /api/selection/import    上传表，按商品聚合去重，落进选品池
    POST /api/selection/enrich    抓主图、类目、价格；自家商品还能抓到重尺
    PATCH /api/selection/items/{id} / POST /items/bulk   人工补重尺和采购价
    POST /api/selection/items/promote                    勾中的才进 products

为什么不写进 `products`
----------------------
`products` 是库存/打包/采购的锚点，草稿进去会被当成真货；而且一轮 1800 条
绝大多数会被淘汰，删掉还会在 `product_no` 上留空洞。草稿留在
`selection_items`，promote 时才复制过去（见 models.SelectionItem 的注释）。

重尺从哪来（这是用户最关心的一点）
----------------------------------
1. **自家店铺的商品**（销售报表里只有 TSIN 的那种）：卖家 API `/v1/offers`
   本来就返回 `length_cm / width_cm / height_cm / weight_grams`，直接换算成
   mm / kg 填进来。`seller_catalog_sync` 早就在用这几个字段，这里只是复用。
2. **竞品商品**：`/v1/offers` 只返回本店 offer，拿不到别人的。主路径是
   卖家门户自己的目录接口（`services/takealot_portal_api.py`）——它给的是
   Takealot 用 Cubiscan 实测的 `consolidated_packaged_dimensions`，实测
   1798 个候选里 1797 个都有，还顺带给了主图，绕开了公开接口的 Cloudflare 429。
3. **公开商品页**：兜底。`Assembled Dimensions` 实测只有约 10% 的商品有、
   `Weight` 约 2%，格式还乱（"22 x 7.2 x 17 cm"、"M-L"），只能当**预填**，
   必须人工确认。拿不到就留空，绝不用类目默认值冒充——重尺错了利润就全错。
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from api.store_routes import get_store_id
from database import get_db
from models import FeeCategory, Product, SelectionItem
from services import takealot_public_api as pub
from services.battery_guess import (
    SEA_BATTERY,
    SEA_GENERAL,
    guess_battery,
    suggested_shipping,
)
from services.chinese_short_name import short_chinese_name
from services.fee_category_matcher import match_fee_category
from services.product_calculator import (
    calculate_all,
    default_fulfillment_fee_zar,
)
from services.selection_import import (
    build_takealot_url,
    normalize_row,
    read_table,
    real_brand,
    to_number,
)

router = APIRouter(prefix="/api/selection", tags=["selection"])

# 与 models.Product 的默认值保持一致；改一处要同步另一处
INBOUND_FEE_CNY = 0.75
OUTBOUND_FEE_CNY = 0.70
LAST_MILE_FEE_CNY = 2.00
OTHER_FEE_CNY = 2.00

# 合格线：用户确认按 15% 算
QUALIFY_MARGIN = 0.15

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
ENRICH_CHUNK = 400          # 单次请求最多抓这么多个，剩下的由前端接着调
# 单次请求最多把多少秒花在「打网络」上。见 `pub.fetch_budget`：被限流时节流
# 会把间隔放到 2 秒，400 个能拖成十几分钟，而 commit 在最后——请求先被掐掉
# 就等于整块白抓。宁可多调几轮，也不要一次跑不完。
ENRICH_NETWORK_BUDGET_SECONDS = 45.0

# 这几种来源的价格才是「按现货卖家最低价」算出来的新口径，重抓时不用再打一遍
# 公开接口。其余来源（空字符串＝历史行、portal＝门户目录 bestPrice、import＝
# 从表格导入）都还是「前台展示价」或来路不明的数，得重新取一次。
PRECISE_PRICE_SOURCES = ("in_stock", "offer", "listed")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _exchange_rate(db: Session) -> float:
    from models import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == "cny_per_zar").first()
    if row:
        try:
            return float(row.value)
        except (TypeError, ValueError):
            pass
    return 0.41


def _fee_rate(db: Session, fee_category: Optional[str]) -> Optional[float]:
    if not fee_category:
        return None
    row = db.query(FeeCategory).filter(
        FeeCategory.name == fee_category, FeeCategory.active == True  # noqa: E712
    ).first()
    return row.success_fee_rate if row else None


def _derive_purchase_cost(item: SelectionItem) -> None:
    """供货单价 × 采购数量 = 采购价。

    供货单价有值时，采购价由它算出来，不再单独维护：两个都能手填必然打架，
    而且现实中你常常只知道单价、不知道这批总共花了多少。单价留空时采购价
    照旧手填（老用法不变），这样只拿到总价的那批数据也不会被清掉。
    """
    if item.unit_price_cny is None:
        return
    quantity = item.purchase_quantity or 4
    if quantity <= 0:
        return
    item.purchase_cost_cny = round(item.unit_price_cny * quantity, 2)


def _calc(item: SelectionItem, exchange_rate: float, fee_rate: Optional[float]) -> None:
    """把重尺+成本算成利润率，写回 item。

    `calculate_all` 里任一环缺失后面全 None，所以这里不需要自己判断完整性——
    缺什么就是 None，界面显示「待核算」。
    """
    # 放在最前面：导入、单条改、批量改、enrich、一键新建都会走 _calc，
    # 在这里派生能保证所有入口算出来的采购价是同一个口径。
    _derive_purchase_cost(item)
    payload = {
        "length_mm": item.length_mm,
        "width_mm": item.width_mm,
        "height_mm": item.height_mm,
        "actual_weight_kg": item.actual_weight_kg,
        "actual_sale_price_zar": item.actual_sale_price_zar,
        "purchase_cost_cny": item.purchase_cost_cny,
        "purchase_shipping_cny": item.purchase_shipping_cny,
        "purchase_quantity": item.purchase_quantity or 4,
        "packaging_cost_per_unit_cny": (
            item.packaging_cost_per_unit_cny
            if item.packaging_cost_per_unit_cny is not None else 1.0
        ),
        "shipping_method": item.shipping_method,
        "success_fee_rate": fee_rate,
        "inbound_listing_fee_cny": INBOUND_FEE_CNY,
        "outbound_operation_fee_cny": OUTBOUND_FEE_CNY,
        "last_mile_delivery_fee_cny": LAST_MILE_FEE_CNY,
        "other_fee_cny": OTHER_FEE_CNY,
        "fulfillment_fee_zar": (
            item.fulfillment_fee_zar
            if item.fulfillment_fee_zar is not None
            else default_fulfillment_fee_zar(item.fee_category)
        ),
        "fee_category": item.fee_category,
    }
    calculated = calculate_all(payload, exchange_rate)
    for key, value in calculated.items():
        if hasattr(item, key):
            setattr(item, key, value)
    item.fulfillment_fee_zar = payload["fulfillment_fee_zar"]


def _refresh_status(item: SelectionItem) -> None:
    if item.promoted_product_id:
        item.status = "已建产品"
        # 不再强制取消勾选：已建产品的行也能勾选，用来批量回推修改。
        return
    if item.profit_margin is None:
        item.status = "待核算"
        return
    item.status = "合格" if item.profit_margin >= QUALIFY_MARGIN else "不合格"


def _apply_offer_dims(item: SelectionItem, offer: Dict[str, Any]) -> bool:
    """卖家 API 的 offer 行里有重尺（cm / g），换算成 mm / kg。

    只在本店商品的 offer 上有效。返回是否真的写入了。
    """
    def number(key: str, scale: float, digits: int) -> Optional[float]:
        value = offer.get(key)
        if value in (None, ""):
            return None
        try:
            return round(float(value) * scale, digits)
        except (TypeError, ValueError):
            return None

    # 长度保留 1 位小数够了（0.1mm），重量必须 3 位——380g 写成 0.4kg 会让
    # 计费重量偏 5%，运费跟着偏。
    length = number("length_cm", 10, 1)
    width = number("width_cm", 10, 1)
    height = number("height_cm", 10, 1)
    weight = number("weight_grams", 0.001, 3)
    if length is None and width is None and height is None and weight is None:
        return False
    item.length_mm = length
    item.width_mm = width
    item.height_mm = height
    item.actual_weight_kg = weight
    item.dimensions_source = "seller_api"
    return True


def _copy_from_product_list(
    db: Session, store_id: str, items: List[SelectionItem]
) -> int:
    """自家商品直接从产品主档抄资料，返回抄到重尺的条数。

    为什么这是第一优先：销售报表只有 TSIN，而那些 TSIN 对应的商品**本来就在
    产品列表里**，重尺是你之前一个个填进去的。再走卖家 API 拉一遍既慢（1800 条
    要分块轮询）又可能把人工填的值冲掉。所以先抄主档，抄不到才走网络。

    抄的范围刻意收窄：只填**空着的**字段。报表带来的商品名/销售额不会被覆盖。
    """
    plids = sorted({i.plid for i in items if i.plid})
    tsins = sorted({i.tsin for i in items if i.tsin})
    if not plids and not tsins:
        return 0

    conditions = []
    if plids:
        conditions.append(Product.plid.in_(plids))
    if tsins:
        conditions.append(Product.tsin.in_(tsins))
    rows = (
        db.query(Product)
        .filter(Product.store_id == store_id, or_(*conditions))
        .all()
    )
    by_plid: Dict[str, Product] = {}
    by_tsin: Dict[str, Product] = {}
    for row in rows:
        if row.plid:
            by_plid.setdefault(row.plid, row)
        if row.tsin:
            by_tsin.setdefault(row.tsin, row)

    copied = 0
    for item in items:
        product = by_plid.get(item.plid) if item.plid else None
        if product is None and item.tsin:
            product = by_tsin.get(item.tsin)
        if product is None:
            continue

        if not item.plid and product.plid:
            item.plid = product.plid
        if not item.tsin and product.tsin:
            item.tsin = product.tsin
        if not item.product_name and product.product_name:
            item.product_name = product.product_name
        if not item.chinese_product_name and product.chinese_product_name:
            # 产品主档里的品名是人填的，照抄过来也算「人填」
            item.chinese_product_name = product.chinese_product_name
            item.chinese_name_auto = False
        if not item.product_image_url and product.product_image_url:
            item.product_image_url = product.product_image_url
        if not item.takealot_url and product.takealot_url:
            item.takealot_url = product.takealot_url
        if item.actual_sale_price_zar is None and product.actual_sale_price_zar is not None:
            item.actual_sale_price_zar = product.actual_sale_price_zar
        if item.rating_value is None and product.rating_value is not None:
            item.rating_value = product.rating_value
        if item.review_count is None and product.review_count is not None:
            item.review_count = product.review_count
        if not item.fee_category and product.fee_category:
            # 主档里的类目是人确认过的（新建产品时看过），比推断的可靠
            item.fee_category = product.fee_category
            item.fee_category_confidence = "high"
            item.fee_category_reason = "沿用产品列表里已确认的类目"

        has_dims = (
            item.length_mm is not None and item.width_mm is not None
            and item.height_mm is not None and item.actual_weight_kg is not None
        )
        if not has_dims and not item.dimensions_source:
            dims = (product.length_mm, product.width_mm,
                    product.height_mm, product.actual_weight_kg)
            if all(value is not None for value in dims):
                item.length_mm, item.width_mm, item.height_mm, item.actual_weight_kg = dims
                item.dimensions_source = "product_list"
                copied += 1
        elif item.dimensions_source == "product_list":
            copied += 1
    return copied


def _fill_catalogue_dims(db: Session, items: List[SelectionItem]) -> int:
    """竞品重尺：走卖家后台「Search Takealot's Catalogue」那套目录接口。

    仅复用有效的已保存连接；检查接口不发现或写入新连接。
    拿不到的留空，等人工填——绝不用类目默认值冒充。
    """
    from services.takealot_catalogue_api import fetch_dims_many, load_winner, configured_key

    winner = load_winner(db)
    if not winner:
        return 0
    key = configured_key(db)
    if not key:
        return 0

    eligible = [item for item in items if not item.dimensions_source and item.plid]
    records = fetch_dims_many([item.plid for item in eligible], key, winner)
    filled = 0
    for item in eligible:
        dims = records.get(str(item.plid).strip())
        if not dims:
            continue
        item.length_mm = dims.get("length_mm")
        item.width_mm = dims.get("width_mm")
        item.height_mm = dims.get("height_mm")
        item.actual_weight_kg = dims.get("weight_kg")
        item.dimensions_source = "catalogue_api"
        filled += 1
    return filled


def _portal_may_overwrite_price(item: SelectionItem) -> bool:
    """门户目录价能不能写进这一行。

    以前只在「售价为空」时写，结果历史行永远停在旧值：1700 多条存量数据的售价
    是门户 bestPrice（很多还是更早那次抓的前台展示价），用户点「重抓全部资料」
    也换不掉，看到的还是贵的那条。现在放开成「只要不是人定的、也不是更准的口径」
    就覆盖：

    - `manual`：用户核对过，绝不动。
    - `import`：表格导入时自带的价，是用户自己的测算口径，门户这种粗口径不许盖它
      （公开接口拿到的是「现货最低价」，那一轮仍然会换掉，见 `_apply_public`）。
    - `in_stock` / `offer` / `listed`：公开接口拆出来的新口径，比门户的目录价准，
      不能被盖回前台展示价（否则重抓一次反而变贵）。
    - 空 / `portal`：历史行或上一轮门户写的，可以刷。
    """
    if item.price_source in ("manual", "import"):
        return False
    if item.price_source in PRECISE_PRICE_SOURCES:
        return False
    return True


def _fill_portal(db: Session, items: List[SelectionItem],
                 plids: List[str]) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """卖家门户目录接口：主图 + 图库 + 类目 + 价格 + 重尺，一次拿全。

    为什么它排在最前面
    ------------------
    公开接口 `api.takealot.com` 从机房 IP 批量抓会被 Cloudflare 429（1804 个
    候选只有 632 个拿到主图），而且**根本没有**长宽高和重量。卖家门户自己的
    「Search Takealot's Catalogue」走的是另一套主机，实测不限流、且一个请求
    就把图和重尺都给全了。

    没配令牌时整段跳过，退回原来的公开接口路径——不影响已经能用的场景。
    """
    from services import takealot_portal_api as portal

    token = portal.configured_token(db)
    if not token or not plids:
        return {}, 0

    records = portal.fetch_many(plids, token)
    by_plid = {str(record.get("plid") or ""): record for record in records}
    dims_filled = 0

    for item in items:
        record = by_plid.get(str(item.plid or "").strip())
        if not record:
            continue
        if record.get("success"):
            if not item.product_name:
                item.product_name = record.get("product_name") or ""
            if not item.product_image_url:
                item.product_image_url = record.get("product_image_url") or ""
            if not item.image_urls and record.get("image_urls"):
                item.image_urls = json.dumps(record["image_urls"], ensure_ascii=False)
            if record.get("actual_sale_price_zar") is not None and _portal_may_overwrite_price(item):
                item.actual_sale_price_zar = record["actual_sale_price_zar"]
                # 门户给的是目录里的 bestPrice，公开接口会给更细的三个口径，
                # 所以这里标成 portal，公开接口那轮会把它换掉。
                item.price_source = "portal"
            if not item.takealot_category_path and record.get("takealot_category_path"):
                item.takealot_category_path = record["takealot_category_path"]
            if not item.tsin and record.get("tsin"):
                item.tsin = record["tsin"]
            # 蓝色品牌名。有品牌的商品要清出去（品牌授权/侵权风险），所以这个
            # 字段得先抓回来——以前门户不返回品牌，靠它判断就永远判不出来。
            if record.get("brand"):
                item.brand = record["brand"]
            if not item.takealot_url:
                item.takealot_url = build_takealot_url(item.plid)
            # 门户给了描述和「包装里有什么」，比标题可靠得多——重判一次带电。
            # 只在「还没结论」或「只是可能」时重判：已经有明确结论的（含用户
            # 手工改过的运输方式）不动，避免每轮抓取把人的判断冲掉。
            if item.battery_status in ("", "maybe"):
                verdict, reason = guess_battery(
                    item.product_name,
                    record.get("description"),
                    record.get("whats_in_the_box"),
                )
                if verdict:
                    previous = item.shipping_method or ""
                    item.battery_status = verdict
                    item.battery_reason = reason
                    # 只有当运输方式还是「我们之前按旧判定自动填的那个」时才跟着改。
                    # 用户手改成别的（比如改走空运）就保留他的选择。
                    if previous in ("", suggested_shipping("")):
                        item.shipping_method = suggested_shipping(verdict)
            # 类目路径拿到了就顺手定费率类目。放在这里而不是只靠 `_apply_public`：
            # 否则门户已经补齐的行会因为「还没有 fee_category」被再判一次待抓，
            # 白白多打一轮公开接口。
            if not item.fee_category and item.takealot_category_path:
                matched = match_fee_category(db, item.takealot_category_path, item.product_name)
                if matched.get("fee_category"):
                    item.fee_category = matched["fee_category"]
                    item.fee_category_confidence = matched.get("confidence") or ""
                    item.fee_category_reason = matched.get("match_reason") or ""
            if not item.dimensions_source:
                dims = record.get("dimensions") or {}
                if (dims.get("length_mm") and dims.get("width_mm")
                        and dims.get("height_mm")):
                    item.length_mm = dims["length_mm"]
                    item.width_mm = dims["width_mm"]
                    item.height_mm = dims["height_mm"]
                    if dims.get("weight_kg"):
                        item.actual_weight_kg = dims["weight_kg"]
                    item.dimensions_source = "portal"
                    dims_filled += 1
                else:
                    # 门户确实没有重尺。记一笔，免得每轮重抓都白跑一趟。
                    item.dimensions_source = "portal_empty"
        elif record.get("not_found"):
            # 目录里没有这个 PLID：重尺永远补不上，别让它一直卡在待抓队列。
            if not item.dimensions_source:
                item.dimensions_source = "portal_empty"
    return by_plid, dims_filled


def _apply_public(item: SelectionItem, record: Optional[Dict[str, Any]], db: Session) -> None:
    """把公开接口的一条结果写进 item。"""
    if not record or not record.get("success"):
        # 主档里已经把名字和主图给全的，本来就没走公开接口——这不是失败。
        # 不区分的话，1800 条自家商品会整片标成「没抓到」。
        access_blocked = record and (
            record.get("public_access_blocked") or record.get("http_status") in pub.ACCESS_STOP_STATUS
        )
        if not access_blocked and item.product_image_url and item.product_name:
            item.enrich_status = "ok"
            item.enrich_message = "资料取自产品列表，未走公开接口"
            return
        # 429 是**限流**，不是拒绝——过一会儿还能读；被 stop 波及、自己压根没
        # 拿到状态码的行也只是「没轮到」。这两种都不能标 failed：`remaining`
        # 只统计 enrich_status 为空的行，标了 failed 就永久退出待抓队列，
        # 用户点多少次「重抓」都补不上——这正是「反复抓仍有一片没主图」的原因。
        if record and (record.get("http_status") == 429
                       or record.get("public_access_blocked")):
            item.enrich_status = ""
            item.enrich_message = "公开接口限流，已留待下一轮重试"
            return
        item.enrich_status = "failed"
        warnings = (record or {}).get("warnings") or []
        item.enrich_message = "；".join(warnings) or "公开接口没有返回可用数据"
        return

    if not item.product_name:
        item.product_name = record.get("product_name") or ""
    if not item.product_image_url:
        item.product_image_url = record.get("product_image_url") or ""
    images = record.get("image_urls") or []
    if images:
        item.image_urls = json.dumps(images, ensure_ascii=False)
    if record.get("actual_sale_price_zar") is not None:
        # 人工改过售价就不再被覆盖：那是用户核对过的数，重抓不该把它冲掉
        # （重尺和中文品名都是这么处理的）。
        if item.price_source != "manual":
            item.actual_sale_price_zar = record["actual_sale_price_zar"]
            # 没给口径就按「前台展示价」算：只有一个价可拿的时候，那个价就是前台
            # 展示的那条。留空会让这一行永远被当成「还没换到新口径」，重抓时反复
            # 被选中、remaining 永远清不掉。
            item.price_source = record.get("price_source") or "listed"
    if record.get("listed_price_zar") is not None:
        item.listed_price_zar = record["listed_price_zar"]
    if record.get("lowest_in_stock_price_zar") is not None:
        item.lowest_in_stock_price_zar = record["lowest_in_stock_price_zar"]
    if record.get("lowest_offer_price_zar") is not None:
        item.lowest_offer_price_zar = record["lowest_offer_price_zar"]
    if record.get("seller_offer_count") is not None:
        item.seller_offer_count = record["seller_offer_count"]
    if record.get("rating_value") is not None:
        item.rating_value = record["rating_value"]
    if record.get("review_count") is not None:
        item.review_count = record["review_count"]
    if record.get("brand"):
        item.brand = record["brand"]
    if record.get("takealot_category_path"):
        item.takealot_category_path = record["takealot_category_path"]
    if not item.tsin and record.get("tsin"):
        item.tsin = record["tsin"]
    if not item.takealot_url:
        item.takealot_url = build_takealot_url(item.plid)

    # 重尺：只在不已有来源时预填。公开页的属性覆盖率和格式都不够可靠，
    # 一旦有人工填过（dimensions_source == manual）就不能被覆盖。
    if not item.dimensions_source:
        dims = record.get("dimensions") or {}
        if dims.get("length_mm") and dims.get("width_mm") and dims.get("height_mm"):
            item.length_mm = dims["length_mm"]
            item.width_mm = dims["width_mm"]
            item.height_mm = dims["height_mm"]
            if dims.get("weight_kg"):
                item.actual_weight_kg = dims["weight_kg"]
            item.dimensions_source = "public_api"

    if not item.fee_category and item.takealot_category_path:
        matched = match_fee_category(db, item.takealot_category_path, item.product_name)
        if matched.get("fee_category"):
            item.fee_category = matched["fee_category"]
            item.fee_category_confidence = matched.get("confidence") or ""
            item.fee_category_reason = matched.get("match_reason") or ""

    item.enrich_status = "ok"
    item.enrich_message = ""


def _assign_tiers(items: List[SelectionItem]) -> None:
    """帕累托分档：按累计净销售额切 S(≤50%) / A(≤80%) / B(≤95%) / C。

    只在有销售数据时执行。竞品表没有销售额，硬套会让所有商品都变 S 档。
    """
    rows = [i for i in items if i.net_sales_zar and i.net_sales_zar > 0]
    if not rows:
        return
    total = sum(i.net_sales_zar for i in rows)
    if total <= 0:
        return
    rows.sort(key=lambda x: -(x.net_sales_zar or 0))
    cumulative = 0.0
    for item in rows:
        cumulative += item.net_sales_zar or 0
        share = cumulative / total
        if share <= 0.5:
            item.tier = "S"
        elif share <= 0.8:
            item.tier = "A"
        elif share <= 0.95:
            item.tier = "B"
        else:
            item.tier = "C"


def _to_dict(item: SelectionItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "batch_name": item.batch_name,
        "plid": item.plid,
        "tsin": item.tsin,
        "takealot_url": item.takealot_url,
        "product_name": item.product_name,
        "chinese_product_name": item.chinese_product_name,
        "brand": item.brand,
        "product_image_url": item.product_image_url,
        "image_urls": json.loads(item.image_urls) if item.image_urls else [],
        "takealot_category_path": item.takealot_category_path,
        "fee_category": item.fee_category,
        "fee_category_confidence": item.fee_category_confidence,
        "fee_category_reason": item.fee_category_reason,
        "actual_sale_price_zar": item.actual_sale_price_zar,
        "listed_price_zar": item.listed_price_zar,
        "lowest_in_stock_price_zar": item.lowest_in_stock_price_zar,
        "lowest_offer_price_zar": item.lowest_offer_price_zar,
        "price_source": item.price_source or "",
        "seller_offer_count": item.seller_offer_count,
        "rating_value": item.rating_value,
        "review_count": item.review_count,
        "sales_qty": item.sales_qty,
        "net_sales_zar": item.net_sales_zar,
        "gross_sales_zar": item.gross_sales_zar,
        "net_sales_cny": item.net_sales_cny,
        "tier": item.tier,
        "already_in_products": bool(item.already_in_products),
        "promoted_product_id": item.promoted_product_id,
        "pool_dirty": bool(item.pool_dirty),
        "promoted_synced_at": item.promoted_synced_at.isoformat() if item.promoted_synced_at else None,
        "length_mm": item.length_mm,
        "width_mm": item.width_mm,
        "height_mm": item.height_mm,
        "actual_weight_kg": item.actual_weight_kg,
        "dimensions_source": item.dimensions_source,
        "purchase_url": item.purchase_url,
        "purchase_cost_cny": item.purchase_cost_cny,
        "purchase_shipping_cny": item.purchase_shipping_cny,
        "purchase_quantity": item.purchase_quantity,
        "unit_price_cny": item.unit_price_cny,
        "packaging_cost_per_unit_cny": item.packaging_cost_per_unit_cny,
        "shipping_method": item.shipping_method,
        "battery_status": item.battery_status or "",
        "battery_reason": item.battery_reason or "",
        "fulfillment_fee_zar": item.fulfillment_fee_zar,
        "success_fee_zar": item.success_fee_zar,
        "chargeable_weight_kg": item.chargeable_weight_kg,
        "unit_product_cost_cny": item.unit_product_cost_cny,
        "international_shipping_per_unit_cny": item.international_shipping_per_unit_cny,
        "cost_to_sa_warehouse_zar": item.cost_to_sa_warehouse_zar,
        "total_cost_zar": item.total_cost_zar,
        "profit_zar": item.profit_zar,
        "profit_margin": item.profit_margin,
        "status": item.status,
        "selected": bool(item.selected),
        "enrich_status": item.enrich_status,
        "enrich_message": item.enrich_message,
        "note": item.note,
    }


# ---------------------------------------------------------------------------
# 1. 导入
# ---------------------------------------------------------------------------

@router.post("/import")
async def import_table(
    file: UploadFile = File(...),
    batch_name: str = Form(""),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """上传 xlsx / csv，按商品聚合后落进选品池。

    **必须聚合**：销售报表是一行一个订单行，直接落库 13,984 行会变成 13,984
    张卡片。这里按 PLID → TSIN → 标题 的优先级归并，销量和净销售额累加，
    归并前的行数记在返回值里，方便核对。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 30MB，请拆分后再上传")

    try:
        parsed = read_table(file.filename or "", content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:  # 损坏的 xlsx 会在 openpyxl 里抛各种异常
        raise HTTPException(status_code=400, detail=f"读表失败：{type(error).__name__}")

    rows = parsed["rows"]
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="没读到有效行。表头里至少要有一列能被识别："
                   "产品链接 / 产品名称 / TSIN / PLID",
        )

    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    skipped = 0
    for index, raw in enumerate(rows):
        row = normalize_row(raw)
        for field in ("unit_price_cny", "purchase_cost_cny", "purchase_shipping_cny", "purchase_quantity"):
            if raw.get(field) in (None, ""):
                continue
            value = to_number(raw[field])
            if value is None or not math.isfinite(value) or value < 0 or (
                field == "purchase_quantity" and (value < 1 or not float(value).is_integer())
            ):
                raise HTTPException(status_code=400, detail=f"第{index + 2}行{field}无效，请核对来源数据")
        if row["plid"]:
            key = f"plid:{row['plid']}"
        elif row["tsin"]:
            key = f"tsin:{row['tsin']}"
        elif row["product_name"]:
            key = f"title:{row['product_name'].strip().lower()}"
        else:
            skipped += 1
            continue

        current = groups.get(key)
        if current is None:
            current = dict(row)
            current["_rows"] = 1
            current["_row_no"] = index + 2      # +2：1 行表头，Excel 行号从 1 起
            groups[key] = current
            order.append(key)
            continue

        current["_rows"] += 1
        for field in ("sales_qty", "net_sales_zar", "gross_sales_zar"):
            value = row[field]
            if value is not None:
                current[field] = (current[field] or 0) + value
        for field in ("actual_sale_price_zar", "rating_value", "review_count",
                      "takealot_url", "chinese_product_name", "product_name",
                      "purchase_url", "unit_price_cny", "purchase_cost_cny",
                      "purchase_shipping_cny", "purchase_quantity", "length_mm",
                      "width_mm", "height_mm", "actual_weight_kg"):
            if current.get(field) in (None, "") and row.get(field) not in (None, ""):
                current[field] = row[field]

    if not groups:
        raise HTTPException(status_code=400, detail="所有行都没能识别出商品身份")

    exchange_rate = _exchange_rate(db)
    batch_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    label = (batch_name or "").strip() or (file.filename or "未命名批次")

    # 已在产品列表里的、有品牌的、池子里已经有的，导入时直接不建。
    #
    # 以前是「建出来再打个标记」（`already_in_products`），结果 1804 条里有
    # 25 条重复、22 条有品牌混在里面，还得人工挑。现在三道过滤都放在这里：
    #   1. 有品牌 —— 品牌授权/侵权风险，不做
    #   2. 已在产品主档 —— 已经上架过的，不用再选一次
    #   3. 池子里已有同 PLID —— 重复导入不该翻倍
    candidate_plids = [g["plid"] for g in groups.values() if g["plid"]]
    candidate_tsins = [g["tsin"] for g in groups.values() if g["tsin"]]

    existing_plids: set = set()
    existing_tsins: set = set()
    for start in range(0, len(candidate_plids), 500):
        chunk = candidate_plids[start:start + 500]
        existing_plids |= {
            row[0] for row in db.query(Product.plid).filter(
                Product.store_id == store_id, Product.plid.in_(chunk)
            ).all()
        }
    for start in range(0, len(candidate_tsins), 500):
        chunk = candidate_tsins[start:start + 500]
        existing_tsins |= {
            row[0] for row in db.query(Product.tsin).filter(
                Product.store_id == store_id, Product.tsin.in_(chunk)
            ).all()
        }

    pooled_plids: set = set()
    for start in range(0, len(candidate_plids), 500):
        chunk = candidate_plids[start:start + 500]
        pooled_plids |= {
            row[0] for row in db.query(SelectionItem.plid).filter(
                SelectionItem.store_id == store_id,
                SelectionItem.plid.in_(chunk),
                SelectionItem.plid != "",
            ).all()
        }

    items: List[SelectionItem] = []
    duplicates = 0
    pool_duplicates = 0
    branded = 0
    for key in order:
        group = groups[key]
        if real_brand(group.get("brand")):
            branded += 1
            continue
        already = (
            (bool(group["plid"]) and group["plid"] in existing_plids)
            or (bool(group["tsin"]) and group["tsin"] in existing_tsins)
        )
        if already:
            duplicates += 1
            continue
        if group["plid"] and group["plid"] in pooled_plids:
            pool_duplicates += 1
            continue
        # 表里自带中文品名就用表里的（人工填的优先），没有才按标题现生成。
        # 生成不出来留空——品名要印标签、进报关单，宁缺勿错。
        auto_name = "" if group["chinese_product_name"] else short_chinese_name(group["product_name"])
        item = SelectionItem(
            store_id=store_id,
            batch_id=batch_id,
            batch_name=label,
            source_row=group["_row_no"],
            plid=group["plid"],
            tsin=group["tsin"],
            takealot_url=group["takealot_url"] or build_takealot_url(group["plid"]),
            product_name=group["product_name"],
            chinese_product_name=group["chinese_product_name"] or auto_name,
            # `chinese_name_auto` 记来源：只有机器起的名字才允许日后按新词表重算。
            chinese_name_auto=bool(auto_name),
            actual_sale_price_zar=group["actual_sale_price_zar"],
            # 表里带来的售价标成 import：抓资料时会用「现货最低价」换掉它，
            # 但如果用户在卡片上自己改过（manual），就不再动。
            price_source="import" if group["actual_sale_price_zar"] is not None else "",
            rating_value=group["rating_value"],
            review_count=group["review_count"],
            sales_qty=group["sales_qty"],
            net_sales_zar=group["net_sales_zar"],
            gross_sales_zar=group["gross_sales_zar"],
            net_sales_cny=(
                round(group["net_sales_zar"] * exchange_rate, 2)
                if group["net_sales_zar"] is not None else None
            ),
            # 走到这里说明前面三道过滤都没命中，所以不可能是重复
            already_in_products=False,
            purchase_url=group["purchase_url"],
            unit_price_cny=group["unit_price_cny"],
            purchase_cost_cny=group["purchase_cost_cny"],
            purchase_shipping_cny=group["purchase_shipping_cny"],
            purchase_quantity=group["purchase_quantity"] if group["purchase_quantity"] is not None else 4,
            dimensions_source="import" if any(group[f] is not None for f in
                ("length_mm", "width_mm", "height_mm", "actual_weight_kg")) else "",
            length_mm=group["length_mm"],
            width_mm=group["width_mm"],
            height_mm=group["height_mm"],
            actual_weight_kg=group["actual_weight_kg"],
            note=group["note"],
            status="待核算",
        )
        # 普货 / 带电先按标题判一次，运输方式跟着判定走（默认海运）。
        # 拿到 Takealot 的描述后 `_fill_portal` 会用更全的文本再判一次。
        verdict, reason = guess_battery(group["product_name"], group["note"])
        item.battery_status = verdict
        item.battery_reason = reason
        item.shipping_method = suggested_shipping(verdict)
        _derive_purchase_cost(item)
        items.append(item)

    _assign_tiers(items)
    db.add_all(items)
    db.commit()

    return {
        "batch_id": batch_id,
        "batch_name": label,
        "sheet": parsed["sheet"],
        "source_rows": parsed["total_rows"],
        "merged_rows": len(rows),
        "created": len(items),
        # 三个过滤各自挡掉多少，前端要分开说——「有品牌」和「重复」是两件事，
        # 混成一个数字用户看不出到底为什么少了这么多行。
        "already_in_products": duplicates,
        "branded": branded,
        "pool_duplicates": pool_duplicates,
        "skipped": skipped,
        "with_sales_data": sum(1 for i in items if i.net_sales_zar),
        "columns": {k: v for k, v in parsed["mapping"].items()},
        "unknown_headers": parsed["unknown_headers"],
    }


# ---------------------------------------------------------------------------
# 2. 抓资料
# ---------------------------------------------------------------------------

class EnrichRequest(BaseModel):
    batch_id: Optional[str] = None
    ids: Optional[List[str]] = None
    limit: int = Field(default=ENRICH_CHUNK, ge=1, le=800)
    refresh: bool = False   # True 时连已抓过的也重抓
    # 跳过已经算出利润率的行。测算过的商品，价格/重尺/成本都是核对过的，
    # 再抓一遍既浪费配额，也可能把刚调好的售价换回去。默认不跳过（老调用方
    # 的行为不变），由界面上的开关决定。
    skip_measured: bool = False


# 「还没结论」的行，`enrich` 的目标查询和 `remaining` 都必须用同一套判定。
#
# 三类：从没抓过（空）、被限流留待下一轮（空）、以及旧版本把 429 直接标成
# `failed` 的历史行。最后一类不能漏——线上有 1112 行是这么卡住的：它们既不在
# 目标查询里、也不在 `remaining` 里，于是「点抓取什么也不发生」，用户看到的就是
# 「429 一直没解决、重尺也补不上」。限流不是结论，重新排队就是了。
RETRYABLE_FAILURE_PATTERNS = ("%429%", "%限流%")


def _pending_rows():
    return or_(
        SelectionItem.enrich_status == "",
        SelectionItem.enrich_status.is_(None),
        and_(
            SelectionItem.enrich_status == "failed",
            or_(*[SelectionItem.enrich_message.like(p) for p in RETRYABLE_FAILURE_PATTERNS]),
        ),
    )


def _backfill_battery(db: Session, query) -> int:
    """按标题给还没有结论的行判一次普货/带电，返回判出结论的条数。

    和品名一样是纯本地正则，不花门户配额，所以放在 `enrich` 最前面——这一轮
    即使没有要抓的行，历史数据也能一次补齐。

    只补「还没结论」的行：已经有 yes / no 的（包括人工在卡片上点过的）不动。
    运输方式的处理分两种：
      - 空着 → 按判定填（默认海运，判成带电就填「海运带电」）
      - 已经是「海运普货」而判定是带电 → 改成「海运带电」
    后一种是**故意**的：带电报普货会被货代退运，这个方向的错误必须自动纠正。
    反过来用户手选了空运的，一个字都不碰。
    """
    rows = query.filter(
        or_(
            SelectionItem.battery_status == "",
            SelectionItem.battery_status.is_(None),
            SelectionItem.shipping_method == "",
            SelectionItem.shipping_method.is_(None),
        )
    ).all()

    judged = 0
    touched = 0
    for item in rows:
        status = item.battery_status or ""
        if not status:
            verdict, reason = guess_battery(item.product_name, item.note)
            if verdict:
                item.battery_status = status = verdict
                item.battery_reason = reason
                judged += 1

        if not item.shipping_method:
            item.shipping_method = suggested_shipping(status)
            touched += 1
        elif status == "yes" and item.shipping_method == SEA_GENERAL:
            item.shipping_method = SEA_BATTERY
            touched += 1

    if judged or touched:
        db.commit()
    return judged


def _backfill_chinese_names(db: Session, query) -> int:
    """补 / 刷新中文品名，返回动了几条。

    纯本地词表匹配、不发网络请求，所以放在 `enrich` 最前面——这一轮即使没有
    要抓的行，品名也能补齐。空标题、一个词都对不上的标题保持为空（不猜）。

    `chinese_name_auto` 是三态：
      True  机器起的 → 跟着新词表重算
      False 人填的   → 一个字都不动
      None  来源未知（本列上线前就有的老数据）→ 也重算，那批名字是更早的
            翻译器拼出来的（"女士袋黑"、"XL\\nL\\n3XL"），不重算就永远错着
    """
    filled = 0
    for item in query.all():
        name = item.chinese_product_name or ""
        if name and item.chinese_name_auto is False:
            continue  # 人填的，不碰
        fresh = short_chinese_name(item.product_name)
        if not fresh or fresh == name:
            continue
        item.chinese_product_name = fresh
        item.chinese_name_auto = True
        filled += 1
    if filled:
        db.commit()
    return filled


@router.post("/enrich")
def enrich(
    data: EnrichRequest,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """抓主图 / 类目 / 价格；自家商品顺带把重尺也填了。

    分块返回：一次最多 800 个，`remaining` 不为 0 就说明还有，前端接着调。
    """
    query = db.query(SelectionItem).filter(SelectionItem.store_id == store_id)
    if data.ids:
        query = query.filter(SelectionItem.id.in_(data.ids))
    elif data.batch_id:
        query = query.filter(SelectionItem.batch_id == data.batch_id)
    query = query.filter(SelectionItem.promoted_product_id.is_(None))
    scoped_query = query  # 后续计数复用同一店铺/ids/批次范围，不带本轮limit。

    # 中文品名和普货/带电判定都是纯本地计算，先补上——这两步不花配额、不会失败，
    # 所以放在最前面，保证「即使这轮没有要抓的行」历史数据也能一次补齐。
    #
    # 注意这两步用的是**没被 skip_measured 收窄**的 scoped_query：已测算的行也
    # 可能缺中文品名，本地补一下不花配额，没有理由跳过。
    names_filled = _backfill_chinese_names(db, scoped_query)
    battery_judged = _backfill_battery(db, scoped_query)

    if data.skip_measured:
        # 测算过的行不再抓网络。利润率的唯一判据就是 profit_margin 有值（见
        # `_calc`：重尺/成本/售价任一缺失它都是 None），所以按它一刀切。
        #
        # scoped_query 必须跟着收窄：`remaining` 和「价格还没换口径」的计数都用它，
        # 不收窄的话这些被跳过的行会一直被算成「还没处理完」，前端每一轮都以为
        # 还有活干。
        query = query.filter(SelectionItem.profit_margin.is_(None))
        scoped_query = query

    # 有可用的门户通道时，「已经 ok 但还没有重尺」的行也值得再跑一轮：门户一个
    # 请求就同时给图和 Takealot 实测的长宽高，补一次就到位。
    #
    # 为什么必须卡着「门户可用」这个条件：没有通道时公开接口根本没有重尺，这些行
    # 会每一轮都白打一次网络（而且大概率还是 429），永远收敛不了。有了通道之后
    # 它们要么拿到 `portal`，要么被标成 `portal_empty`，下一轮就不再入选。
    from services import takealot_portal_api as portal_api
    portal_ready = bool(portal_api.configured_token(db)) and not portal_api.token_state(db).get("expired")

    pending_filter = _pending_rows()
    if portal_ready:
        pending_filter = or_(
            pending_filter,
            and_(
                SelectionItem.enrich_status == "ok",
                or_(SelectionItem.dimensions_source == "",
                    SelectionItem.dimensions_source.is_(None)),
                SelectionItem.plid.isnot(None),
                SelectionItem.plid != "",
            ),
        )
    if not data.refresh:
        query = query.filter(pending_filter)
        targets = query.order_by(SelectionItem.created_at).limit(data.limit).all()
    else:
        # 重抓：**价格还没落到新口径的行排前面**。
        #
        # 不排的话，一次只处理 400 个、且固定按 created_at 取前 400 个，历史
        # 1700 多行里永远只有最早那 400 行被重算，剩下的一直停在旧的前台展示价
        # ——用户点多少次「重抓全部资料」，售价都不变。按「缺什么先补什么」排序后，
        # 每一块都在推进，前端按 `remaining` 接着调就能收敛。
        needs_price = case(
            (SelectionItem.price_source.in_(PRECISE_PRICE_SOURCES), 1), else_=0,
        )
        targets = (query.order_by(needs_price, SelectionItem.created_at)
                   .limit(data.limit).all())

    # 本轮开始前记下价格来源：收尾时用它算「这一轮真把几条换成了新口径」。
    # 前端靠这个数判断「还有没有进展」，否则抓不到价格的行会让它空转到轮次上限。
    price_source_before = {item.id: (item.price_source or "") for item in targets}

    if not targets:
        # 字段要和正常返回一致：前端读 `pending` / `portal_*` 时不该突然是
        # undefined，否则收尾文案会漏掉「还有多少没抓」。
        #
        # `portal_configured` 用真实值而不是写死 False：令牌配着、只是这轮没东西
        # 可抓的时候，前端不该提示「去配令牌」。
        return {"processed": 0, "ok": 0, "failed": 0, "pending": 0,
                "with_dimensions": 0, "from_product_list": 0, "skipped_network": 0,
                "remaining": 0, "seller_api": "", "failures": [],
                "names_filled": names_filled, "battery_judged": battery_judged,
                "price_fixed": 0,
                "portal_configured": portal_ready, "portal_limited": False,
                "portal_retry_after": 0.0}

    # --- 第一优先：产品主档。自家商品的图/类目/重尺库里已经有了 ---
    #
    # 这是「销售报表只有 TSIN」的主路径：那些商品本来就在产品列表里，重尺是你
    # 之前填过的。再去卖家 API 拉一遍等于重复劳动，还可能把人工填的值覆盖掉。
    # 抄完之后，缺东西的才走网络。
    from_product_list = _copy_from_product_list(db, store_id, targets)

    # --- 第二优先：卖家 API（本店 offer，能把 TSIN 反查成 PLID）---
    offer_by_plid: Dict[str, Dict[str, Any]] = {}
    offer_by_tsin: Dict[str, Dict[str, Any]] = {}
    seller_note = "未配置卖家 API 密钥"
    try:
        from services.takealot_seller_api import _load_offers, configured_key
        key = configured_key(db)
        if key:
            offers = _load_offers(key)
            for offer in offers:
                plid = str(offer.get("productline_id") or "")
                tsin = str(offer.get("tsin_id") or "")
                if plid:
                    offer_by_plid[plid] = offer
                if tsin:
                    offer_by_tsin[tsin] = offer
            seller_note = f"已读取本店 {len(offers)} 个 offer"
        else:
            seller_note = "未配置卖家 API 密钥"
    except Exception as error:
        seller_note = f"卖家接口不可用：{error}"

    # 用 TSIN 反查 PLID，并把本店重尺填上（已经有重尺来源的不动）
    dims_from_seller = 0
    for item in targets:
        offer = None
        if item.plid and item.plid in offer_by_plid:
            offer = offer_by_plid[item.plid]
        elif item.tsin and item.tsin in offer_by_tsin:
            offer = offer_by_tsin[item.tsin]
            if not item.plid:
                item.plid = str(offer.get("productline_id") or "")
                if not item.takealot_url:
                    item.takealot_url = build_takealot_url(item.plid)
        if not offer:
            continue
        if not item.dimensions_source and _apply_offer_dims(item, offer):
            dims_from_seller += 1
        if not item.product_name:
            item.product_name = str(offer.get("title") or "")
        if not item.product_image_url:
            item.product_image_url = str(offer.get("image_url") or "")

    # --- 第三优先：竞品重尺走卖家目录接口（已有保存连接）---
    from services.takealot_catalogue_api import CatalogueAccessDenied, CatalogueReadPending
    try:
        dims_from_catalogue = _fill_catalogue_dims(db, targets)
    except CatalogueAccessDenied as error:
        # 本轮事务未提交：保留之前已保存资料，不把未完成抓取伪装成成功。
        db.rollback()
        raise HTTPException(status_code=424, detail=str(error)) from None

    except CatalogueReadPending as error:
        db.rollback()
        raise HTTPException(status_code=504, detail=str(error)) from None

    # --- 卖家门户目录接口：主图 + 类目 + 价格 + 重尺，一次拿全 ---
    #
    # 排在最前面是因为它**不限流**且**重尺只有它给**。公开接口那套要放慢到
    # 0.2 秒一次还照样吃 429，1804 个商品抓下来一半没主图；这里连打 20 次全
    # 200。所以先走它，剩下的零头才回落到公开接口。
    need_portal = [
        item.plid for item in targets
        if item.plid and (
            data.refresh
            or not item.product_image_url
            or item.actual_sale_price_zar is None
            or not item.fee_category
            or not item.dimensions_source
        )
    ]
    need_portal = list(dict.fromkeys(need_portal))
    portal_records: Dict[str, Dict[str, Any]] = {}
    portal_deferred: set = set()
    dims_from_portal = 0
    portal_configured = False
    portal_retry_after = 0.0
    if need_portal:
        from services import takealot_portal_api as _portal
        portal_configured = portal_ready
        portal_records, dims_from_portal = _fill_portal(db, targets, need_portal)
        portal_deferred = {
            plid for plid, record in portal_records.items()
            if record.get("budget_deferred")
        }
        # 配额见底时这一轮可能一条都抓不到。把「还要等多久」明确回给前端，
        # 否则它会立刻再发一轮、再被整批推迟，白白空转几十次。
        if portal_deferred:
            portal_retry_after = round(_portal.QUOTA.wait_seconds(), 1)

    # --- 公开接口补齐门户没覆盖到的（没配令牌 / 目录里没有 / 令牌过期）---
    #
    # 主档里已经把图/类目/价格都给全的，不用再打一次网络——1800 条销售报表
    # 里绝大多数是这种情况，省下来的是几十秒。
    if data.refresh:
        need_fetch = [item.plid for item in targets if item.plid]
    else:
        # 判定只用「有主图 + 有价格 + 有费率类目」三项。不要求
        # takealot_category_path：Product 主档本来就不存前台类目路径（它存的是
        # 已经确认过的 fee_category），拿它当必要条件会让自家商品永远重抓一遍。
        need_fetch = [
            item.plid for item in targets
            if item.plid and not (
                item.product_image_url
                and item.actual_sale_price_zar is not None
                and item.fee_category
            )
        ]
    # 门户已经把这些行补齐了，不必再打一次公开接口。
    #
    # 但价格口径要单独看：门户只给一个目录 bestPrice（往往就是前台展示的那条，
    # 也就是最贵的），「现货卖家最低价」只有公开接口的 buybox 拆得出来。所以
    # 重抓（refresh）时，价格还没落到新口径的行不能因为「图、价、类目都齐了」
    # 就被剔掉——不然点一百次重抓，售价还是那条贵的。
    if portal_records:
        still_missing = {
            item.plid for item in targets
            if item.plid and (
                not (
                    item.product_image_url
                    and item.actual_sale_price_zar is not None
                    and item.fee_category
                )
                or (
                    data.refresh
                    and item.price_source not in PRECISE_PRICE_SOURCES
                )
            )
        }
        need_fetch = [plid for plid in need_fetch if plid in still_missing]
    # 同一 PLID 可来自多个导入批次。先按首次出现顺序去重，再按请求预算切分，
    # 否则已抓到的 PLID 也可能出现在推迟集合里，导致对应行全部跳过。
    need_fetch = list(dict.fromkeys(need_fetch))
    records: Dict[str, Dict[str, Any]] = {}
    deferred_plids: set = set(portal_deferred)
    all_needed = set(need_fetch)
    if need_fetch:
        # 被限流时 PACER 会把间隔翻倍，400 个能拖成十几分钟——这个请求会在
        # commit 之前被代理掐掉，抓到的全丢。所以按当前节奏算这一轮抓得完多少，
        # 剩下的留给前端下一轮（`remaining` 会让它接着调）。
        afford = pub.fetch_budget(ENRICH_NETWORK_BUDGET_SECONDS)
        if len(need_fetch) > afford:
            deferred_plids.update(need_fetch[afford:])
            need_fetch = need_fetch[:afford]
        # workers 是并发**上限**不是目标：请求之间由 PACER 强制排队。
        # 写死 8 会把 fetch_many 的新默认值（4）抵消掉，机房 IP 上就是整片 429。
        for record in pub.fetch_many(need_fetch, workers=4):
            if record.get("plid") is not None:
                records[str(record["plid"])] = record
                if record.get("budget_deferred"):
                    deferred_plids.add(str(record["plid"]))
    # 按条数算，不按 plid 去重后的个数算：同一 PLID 可能对应多条选品记录，
    # 用集合长度会让这两个计数偏小，前端看到的「省了多少 / 处理了多少」就对不上。
    skipped_network = sum(1 for item in targets if item.plid and item.plid not in all_needed)


    exchange_rate = _exchange_rate(db)
    fee_rates: Dict[str, Optional[float]] = {}
    ok = failed = pending = handled = 0
    failures: List[Dict[str, str]] = []
    for item in targets:
        # 被推迟的这几条这一轮压根没打网络。如果照常走一遍，会被记成「抓取失败」，
        # 前端看到一片红；而且 enrich_status 一旦变成 failed 就不在 remaining 里，
        # 下一轮再也不会自动重试。所以原样留着，等下一轮。
        if item.plid in deferred_plids:
            continue
        handled += 1
        _apply_public(item, records.get(item.plid or ""), db)
        if item.fee_category and item.fee_category not in fee_rates:
            fee_rates[item.fee_category] = _fee_rate(db, item.fee_category)
        _calc(item, exchange_rate, fee_rates.get(item.fee_category or ""))
        _refresh_status(item)
        if item.enrich_status == "ok":
            ok += 1
        elif item.enrich_status in ("", None):
            # 留待下一轮（限流或被 stop 波及）。既不算成功也不算失败——算进
            # failed 会让「失败 N 个」虚高，用户会以为这些商品永远抓不到。
            pending += 1
        else:
            failed += 1
            if len(failures) < 20:
                failures.append({
                    "id": item.id,
                    "plid": item.plid or "",
                    "tsin": item.tsin or "",
                    "message": item.enrich_message or "未知原因",
                })
    db.commit()

    # 和本轮的目标查询用同一个条件，否则会出现「remaining 说还有、下一轮却选不中」
    # 或者反过来的空转。
    remaining = scoped_query.filter(pending_filter).count()
    if data.refresh:
        # 重抓时还要算上「价格没换到新口径」的行：它们资料是齐的（不在 pending 里），
        # 但售价还是旧的前台展示价。不把它们算进去，前端第一块跑完就以为收工了，
        # 剩下 1300 多行的售价永远不会更新。
        stale_price = scoped_query.filter(
            or_(
                SelectionItem.price_source.is_(None),
                SelectionItem.price_source.notin_(PRECISE_PRICE_SOURCES),
            )
        ).count()
        remaining = max(remaining, stale_price)

    # 这一轮真把多少条从「旧口径」换成了「现货最低价」。前端用它判断有没有进展：
    # 抓不到价格的行会让 remaining 一直不为 0，没有这个数就只能空转到轮次上限。
    price_fixed = sum(
        1 for item in targets
        if (item.price_source or "") in PRECISE_PRICE_SOURCES
        and price_source_before.get(item.id, "") not in PRECISE_PRICE_SOURCES
    )

    return {
        # 推迟到下一轮的不算「已处理」——否则前端的「已抓取 N 个」会虚高，
        # 而且 `!processed` 这个收尾判断也会失效。
        "processed": handled,
        "ok": ok,
        "failed": failed,
        # 限流 / 被 stop 波及：这一轮没结论，下一轮接着抓。前端据此区分
        # 「抓不到」和「还没轮到」，否则会把待重试的也报成失败。
        # 推迟到下一轮的那批也算「待处理」：它们既没成功也没失败，只是没轮到。
        "pending": pending + sum(1 for item in targets if item.plid in deferred_plids),
        "with_dimensions": dims_from_seller + dims_from_catalogue + dims_from_portal,
        "from_product_list": from_product_list,
        "price_fixed": price_fixed,
        "skipped_network": skipped_network,
        "remaining": remaining,
        "public_access_blocked": any(
            record.get("public_access_blocked") or record.get("http_status") in pub.ACCESS_STOP_STATUS
            for record in records.values()
        ),
        "seller_api": seller_note,
        # 门户配额（200 次/分钟）这一轮是否被卡住。前端据此决定「接着点」还是
        # 「等一会儿」——1804 个商品要 9~15 轮才跑得完，这是接口的硬限制。
        "portal_configured": portal_configured,
        "portal_limited": any(
            record.get("budget_deferred") or record.get("portal_rate_limited")
            for record in portal_records.values()
        ),
        # 配额什么时候回来。前端按这个值等一会儿再继续，而不是立刻重试空转。
        "portal_retry_after": portal_retry_after,
        # 这一轮顺手补了几个中文品名 / 判了几条普货带电（纯本地计算，不占配额）
        "names_filled": names_filled,
        "battery_judged": battery_judged,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# 3. 列表 / 修改
# ---------------------------------------------------------------------------

def query_items(
    db: Session,
    store_id: str,
    *,
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    tier: Optional[str] = None,
    only_selected: bool = False,
    hide_duplicates: bool = False,
    sort: str = "tier",
    limit: int = 60,
    offset: int = 0,
) -> Dict[str, Any]:
    """列表查询本体。

    路由层只负责把 Query 参数转进来——`Query(None)` 这个默认值对象本身是
    真值，直接写在路由函数里再被内部调用（测试、别的模块）时，`if batch_id:`
    会永远成立，静默加上一个 `batch_id IS NULL` 的过滤条件，返回空列表。
    把逻辑拆出来就不会踩这个坑。
    """
    query = db.query(SelectionItem).filter(SelectionItem.store_id == store_id)
    if batch_id:
        query = query.filter(SelectionItem.batch_id == batch_id)
    if status:
        query = query.filter(SelectionItem.status == status)
    if tier:
        query = query.filter(SelectionItem.tier == tier)
    if only_selected:
        query = query.filter(SelectionItem.selected == True)  # noqa: E712
    if hide_duplicates:
        query = query.filter(SelectionItem.already_in_products == False)  # noqa: E712
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.filter(or_(
            SelectionItem.product_name.ilike(like),
            SelectionItem.tsin.ilike(like),
            SelectionItem.plid.ilike(like),
            SelectionItem.chinese_product_name.ilike(like),
        ))

    total = query.count()

    # 默认按档位（S→A→B→C→空）再按净销售额降序，和原来那份分析表一致
    if sort == "profit":
        query = query.order_by(SelectionItem.profit_margin.desc().nullslast())
    elif sort == "price":
        query = query.order_by(SelectionItem.actual_sale_price_zar.desc().nullslast())
    elif sort == "sales":
        query = query.order_by(SelectionItem.sales_qty.desc().nullslast())
    else:
        # 档位必须按 S→A→B→C→空 排，不能直接按字符串排：字母序里 S 排在 C 后面，
        # 直接 `coalesce(tier,'Z').asc()` 会把最好的货压到最后。
        tier_rank = case(
            (SelectionItem.tier == "S", 0),
            (SelectionItem.tier == "A", 1),
            (SelectionItem.tier == "B", 2),
            (SelectionItem.tier == "C", 3),
            else_=4,
        )
        query = query.order_by(
            tier_rank.asc(),
            SelectionItem.net_sales_zar.desc().nullslast(),
            SelectionItem.review_count.desc().nullslast(),
        )
    items = query.offset(offset).limit(limit).all()
    return {"total": total, "items": [_to_dict(i) for i in items]}


@router.get("/items")
def list_items(
    batch_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    only_selected: bool = Query(False),
    hide_duplicates: bool = Query(False),
    sort: str = Query("tier"),
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    return query_items(
        db, store_id,
        batch_id=batch_id, status=status, keyword=keyword, tier=tier,
        only_selected=only_selected, hide_duplicates=hide_duplicates,
        sort=sort, limit=limit, offset=offset,
    )


@router.get("/batches")
def list_batches(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    rows = (
        db.query(
            SelectionItem.batch_id,
            SelectionItem.batch_name,
            func.count(SelectionItem.id),
            func.min(SelectionItem.created_at),
        )
        .filter(SelectionItem.store_id == store_id)
        .group_by(SelectionItem.batch_id, SelectionItem.batch_name)
        .order_by(func.min(SelectionItem.created_at).desc())
        .all()
    )
    return [
        {"batch_id": r[0], "batch_name": r[1], "count": r[2],
         "created_at": r[3].isoformat() if r[3] else ""}
        for r in rows
    ]


def summarize(db: Session, store_id: str, batch_id: Optional[str] = None) -> Dict[str, Any]:
    query = db.query(SelectionItem).filter(SelectionItem.store_id == store_id)
    if batch_id:
        query = query.filter(SelectionItem.batch_id == batch_id)
    items = query.all()
    return {
        "total": len(items),
        "pending": sum(1 for i in items if i.status == "待核算"),
        "qualified": sum(1 for i in items if i.status == "合格"),
        "rejected": sum(1 for i in items if i.status == "不合格"),
        "promoted": sum(1 for i in items if i.status == "已建产品"),
        "selected": sum(1 for i in items if i.selected),
        "duplicates": sum(1 for i in items if i.already_in_products),
        "with_image": sum(1 for i in items if i.product_image_url),
        "with_dimensions": sum(1 for i in items if i.length_mm and i.actual_weight_kg),
        "no_plid": sum(1 for i in items if not i.plid),
        # 已建产品但选品池又改过的条数——这些需要回推一次。
        "pending_sync": sum(1 for i in items if i.promoted_product_id and i.pool_dirty),
        # 售价口径分布：现货最低 / 最低报价 / 前台价 / 导入表 / 人工
        "price_in_stock": sum(1 for i in items if i.price_source == "in_stock"),
        "price_manual": sum(1 for i in items if i.price_source == "manual"),
        # 测算进度。界面上按这两块把卡片分成「未测算 / 已测算」两个可折叠分区，
        # 抓取时的「跳过已测算」也认这一刀，所以两边必须是同一个判据。
        "measured": sum(1 for i in items if i.profit_margin is not None),
        "unmeasured": sum(1 for i in items if i.profit_margin is None),
    }


@router.get("/summary")
def summary(
    batch_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    return summarize(db, store_id, batch_id)


class ItemPatch(BaseModel):
    product_name: Optional[str] = None
    chinese_product_name: Optional[str] = None
    fee_category: Optional[str] = None
    takealot_url: Optional[str] = None
    actual_sale_price_zar: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    length_mm: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    width_mm: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    height_mm: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    actual_weight_kg: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    purchase_url: Optional[str] = None
    purchase_cost_cny: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    purchase_shipping_cny: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    purchase_quantity: Optional[int] = Field(default=None, ge=1)
    unit_price_cny: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    packaging_cost_per_unit_cny: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    shipping_method: Optional[str] = None
    # 人工确认普货/带电：yes / no / maybe / ""（未判定）
    battery_status: Optional[str] = None
    selected: Optional[bool] = None
    note: Optional[str] = None


def _apply_patch(item: SelectionItem, patch: ItemPatch, db: Session, rate: float) -> None:
    fields = patch.model_dump(exclude_unset=True)
    dim_fields = {"length_mm", "width_mm", "height_mm", "actual_weight_kg"}
    if dim_fields & set(fields):
        # 人填过重尺就锁定来源，后续 enrich 不再用公开页的值覆盖
        item.dimensions_source = "manual"
    if "chinese_product_name" in fields:
        # 人改过品名就不再是「机器起的」，后续词表升级不许覆盖
        item.chinese_name_auto = False
    if "actual_sale_price_zar" in fields:
        # 人手改过的售价标成 manual，之后「重抓全部资料」不再覆盖它
        item.price_source = "manual"
    # 已建产品的行改了东西 → 标记「待更新到产品列表」，卡片上会出现回推按钮。
    # 勾选本身不算改动（那是挑要推哪几条）。
    if item.promoted_product_id and set(fields) - {"selected"}:
        item.pool_dirty = True
    for key, value in fields.items():
        setattr(item, key, value)
    fee_rate = _fee_rate(db, item.fee_category)
    _calc(item, rate, fee_rate)
    _refresh_status(item)


@router.patch("/items/{item_id}")
def patch_item(
    item_id: str,
    patch: ItemPatch,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    item = db.query(SelectionItem).filter(
        SelectionItem.id == item_id, SelectionItem.store_id == store_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="选品记录不存在")
    _apply_patch(item, patch, db, _exchange_rate(db))
    db.commit()
    db.refresh(item)
    return _to_dict(item)


class BulkPatch(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=2000)
    patch: ItemPatch


@router.post("/items/bulk")
def bulk_patch(
    data: BulkPatch,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """批量改同一个字段（主要是「统一物流方式」「统一采购数量」「全选」）。"""
    items = db.query(SelectionItem).filter(
        SelectionItem.id.in_(data.ids), SelectionItem.store_id == store_id
    ).all()
    rate = _exchange_rate(db)
    for item in items:
        _apply_patch(item, data.patch, db, rate)
    db.commit()
    return {"updated": len(items)}


class SelectQualifiedRequest(BaseModel):
    batch_id: Optional[str] = None
    min_margin: float = QUALIFY_MARGIN
    only_new: bool = True       # 跳过已在产品列表里的重复品


@router.get("/diag/catalogue")
def diag_catalogue(
    plid: str = Query(..., min_length=1, description="检查/接入目录连接用的数字 PLID"),
    discover: bool = Query(False, description="用户主动接入时传 true：一次性试端点×鉴权位置"),
    save: bool = Query(True, description="探测成功时记住这套连接，enrich 直接复用"),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """检查目录连接；**用户主动点按钮时**（discover=true）才允许发现并保存。

    默认只读：不读密钥、不试端点、不覆盖已保存的连接——后台流程不会自动
    调这条去扫上游。前端「探测竞品重尺接口」按钮传 discover=true，那是一次
    明确的用户授权；成功就存下来，之后 enrich 只复用、不再探测。

    返回里只有端点、鉴权方式、状态码和字段名，**不含密钥和响应正文**。
    """
    from services import takealot_catalogue_api as catalogue

    # `Query(False)` 在「直接调用函数」时是 Query 对象而不是 bool，而 Query
    # 对象是 truthy——不归一化的话，一次只读检查会被当成 discover=True，
    # 把用户已保存的连接覆盖掉。只有明确传了 True 才算用户授权。
    discover = discover is True

    # 默认路径刻意保持旧签名：只读检查不传第三个参数，行为和以前完全一致，
    # 也不会因为新参数把调用方的桩函数打穿（位置参数，不用 discover=）。
    report = catalogue.probe(db, plid, True) if discover else catalogue.probe(db, plid)
    report["saved"] = False
    if discover and save and report.get("ok") and report.get("winner"):
        catalogue.save_winner(db, report["winner"])
        report["saved"] = True
    return report


@router.get("/diag/portal")
def diag_portal(
    plid: str = Query(..., min_length=1, description="任取一个 PLID，用来测卖家门户目录接口"),
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """从**服务器**这一侧测一次卖家门户目录接口。

    这条通道是重尺的唯一来源，也是主图绕过 429 的通道：公开接口批量抓会被
    Cloudflare 限流，而卖家门户自己的目录接口不限流，一个请求同时给主图、
    图库、类目和长宽高。
    """
    from services import takealot_portal_api as portal

    return portal.probe(db, plid)


class PortalTokenIn(BaseModel):
    token: str = ""


@router.get("/portal/token")
def get_portal_token_state(
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """门户令牌状态。只返回是否配置和到期时间，**不返回令牌本身**。"""
    from services import takealot_portal_api as portal

    return portal.token_state(db)


@router.put("/portal/token")
def set_portal_token(
    data: PortalTokenIn,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """保存卖家门户令牌（浏览器 localStorage 里 `usr_st_auth` 的 `api_key`）。

    令牌有效期 24 小时，过期后重抓会退回公开接口——那时主图会重新开始吃
    429、重尺也补不上。所以这里把到期时间一并返回，前端可以提前提示。
    """
    from services import takealot_portal_api as portal

    value = (data.token or "").strip()
    if value and len(value.split(".")) != 3:
        raise HTTPException(400, "这不像一个卖家门户令牌（应为三段式的 JWT）")
    portal.save_token(db, value)
    return portal.token_state(db)


@router.get("/diag/public")
def diag_public(
    plid: str = Query(..., min_length=1, description="任取一个 PLID，用来测试公开接口"),
    store_id: str = Depends(get_store_id),
):
    """从**服务器**这一侧测一次公开接口，把真实结果原样返回。

    为什么需要它：抓不到主图时，界面上只有一句「公开接口返回 403」或
    「公开接口读取失败：URLError」，看不出是接口挂了、PLID 不对，还是这台
    服务器到 Takealot 的链路被挡。在你自己的电脑上测又永远成功（家庭 IP
    和机房 IP 在 Cloudflare 眼里不是一回事），所以只能让服务器自己回答。
    """
    from services import takealot_public_api as pub

    record = pub.fetch_product(plid)
    image = record.get("product_image_url") or ""
    return {
        "plid": plid,
        "success": bool(record.get("success")),
        "warnings": record.get("warnings") or [],
        "product_name": record.get("product_name"),
        "product_image_url": image,
        "image_ok": bool(image),
        "takealot_category_path": record.get("takealot_category_path"),
        "actual_sale_price_zar": record.get("actual_sale_price_zar"),
        "dimensions": record.get("dimensions") or {},
        # 图床是另一个域名（media.takealot.com），它的可用性和接口不是一回事：
        # 接口通了但图床被挡，照样会显示成「没抓到主图」。
        "hint": (
            "接口正常，主图和类目都拿到了"
            if record.get("success") and image
            else "接口通了但没返回图库" if record.get("success")
            else "接口这一层就失败了，看 warnings"
        ),
    }


@router.post("/items/select-qualified")
def select_qualified(
    data: SelectQualifiedRequest,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """把「利润率达标且还没建过产品」的整批勾上。

    前端逐页取 id 再批量勾会漏：翻页期间列表在变，而且 1800 条要翻 30 页。
    这里一次 UPDATE 解决，条件与列表筛选同源（都用 profit_margin）。
    """
    query = db.query(SelectionItem).filter(
        SelectionItem.store_id == store_id,
        SelectionItem.profit_margin.isnot(None),
        SelectionItem.profit_margin >= data.min_margin,
        SelectionItem.promoted_product_id.is_(None),
    )
    if data.batch_id:
        query = query.filter(SelectionItem.batch_id == data.batch_id)
    if data.only_new:
        query = query.filter(SelectionItem.already_in_products == False)  # noqa: E712
    selected = query.update({SelectionItem.selected: True}, synchronize_session=False)
    db.commit()
    return {"selected": selected, "min_margin": data.min_margin}


class IdList(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=500)


@router.post("/items/promote")
def promote(
    data: IdList,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """勾中的选品 → 复制进产品列表。

    只复制「核算需要的东西」，不复制销售数据（那是竞品/历史数据，不该进主档）。
    建完之后 item 指向新产品，再点就不会重复建。
    """
    from api.product_routes import _apply_calculated_fields
    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError
    from models import Store

    # 与卖家同步使用同一店铺写保留：先拿锁，再读取身份，直到commit/rollback。
    # Python锁不能保护另一worker；SQLite会拒绝并发写，不允许旧快照创建重复主档。
    try:
        locked = db.execute(update(Store).where(Store.id == store_id).values(id=Store.id))
    except OperationalError as error:
        db.rollback()
        if 'locked' in str(error).lower() or 'busy' in str(error).lower():
            raise HTTPException(status_code=409, detail='其他操作正在写入，请刷新后重试') from None
        raise
    if locked.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail='店铺已变化，请刷新后重试')

    items = db.query(SelectionItem).filter(
        SelectionItem.id.in_(data.ids), SelectionItem.store_id == store_id
    ).populate_existing().all()
    if not items:
        db.rollback()
        raise HTTPException(status_code=404, detail="没有找到可新建的选品记录")

    by_plid: Dict[int, set] = {}
    by_tsin: Dict[str, set] = {}
    identities: Dict[str, Any] = {}

    def index_product(product_id, plid, tsin, url):
        anchors = {value for value in (pub.parse_plid(plid), pub.parse_plid(url)) if value is not None}
        identity_tsin = (tsin or '').strip()
        identities[product_id] = (anchors, identity_tsin)
        for anchor in anchors:
            by_plid.setdefault(anchor, set()).add(product_id)
        if identity_tsin:
            by_tsin.setdefault(identity_tsin, set()).add(product_id)

    # 包含归档主档，避免重新创建仍被库存/装箱引用的历史产品；仅取身份列。
    for existing in db.query(Product.id, Product.plid, Product.tsin, Product.takealot_url).filter(
        Product.store_id == store_id
    ).all():
        index_product(*existing)

    created: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    for item in items:
        if item.promoted_product_id:
            skipped.append({"id": item.id, "reason": "已建过产品"})
            continue
        anchors = {value for value in (pub.parse_plid(item.plid), pub.parse_plid(item.takealot_url)) if value is not None}
        tsin = (item.tsin or '').strip()
        if len(anchors) > 1:
            skipped.append({'id': item.id, 'reason': 'PLID与链接不一致，请核对规格'})
            continue
        plid_hits = set().union(*(by_plid.get(anchor, set()) for anchor in anchors))
        tsin_hits = by_tsin.get(tsin, set()) if tsin else set()
        matches = plid_hits | tsin_hits
        if matches:
            conflict = len(matches) > 1 or any(
                len(identities[product_id][0]) > 1
                or (anchors and identities[product_id][0] and anchors != identities[product_id][0])
                or (tsin and identities[product_id][1] and tsin != identities[product_id][1])
                for product_id in matches
            )
            reason = ('商品身份信息不一致，请核对PLID、TSIN及规格' if conflict else
                      '产品列表里已有同一 PLID' if plid_hits else '产品列表里已有同一 TSIN')
            if not conflict:
                item.already_in_products = True
            skipped.append({'id': item.id, 'reason': reason})
            continue
        item.already_in_products = False  # 导入时标记仅供提示，不能替代当前主档核对。
        if not item.product_name and not item.plid:
            skipped.append({"id": item.id, "reason": "既没有商品名也没有 PLID，无法建产品"})
            continue

        product = Product(
            store_id=store_id,
            takealot_url=item.takealot_url or build_takealot_url(item.plid),
            plid=item.plid or "",
            tsin=item.tsin or "",
            product_name=item.product_name or "",
            chinese_product_name=item.chinese_product_name or "",
            product_image_url=item.product_image_url or "",
            actual_sale_price_zar=item.actual_sale_price_zar,
            fee_category=item.fee_category or None,
            # 类目是自动匹配的：用户在建产品时点过一次「新建」，视为认可；
            # 后面在详情页仍可改，改了会重算。
            fee_category_confirmed=bool(item.fee_category),
            purchase_url=item.purchase_url or "",
            purchase_cost_cny=item.purchase_cost_cny,
            purchase_shipping_cny=item.purchase_shipping_cny,
            purchase_quantity=item.purchase_quantity or 4,
            unit_price_cny=item.unit_price_cny,
            packaging_cost_per_unit_cny=(
                item.packaging_cost_per_unit_cny
                if item.packaging_cost_per_unit_cny is not None else 1.0
            ),
            length_mm=item.length_mm,
            width_mm=item.width_mm,
            height_mm=item.height_mm,
            actual_weight_kg=item.actual_weight_kg,
            shipping_method=item.shipping_method,
            rating_value=item.rating_value,
            review_count=item.review_count,
            note=item.note or "",
            # ORM defaults are populated only at INSERT; initialize every fee
            # used by the pre-flush calculation from the selection policy.
            inbound_listing_fee_cny=INBOUND_FEE_CNY,
            outbound_operation_fee_cny=OUTBOUND_FEE_CNY,
            last_mile_delivery_fee_cny=LAST_MILE_FEE_CNY,
            other_fee_cny=OTHER_FEE_CNY,
            fulfillment_fee_zar=(item.fulfillment_fee_zar
                if item.fulfillment_fee_zar is not None
                else default_fulfillment_fee_zar(item.fee_category)),
        )
        product.success_fee_rate = _fee_rate(db, item.fee_category)
        max_no = (
            db.query(Product.product_no)
            .filter(Product.store_id == store_id)
            .order_by(Product.product_no.desc())
            .first()
        )
        product.product_no = (max_no[0] + 1) if max_no and max_no[0] else 1
        _apply_calculated_fields(db, product)
        product.recorded_at = datetime.utcnow()
        db.add(product)
        db.flush()
        index_product(product.id, product.plid, product.tsin, product.takealot_url)

        item.promoted_product_id = product.id
        item.status = "已建产品"
        item.selected = False
        # 记下这次推送出去的字段值：以后再同步时，产品列表里被人工改过的字段
        # 不会被选品池覆盖。
        item.promoted_snapshot = json.dumps(
            {field: getattr(item, field, None) for field in PUSH_FIELDS},
            ensure_ascii=False, default=str,
        )
        item.pool_dirty = False
        item.promoted_synced_at = datetime.utcnow()
        created.append({"id": item.id, "product_id": product.id,
                        "product_no": str(product.product_no),
                        "product_name": product.product_name})

    db.commit()
    return {"created": len(created), "skipped": len(skipped),
            "products": created, "skip_details": skipped}


# ---------------------------------------------------------------------------
# 4b. 已建产品的回改推送
# ---------------------------------------------------------------------------

# 选品池 → 产品主档 的字段映射（左边池子、右边主档）。
# 只放「池子是来源」的字段；库存、打包、采购这些主档自己的账不归池子管。
PUSH_FIELDS = (
    "product_name",
    "chinese_product_name",
    "product_image_url",
    "takealot_url",
    "plid",
    "tsin",
    "actual_sale_price_zar",
    "fee_category",
    "purchase_url",
    "purchase_cost_cny",
    "purchase_shipping_cny",
    "purchase_quantity",
    "unit_price_cny",
    "packaging_cost_per_unit_cny",
    "length_mm",
    "width_mm",
    "height_mm",
    "actual_weight_kg",
    "shipping_method",
    "rating_value",
    "review_count",
    "note",
)


def _same_value(left: Any, right: Any) -> bool:
    """两个字段值是否「还是同一个」。浮点按四舍五入比，避免 0.1+0.2 那种差异。"""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 0.005
    if left is None and right is None:
        return True
    return str(left or "") == str(right or "")


@router.post("/items/sync")
def sync_to_products(
    data: IdList,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """把选品池里的修改同步回已建的产品主档。

    已建产品不该被锁死——选品池是核算台，产品主档是结果，中间改了很多次
    （换了货源、重量改过、售价定了）都是常事。所以这里做的是**回推**：
    把池子里的当前值写回主档并重算利润。

    冲突怎么办
    ----------
    两边都改过同一个字段时听产品列表的：`promoted_snapshot` 记着上次推送时
    的值，主档里的值和它不一样，说明用户在主档里人工改过——这时不覆盖，
    也不报错，在返回结果里列出来让人自己决定。
    """
    from api.product_routes import _apply_calculated_fields

    items = db.query(SelectionItem).filter(
        SelectionItem.id.in_(data.ids), SelectionItem.store_id == store_id
    ).all()
    if not items:
        raise HTTPException(status_code=404, detail="没有找到选品记录")

    updated: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for item in items:
        if not item.promoted_product_id:
            skipped.append({"id": item.id, "reason": "还没建产品，用「新建产品」推一次"})
            continue
        product = db.query(Product).filter(
            Product.id == item.promoted_product_id, Product.store_id == store_id
        ).first()
        if not product:
            # 主档里的产品被删了：解除关联，让它能重新建一次。
            item.promoted_product_id = None
            item.status = "待核算"
            item.pool_dirty = False
            skipped.append({"id": item.id, "reason": "产品列表里没有对应产品，已解除关联"})
            continue

        snapshot = {}
        try:
            snapshot = json.loads(item.promoted_snapshot or "{}")
        except (TypeError, ValueError):
            snapshot = {}

        kept: List[str] = []
        for field in PUSH_FIELDS:
            new_value = getattr(item, field, None)
            current = getattr(product, field, None)
            if field in snapshot and not _same_value(current, snapshot.get(field)):
                # 主档里人工改过，选品池不覆盖它。
                kept.append(field)
                continue
            setattr(product, field, new_value)

        product.success_fee_rate = _fee_rate(db, product.fee_category)
        _apply_calculated_fields(db, product)

        item.promoted_snapshot = json.dumps(
            {field: getattr(item, field, None) for field in PUSH_FIELDS},
            ensure_ascii=False, default=str,
        )
        item.pool_dirty = False
        item.promoted_synced_at = datetime.utcnow()
        updated.append({"id": item.id, "product_id": product.id,
                        "product_no": str(product.product_no),
                        "product_name": product.product_name,
                        "kept_fields": kept})

    db.commit()
    return {"updated": len(updated), "skipped": len(skipped),
            "products": updated, "skip_details": skipped}


@router.delete("/items")
def delete_items(
    data: IdList,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    deleted = (
        db.query(SelectionItem)
        .filter(SelectionItem.id.in_(data.ids), SelectionItem.store_id == store_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}


class PurgeRequest(BaseModel):
    branded: bool = True
    in_products: bool = True
    duplicates: bool = True
    dry_run: bool = False


@router.post("/items/purge")
def purge_items(
    data: PurgeRequest,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """清掉「有品牌 / 已在产品主档 / 池内重复」的选品记录。

    这三类都是「留着没用、还会干扰判断」的行：
      - 有品牌：品牌授权/侵权风险，不做
      - 已在产品主档：已经上架过了，不用再选一次
      - 池内重复：同一个 PLID 有多条（重复导入造成），只留信息最全的一条

    已经建过产品的记录（`promoted_product_id` 有值）一律不动——那是产品列表
    的溯源记录，删了产品详情里就看不到「这条是从哪个批次来的」。

    `dry_run=True` 只算不删，返回每一类会删几条 + 前 10 条标题，先在界面上
    给人看一眼再动手。
    """
    base = db.query(SelectionItem).filter(
        SelectionItem.store_id == store_id,
        SelectionItem.promoted_product_id.is_(None),
    )
    rows = base.all()

    product_plids = {p for (p,) in db.query(Product.plid).filter(
        Product.store_id == store_id, Product.plid.isnot(None), Product.plid != ""
    ).all()}
    product_tsins = {t for (t,) in db.query(Product.tsin).filter(
        Product.store_id == store_id, Product.tsin.isnot(None), Product.tsin != ""
    ).all()}

    doomed: Dict[str, SelectionItem] = {}   # id -> item，去重（一行可能命中多条规则）
    reasons: Dict[str, List[SelectionItem]] = {"branded": [], "in_products": [], "duplicates": []}

    def mark(reason: str, item: SelectionItem) -> None:
        if item.id in doomed:
            return
        doomed[item.id] = item
        reasons[reason].append(item)

    if data.branded:
        for item in rows:
            if (item.brand or "").strip():
                mark("branded", item)

    if data.in_products:
        for item in rows:
            plid = (item.plid or "").strip()
            tsin = (item.tsin or "").strip()
            if (plid and plid in product_plids) or (tsin and tsin in product_tsins):
                mark("in_products", item)

    if data.duplicates:
        # 同 PLID 留一条：优先信息最全的（有图 + 有重尺 + 有货源链接），
        # 再按创建时间新的在前。其余进待删。
        by_plid: Dict[str, List[SelectionItem]] = {}
        for item in rows:
            plid = (item.plid or "").strip()
            if plid:
                by_plid.setdefault(plid, []).append(item)
        for plid, group in by_plid.items():
            if len(group) < 2:
                continue

            def keep_score(item: SelectionItem) -> Tuple[int, float]:
                score = 0
                score += 2 if item.product_image_url else 0
                score += 2 if item.dimensions_source else 0
                score += 1 if item.purchase_url else 0
                score += 1 if item.unit_price_cny is not None else 0
                return score, item.created_at.timestamp() if item.created_at else 0.0

            group.sort(key=keep_score, reverse=True)
            for item in group[1:]:
                mark("duplicates", item)

    payload = {
        "dry_run": data.dry_run,
        "counts": {key: len(value) for key, value in reasons.items()},
        "total": len(doomed),
        "samples": {
            key: [
                {
                    "id": item.id,
                    "brand": item.brand,
                    "product_name": item.product_name,
                    "chinese_product_name": item.chinese_product_name,
                }
                for item in value[:10]
            ]
            for key, value in reasons.items()
        },
    }

    if data.dry_run or not doomed:
        return payload

    deleted = (
        db.query(SelectionItem)
        .filter(
            SelectionItem.id.in_(list(doomed)),
            SelectionItem.store_id == store_id,
            SelectionItem.promoted_product_id.is_(None),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    payload["deleted"] = deleted
    return payload


@router.delete("/batches/{batch_id}")
def delete_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    store_id: str = Depends(get_store_id),
):
    """整批删掉。已建产品的记录保留，否则产品列表里那条就追溯不到来源了。"""
    deleted = (
        db.query(SelectionItem)
        .filter(
            SelectionItem.batch_id == batch_id,
            SelectionItem.store_id == store_id,
            SelectionItem.promoted_product_id.is_(None),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}
