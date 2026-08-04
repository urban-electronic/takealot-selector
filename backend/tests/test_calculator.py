"""
计算引擎单元测试
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.product_calculator import calculate_all, determine_selection_status


def test_volume_cbm():
    product = {
        "length_mm": 300, "width_mm": 200, "height_mm": 100,
        "actual_weight_kg": 2.0,
        "actual_sale_price_zar": 500,
        "purchase_cost_cny": 50, "purchase_shipping_cny": 10,
        "purchase_quantity": 4,
        "packaging_cost_per_unit_cny": 2.0,
        "shipping_method": "空运普货",
        "success_fee_rate": 0.15,
        "inbound_listing_fee_cny": 0.75,
        "outbound_operation_fee_cny": 0.70,
        "last_mile_delivery_fee_cny": 2.00,
        "other_fee_cny": 2.00,
        "fulfillment_fee_zar": 42.00,
        "fee_category": "Sport",
        "fee_category_confirmed": True,
    }
    result = calculate_all(product, exchange_rate=0.41)

    # 体积 = 300*200*100 / 1e9 = 0.006
    assert result["volume_cbm"] == 0.006, f"Volume: {result['volume_cbm']}"
    print("PASS: volume_cbm =", result["volume_cbm"])

    # 计费重量 = max(2.0, 0.006 * 1.67) = max(2.0, 0.01002) = 2.0
    assert result["chargeable_weight_kg"] == 2.0, f"Weight: {result['chargeable_weight_kg']}"
    print("PASS: chargeable_weight_kg =", result["chargeable_weight_kg"])

    # 国内前置仓 = max(5 * 2.0, 1) = 10
    assert result["domestic_forwarding_cost_per_unit_cny"] == 10.0, f"Domestic: {result['domestic_forwarding_cost_per_unit_cny']}"
    print("PASS: domestic_forwarding_cost =", result["domestic_forwarding_cost_per_unit_cny"])

    # 综合单个成本 = (50+10)/4 + 2 + 10 = 15 + 2 + 10 = 27
    assert result["unit_product_cost_cny"] == 27.0, f"Unit cost: {result['unit_product_cost_cny']}"
    print("PASS: unit_product_cost_cny =", result["unit_product_cost_cny"])

    # 国际头程: 空运普货 79 * 2 = 158
    assert result["international_shipping_per_unit_cny"] == 158.0, f"Intl Shipping: {result['international_shipping_per_unit_cny']}"
    print("PASS: international_shipping =", result["international_shipping_per_unit_cny"])

    # 到南非仓 CNY = 27 + 158 = 185
    assert result["cost_to_sa_warehouse_cny"] == 185.0, f"SA WH CNY: {result['cost_to_sa_warehouse_cny']}"
    print("PASS: cost_to_sa_warehouse_cny =", result["cost_to_sa_warehouse_cny"])

    # 到南非仓 ZAR = 185 / 0.41 ≈ 451.22
    print("cost_to_sa_warehouse_zar =", result["cost_to_sa_warehouse_zar"])

    # 海外仓操作 CNY = 0.75+0.70+2+2 = 5.45
    assert result["overseas_warehouse_operation_cost_cny"] == 5.45, f"Overseas: {result['overseas_warehouse_operation_cost_cny']}"
    print("PASS: overseas_warehouse_operation_cost_cny =", result["overseas_warehouse_operation_cost_cny"])

    # Success fee = 0.15 * 500 = 75
    assert result["success_fee_zar"] == 75.0, f"Success fee: {result['success_fee_zar']}"
    print("PASS: success_fee_zar =", result["success_fee_zar"])

    # Official total = 75 + 42 = 117
    assert result["official_total_cost_zar"] == 117.0, f"Official: {result['official_total_cost_zar']}"
    print("PASS: official_total_cost_zar =", result["official_total_cost_zar"])

    # 利润率
    print("profit_margin =", result["profit_margin"])
    print("profit_zar =", result["profit_zar"])
    print("total_cost_zar =", result["total_cost_zar"])

    # 选品状态判定
    status = determine_selection_status(product, result)
    print("Selection status:", status)

    print("\nAll tests passed!")


def test_sea_shipping():
    product = {
        "length_mm": 600, "width_mm": 500, "height_mm": 400,
        "actual_weight_kg": 10.0,
        "actual_sale_price_zar": 800,
        "purchase_cost_cny": 200, "purchase_shipping_cny": 50,
        "purchase_quantity": 2,
        "packaging_cost_per_unit_cny": 5.0,
        "shipping_method": "海运普货",
        "success_fee_rate": 0.12,
        "inbound_listing_fee_cny": 0.75,
        "outbound_operation_fee_cny": 0.70,
        "last_mile_delivery_fee_cny": 2.00,
        "other_fee_cny": 2.00,
        "fulfillment_fee_zar": 42.00,
        "fee_category": "Small Appliances",
        "fee_category_confirmed": True,
    }
    result = calculate_all(product, exchange_rate=0.41)

    volume = 600 * 500 * 400 / 1e9  # 0.12
    assert result["volume_cbm"] == 0.12, f"Volume: {result['volume_cbm']}"
    print("PASS: sea volume_cbm =", result["volume_cbm"])

    # 海运普货 = 0.12 * 1500 = 180
    assert result["international_shipping_per_unit_cny"] == 180.0, f"Sea shipping: {result['international_shipping_per_unit_cny']}"
    print("PASS: sea international_shipping =", result["international_shipping_per_unit_cny"])

    print("Sea profit_margin =", result["profit_margin"])
    print("\nSea shipping tests passed!")


if __name__ == "__main__":
    test_volume_cbm()
    print("\n---\n")
    test_sea_shipping()
