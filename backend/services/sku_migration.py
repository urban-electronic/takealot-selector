"""以卖家后台为准更正 SKU 时，把下游各表里引用旧 SKU 的地方一起迁到新 SKU。

为什么不能只改 `Product.sku`：库存表主键是 `(store_id, sku)`，发运行、箱内明细
也都按 SKU 关联。只改主档的结果是旧 SKU 的库存挂在空气上、新 SKU 查不到货，
在途发运匹配不到，装箱资料（重量/材质/图片/模板行）也一起失联。

本模块只负责「下游表迁移 + 留档」，不负责判断该不该改——是否变更由
`takealot_seller_api.offer_plan()` 决定，是否提交由调用方的事务决定。
"""

from __future__ import annotations

import json
import re

from models import (
    DEFAULT_STORE_ID, InventoryAdjustment, PackingProduct, Shipment,
    ShipmentCarton, ShipmentCartonItem, ShipmentLine, SkuRename, SystemSettings,
)

_SKU_SPLIT = re.compile(r"[\s,，;；]+")

# 自动调价规则与 buybox 监控都把 sku 存在 JSON 里，键含 product_id 可直接定位。
_CONFIG_PREFIXES = ("_pricing_rule:", "_pricing_monitor:")


def sku_tokens(value: str | None) -> list[str]:
    """与产品页一致的多规格拆分口径：换行、空格、中英文逗号分号都算分隔。"""
    return [part for part in _SKU_SPLIT.split((value or "").strip()) if part]


def _like_prefix(value: str) -> str:
    """把前缀转义成 LIKE 字面量；`_pricing_rule:` 里的下划线不能当通配符。"""
    return value.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")


def migrate_sku(
    db,
    product,
    old_skus: list[str],
    new_skus: list[str],
    store_id: str = DEFAULT_STORE_ID,
    source: str = "seller_api",
) -> dict:
    """把 old_skus 在各表里的引用改成 new_skus，并写一条留档。

    两个列表按下标一一对应，长度不等直接拒绝——没有可靠的对应关系时，
    宁可不改，也不能把库存挂到错误的规格上。

    不 commit，由调用方统一提交；返回 {'moved': {表名: 行数}, 'conflicts': [...],
    'renamed': [...]}，冲突项表示该表里新 SKU 已存在，已跳过而不是覆盖。
    """
    if len(old_skus) != len(new_skus):
        raise ValueError("旧 SKU 与新 SKU 数量不一致，无法一一对应")

    pairs = [(o.strip(), n.strip()) for o, n in zip(old_skus, new_skus)
             if o and n and o.strip() and n.strip() and o.strip() != n.strip()]
    moved: dict[str, int] = {}
    conflicts: list[str] = []
    if not pairs:
        return {"moved": moved, "conflicts": conflicts, "renamed": []}

    for old, new in pairs:
        # 1) 装箱产品资料：复合主键 (store_id, sku)，新 SKU 已存在时跳过而不是覆盖。
        row = db.query(PackingProduct).filter_by(store_id=store_id, sku=old).first()
        if row:
            taken = db.query(PackingProduct).filter_by(store_id=store_id, sku=new).first()
            if taken:
                conflicts.append(f"装箱资料里已存在 {new}，未合并 {old}")
            else:
                row.sku = new
                moved["packing_products"] = moved.get("packing_products", 0) + 1

        # 2) 库存调整流水：只追加的历史，改 sku 让它跟着新编码继续可查。
        count = (db.query(InventoryAdjustment)
                 .filter(InventoryAdjustment.store_id == store_id, InventoryAdjustment.sku == old)
                 .update({"sku": new}, synchronize_session=False))
        if count:
            moved["inventory_adjustments"] = moved.get("inventory_adjustments", 0) + count

        # 3) 发运行：本表没有 store_id，必须经 Shipment 限定店铺。
        line_ids = [row_id for (row_id,) in db.query(ShipmentLine.id)
                    .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                    .filter(Shipment.store_id == store_id, ShipmentLine.sku == old).all()]
        if line_ids:
            count = (db.query(ShipmentLine)
                     .filter(ShipmentLine.id.in_(line_ids))
                     .update({"sku": new}, synchronize_session=False))
            moved["shipment_lines"] = moved.get("shipment_lines", 0) + count

        # 4) 箱内明细：同样经 carton → shipment 限定店铺。
        item_ids = [row_id for (row_id,) in db.query(ShipmentCartonItem.id)
                    .join(ShipmentCarton, ShipmentCarton.id == ShipmentCartonItem.carton_id)
                    .join(Shipment, Shipment.id == ShipmentCarton.shipment_id)
                    .filter(Shipment.store_id == store_id, ShipmentCartonItem.sku == old).all()]
        if item_ids:
            count = (db.query(ShipmentCartonItem)
                     .filter(ShipmentCartonItem.id.in_(item_ids))
                     .update({"sku": new}, synchronize_session=False))
            moved["shipment_carton_items"] = moved.get("shipment_carton_items", 0) + count

        # 5) 自动调价 / buybox 监控：绑定的 sku 变了会抛「请重新绑定」，一并改掉。
        for prefix in _CONFIG_PREFIXES:
            base = f"{prefix}{store_id}:{product.id}:"
            rows = (db.query(SystemSettings)
                    .filter(SystemSettings.key.like(_like_prefix(base) + "%", escape="\\")).all())
            for setting in rows:
                try:
                    value = json.loads(setting.value)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict) and value.get("sku") == old:
                    value["sku"] = new
                    setting.value = json.dumps(value, ensure_ascii=False)
                    moved["pricing_configs"] = moved.get("pricing_configs", 0) + 1

        db.add(SkuRename(store_id=store_id, product_id=product.id, old_sku=old,
                         new_sku=new, source=source,
                         detail=json.dumps(moved, ensure_ascii=False)))

    return {"moved": moved, "conflicts": conflicts, "renamed": pairs}
