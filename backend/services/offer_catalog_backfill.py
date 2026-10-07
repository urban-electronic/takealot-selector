"""用卖家后台 Offer Export 补齐现有产品的 SKU 和真实规格。

数据文件只作为匹配字典使用：本模块绝不创建产品，也不跨店铺复制产品。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from models import Product


CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "offer_catalog_2026_10_07.json"
_SKU_SPLIT = re.compile(r"[\s,，;；]+")
_PLID = re.compile(r"PLID(\d+)", re.IGNORECASE)

_TERMS = {
    "dark green": "深绿色", "light green": "浅绿色", "light blue": "浅蓝色",
    "dark blue": "深蓝色", "royal blue": "宝蓝色", "navy blue": "藏蓝色",
    "navy": "藏蓝色", "black": "黑色", "white": "白色", "red": "红色",
    "blue": "蓝色", "green": "绿色", "yellow": "黄色", "purple": "紫色",
    "pink": "粉色", "grey": "灰色", "gray": "灰色", "brown": "棕色",
    "orange": "橙色", "khaki": "卡其色", "beige": "米色", "coffee": "咖色",
}


def _plid(*values: str | None) -> str:
    for value in values:
        match = _PLID.search(str(value or ""))
        if match:
            return match.group(1)
    return ""


def _tokens(value: str | None) -> list[str]:
    return [part for part in _SKU_SPLIT.split((value or "").strip()) if part]


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.lower().startswith(prefix.lower()):
            prefix = prefix[:-1]
    # 不在单词中间截断，避免生成难辨认的半个型号。
    return re.sub(r"[^\s\-–—,/+&]*$", "", prefix).rstrip(" -–—,/+&")


def _translate_terms(value: str) -> str:
    result = value
    for english in sorted(_TERMS, key=len, reverse=True):
        result = re.sub(rf"(?<![a-z]){re.escape(english)}(?![a-z])", _TERMS[english], result, flags=re.I)
    return result


def _variant_labels(titles: list[str]) -> list[str]:
    """从同一产品页的标题差异中提取颜色、尺码、容量或型号。"""
    prefix = _common_prefix(titles)
    labels: list[str] = []
    for title in titles:
        tail = title[len(prefix):] if prefix and title.lower().startswith(prefix.lower()) else title
        tail = re.sub(r"^[\s\-–—,/+&]+", "", tail).strip()
        if not tail:
            tail = title
        tail = _translate_terms(tail)
        # 现有界面用空白分隔不同 SKU，因此规格内部以中点连接。
        label = re.sub(r"[\s\-–—,/]+", "·", tail).strip("·")
        labels.append(label[:80] or "规格待确认")
    return labels


def load_offer_catalog(path: Path = CATALOG_PATH) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {group["plid"]: group for group in payload.get("groups", [])}


def apply_offer_catalog_backfill(
    db,
    products: Iterable["Product"] | None = None,
    store_id: str | None = None,
) -> dict[str, int]:
    """幂等补齐现有产品；保留原主款顺序及表格中不存在的历史 SKU。"""
    catalog = load_offer_catalog()
    if products is None:
        from models import Product
        query = db.query(Product)
        rows = query.filter(Product.store_id == store_id).all() if store_id else []
    else:
        rows = list(products)
    stats = {"matched": 0, "sku_updated": 0, "variant_labels_updated": 0}

    for product in rows:
        group = catalog.get(_plid(product.takealot_url, product.tsin))
        if not group:
            continue
        variants = group.get("variants", [])
        official_skus = [str(item.get("sku") or "").strip() for item in variants]
        official_skus = list(dict.fromkeys(sku for sku in official_skus if sku))
        if not official_skus:
            continue
        stats["matched"] += 1

        old_skus = _tokens(product.sku)
        old_labels = [part for part in re.split(r"[\r\n]+", (product.chinese_product_name or "").strip()) if part]
        old_label_by_sku = dict(zip(old_skus, old_labels)) if len(old_labels) == len(old_skus) else {}
        # 已经选定的主款放在第一位，其余官方分支依表格顺序补齐。
        merged_skus = [sku for sku in old_skus if sku in official_skus]
        merged_skus += [sku for sku in official_skus if sku not in merged_skus]
        merged_skus += [sku for sku in old_skus if sku not in merged_skus]
        merged_value = "\n".join(merged_skus)
        if merged_value != (product.sku or "").strip():
            product.sku = merged_value
            stats["sku_updated"] += 1

        if len(merged_skus) <= 1:
            continue
        title_by_sku = {str(item.get("sku") or "").strip(): str(item.get("title") or "").strip() for item in variants}
        official_titles = [title_by_sku[sku] for sku in merged_skus if sku in title_by_sku]
        official_labels = dict(zip([sku for sku in merged_skus if sku in title_by_sku], _variant_labels(official_titles)))
        labels = [official_labels.get(sku) or old_label_by_sku.get(sku) or f"历史分支·{sku[-4:]}" for sku in merged_skus]
        labels_value = "\n".join(labels)
        if labels_value != (product.chinese_product_name or "").strip():
            product.chinese_product_name = labels_value
            stats["variant_labels_updated"] += 1

    if stats["sku_updated"] or stats["variant_labels_updated"]:
        db.commit()
    return stats
