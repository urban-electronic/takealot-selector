"""
Takealot 选品利润计算引擎
所有公式集中在此外,前端不自行实现任何财务计算。
"""

from typing import Optional, Dict, Any
from math import floor


def calculate_all(product: Dict[str, Any], exchange_rate: float = 0.41) -> Dict[str, Any]:
    """
    输入产品字典,返回所有计算结果。
    product 字典至少包含:
    length_mm, width_mm, height_mm, actual_weight_kg,
    actual_sale_price_zar, purchase_cost_cny, purchase_shipping_cny,
    purchase_quantity, packaging_cost_per_unit_cny, shipping_method,
    success_fee_rate, inbound_listing_fee_cny, outbound_operation_fee_cny,
    last_mile_delivery_fee_cny, other_fee_cny, fulfillment_fee_zar
    """
    result = {}

    length = product.get("length_mm")
    width = product.get("width_mm")
    height = product.get("height_mm")
    actual_weight = product.get("actual_weight_kg")
    sale_price = product.get("actual_sale_price_zar")
    purchase_cost = product.get("purchase_cost_cny")
    purchase_shipping = product.get("purchase_shipping_cny")
    purchase_qty = product.get("purchase_quantity", 4)
    packaging_cost = product.get("packaging_cost_per_unit_cny")
    shipping_method = product.get("shipping_method")
    fee_rate = product.get("success_fee_rate")
    inbound_fee = product.get("inbound_listing_fee_cny", 0.75)
    outbound_fee = product.get("outbound_operation_fee_cny", 0.70)
    last_mile = product.get("last_mile_delivery_fee_cny", 2.00)
    other_fee = product.get("other_fee_cny", 2.00)
    fulfillment_fee = product.get("fulfillment_fee_zar", 42.00)

    # 添加 manual_fulfillment_fee_zar 覆盖
    manual_fulfillment = product.get("manual_fulfillment_fee_zar")
    if manual_fulfillment is not None:
        fulfillment_fee = manual_fulfillment

    # 7.2 单个体积（方）
    if length is not None and width is not None and height is not None:
        volume_cbm = (length * width * height) / 1_000_000_000
        result["volume_cbm"] = round(volume_cbm, 6)
    else:
        result["volume_cbm"] = None

    # 7.3 体积重 & 实际计费重量
    # 体积重(kg) = 长(cm) × 宽(cm) × 高(cm) ÷ 6000
    # 因为输入为mm，等价于: (mm³) / 6_000_000 = cbm * (1000/6)
    if result["volume_cbm"] is not None:
        result["volumetric_weight_kg"] = round(result["volume_cbm"] * 1000 / 6, 3)
    else:
        result["volumetric_weight_kg"] = None

    if result["volumetric_weight_kg"] is not None and actual_weight is not None:
        result["chargeable_weight_kg"] = round(
            max(actual_weight, result["volumetric_weight_kg"]), 3
        )
    elif actual_weight is not None:
        result["chargeable_weight_kg"] = actual_weight
    elif result["volumetric_weight_kg"] is not None:
        result["chargeable_weight_kg"] = result["volumetric_weight_kg"]
    else:
        result["chargeable_weight_kg"] = None

    # 7.5 国内前置仓单个费用
    # 公式: IF(5 × 计费重量 > 1, 5 × 计费重量, 1)
    if result["chargeable_weight_kg"] is not None:
        raw_cost = 5 * result["chargeable_weight_kg"]
        result["domestic_forwarding_cost_per_unit_cny"] = round(
            raw_cost if raw_cost > 1 else 1, 2
        )
    else:
        result["domestic_forwarding_cost_per_unit_cny"] = None

    # 手动覆盖前置仓快递费
    manual_domestic = product.get("manual_domestic_forwarding_cny")
    if manual_domestic is not None:
        result["domestic_forwarding_cost_per_unit_cny"] = manual_domestic

    # 7.6 综合单个成本
    if (
        purchase_cost is not None
        and purchase_shipping is not None
        and purchase_qty
        and result["domestic_forwarding_cost_per_unit_cny"] is not None
    ):
        packaging_cost_val = packaging_cost if packaging_cost is not None else 0
        result["unit_product_cost_cny"] = round(
            (purchase_cost + purchase_shipping) / purchase_qty
            + packaging_cost_val
            + result["domestic_forwarding_cost_per_unit_cny"],
            2,
        )
    else:
        result["unit_product_cost_cny"] = None

    # 7.7 国际头程运费
    if shipping_method is not None and result["chargeable_weight_kg"] is not None:
        if shipping_method == "空运普货":
            result["international_shipping_per_unit_cny"] = round(
                79 * result["chargeable_weight_kg"], 2
            )
        elif shipping_method == "空运带电":
            result["international_shipping_per_unit_cny"] = round(
                89 * result["chargeable_weight_kg"], 2
            )
        elif shipping_method == "海运普货":
            if result["volume_cbm"] is not None:
                result["international_shipping_per_unit_cny"] = round(
                    result["volume_cbm"] * 1500, 2
                )
            else:
                result["international_shipping_per_unit_cny"] = None
        elif shipping_method == "海运带电":
            if result["volume_cbm"] is not None:
                result["international_shipping_per_unit_cny"] = round(
                    result["volume_cbm"] * 2100, 2
                )
            else:
                result["international_shipping_per_unit_cny"] = None
        else:
            result["international_shipping_per_unit_cny"] = None
    else:
        result["international_shipping_per_unit_cny"] = None

    # 手动覆盖国际头程
    manual_intl = product.get("manual_international_shipping_cny")
    if manual_intl is not None:
        result["international_shipping_per_unit_cny"] = manual_intl

    # 7.8 到南非仓总成本 (CNY & ZAR)
    if (
        result["unit_product_cost_cny"] is not None
        and result["international_shipping_per_unit_cny"] is not None
    ):
        result["cost_to_sa_warehouse_cny"] = round(
            result["unit_product_cost_cny"]
            + result["international_shipping_per_unit_cny"],
            2,
        )
        if exchange_rate > 0:
            result["cost_to_sa_warehouse_zar"] = round(
                result["cost_to_sa_warehouse_cny"] / exchange_rate, 2
            )
        else:
            result["cost_to_sa_warehouse_zar"] = None
    else:
        result["cost_to_sa_warehouse_cny"] = None
        result["cost_to_sa_warehouse_zar"] = None

    # 7.9 海外仓操作成本
    result["overseas_warehouse_operation_cost_cny"] = round(
        (inbound_fee or 0)
        + (outbound_fee or 0)
        + (last_mile or 0)
        + (other_fee or 0),
        2,
    )
    if exchange_rate > 0:
        result["overseas_warehouse_operation_cost_zar"] = round(
            result["overseas_warehouse_operation_cost_cny"] / exchange_rate, 2
        )
    else:
        result["overseas_warehouse_operation_cost_zar"] = None

    # 手动覆盖海外仓操作费
    manual_overseas = product.get("manual_overseas_op_cost_cny")
    if manual_overseas is not None:
        result["overseas_warehouse_operation_cost_cny"] = manual_overseas
        if exchange_rate > 0:
            result["overseas_warehouse_operation_cost_zar"] = round(
                manual_overseas / exchange_rate, 2
            )
        else:
            result["overseas_warehouse_operation_cost_zar"] = None

    # 7.10 Takealot 平台费用
    if fee_rate is not None and sale_price is not None:
        result["success_fee_zar"] = round(fee_rate * sale_price, 2)
        result["official_total_cost_zar"] = round(
            result["success_fee_zar"] + (fulfillment_fee or 0), 2
        )
    else:
        result["success_fee_zar"] = None
        result["official_total_cost_zar"] = None

    # 手动覆盖 Success Fee，并重算 official_total_cost_zar
    manual_success = product.get("manual_success_fee_zar")
    if manual_success is not None:
        result["success_fee_zar"] = manual_success
        result["official_total_cost_zar"] = round(
            manual_success + (fulfillment_fee or 0), 2
        )

    # 7.11 总成本、利润及利润率
    if (
        result["cost_to_sa_warehouse_zar"] is not None
        and result["overseas_warehouse_operation_cost_zar"] is not None
        and result["official_total_cost_zar"] is not None
        and sale_price is not None
        and sale_price > 0
    ):
        result["total_cost_zar"] = round(
            result["cost_to_sa_warehouse_zar"]
            + result["overseas_warehouse_operation_cost_zar"]
            + result["official_total_cost_zar"],
            2,
        )
        result["profit_zar"] = round(sale_price - result["total_cost_zar"], 2)
        result["profit_margin"] = round(
            1 - result["total_cost_zar"] / sale_price, 4
        )
    else:
        result["total_cost_zar"] = None
        result["profit_zar"] = None
        result["profit_margin"] = None

    # 手动覆盖总成本
    manual_total = product.get("manual_total_cost_zar")
    if manual_total is not None and sale_price is not None and sale_price > 0:
        result["total_cost_zar"] = manual_total
        result["profit_zar"] = round(sale_price - manual_total, 2)
        result["profit_margin"] = round(1 - manual_total / sale_price, 4)

    # 7.12 达到20%/15%利润率的最低售价
    result["minimum_price_at_20_margin"] = None
    result["minimum_price_at_15_margin"] = None
    if (
        result["profit_margin"] is not None
        and sale_price is not None
        and fee_rate is not None
    ):
        if result["profit_margin"] > 0.20:
            numerator = (1 - fee_rate) * sale_price - result["profit_zar"]
            denominator = (1 - fee_rate) - 0.20
            if denominator != 0:
                result["minimum_price_at_20_margin"] = round(
                    numerator / denominator, 0
                )
        if result["profit_margin"] > 0.15:
            numerator = (1 - fee_rate) * sale_price - result["profit_zar"]
            denominator = (1 - fee_rate) - 0.15
            if denominator != 0:
                result["minimum_price_at_15_margin"] = round(
                    numerator / denominator, 0
                )

    return result


def determine_selection_status(
    product: Dict[str, Any],
    calculated: Dict[str, Any],
) -> str:
    """判定选品状态"""
    from models import SelectionStatus

    # 检查关键字段是否完整
    required_fields = [
        "actual_sale_price_zar",
        "purchase_cost_cny",
        "purchase_shipping_cny",
        "purchase_quantity",
        "length_mm",
        "width_mm",
        "height_mm",
        "actual_weight_kg",
        "shipping_method",
    ]
    for f in required_fields:
        if product.get(f) is None:
            return SelectionStatus.DATA_INCOMPLETE.value

    # Fee品类必须已确认
    if not product.get("fee_category_confirmed") or not product.get("fee_category"):
        return SelectionStatus.CATEGORY_PENDING.value

    # 计算利润率
    profit_margin = calculated.get("profit_margin")
    if profit_margin is None:
        return SelectionStatus.DATA_INCOMPLETE.value

    if profit_margin >= 0.25:
        return SelectionStatus.QUALIFIED.value
    else:
        return SelectionStatus.NOT_RECOMMENDED.value
