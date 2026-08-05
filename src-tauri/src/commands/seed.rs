use rusqlite::Connection;

const SEED_JSON: &str = include_str!("../../seed_data.json");

pub fn run_seed(conn: &Connection) {
    // Only seed if products table is empty
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM products", [], |r| r.get(0))
        .unwrap_or(0);
    if count > 0 {
        return;
    }

    let data: serde_json::Value =
        serde_json::from_str(SEED_JSON).expect("Failed to parse seed data");

    if let Some(products) = data["products"].as_array() {
        for p in products {
            let sql = "INSERT OR IGNORE INTO products (
                id, product_no, recorded_at, note, takealot_url, tsin,
                product_name, product_image_url, product_image_path,
                actual_sale_price_zar,
                fee_category, fee_category_confirmed, fee_rate_range,
                success_fee_rate, success_fee_cap,
                purchase_url, sku, chinese_product_name,
                purchase_cost_cny, purchase_shipping_cny, purchase_quantity,
                length_mm, width_mm, height_mm, actual_weight_kg,
                packaging_cost_per_unit_cny, shipping_method,
                inbound_listing_fee_cny, outbound_operation_fee_cny,
                last_mile_delivery_fee_cny, other_fee_cny,
                fulfillment_fee_zar,
                manual_domestic_forwarding_cny, manual_international_shipping_cny,
                manual_overseas_op_cost_cny, manual_success_fee_zar,
                manual_fulfillment_fee_zar, manual_total_cost_zar,
                volume_cbm, volumetric_weight_kg, chargeable_weight_kg,
                domestic_forwarding_cost_per_unit_cny, unit_product_cost_cny,
                international_shipping_per_unit_cny,
                cost_to_sa_warehouse_cny, cost_to_sa_warehouse_zar,
                overseas_warehouse_operation_cost_cny, overseas_warehouse_operation_cost_zar,
                success_fee_zar, official_total_cost_zar,
                total_cost_zar, profit_zar, profit_margin,
                minimum_price_at_20_margin, minimum_price_at_15_margin,
                link_status, selection_status,
                exchange_rate_used, fee_rate_used,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)";
            let _ = conn.execute(
                sql,
                rusqlite::params![
                    p["id"].as_str().unwrap_or(""),
                    p["product_no"].as_i64(),
                    p["recorded_at"].as_str().unwrap_or(""),
                    p["note"].as_str().unwrap_or(""),
                    p["takealot_url"].as_str().unwrap_or(""),
                    p["tsin"].as_str().unwrap_or(""),
                    p["product_name"].as_str().unwrap_or(""),
                    p["product_image_url"].as_str().unwrap_or(""),
                    p["product_image_path"].as_str().unwrap_or(""),
                    p["actual_sale_price_zar"].as_f64(),
                    p["fee_category"].as_str().unwrap_or(""),
                    p["fee_category_confirmed"].as_i64().unwrap_or(0),
                    p["fee_rate_range"].as_str().unwrap_or(""),
                    p["success_fee_rate"].as_f64(),
                    p["success_fee_cap"].as_f64(),
                    p["purchase_url"].as_str().unwrap_or(""),
                    p["sku"].as_str().unwrap_or(""),
                    p["chinese_product_name"].as_str().unwrap_or(""),
                    p["purchase_cost_cny"].as_f64(),
                    p["purchase_shipping_cny"].as_f64(),
                    p["purchase_quantity"].as_i64().unwrap_or(4),
                    p["length_mm"].as_f64(),
                    p["width_mm"].as_f64(),
                    p["height_mm"].as_f64(),
                    p["actual_weight_kg"].as_f64(),
                    p["packaging_cost_per_unit_cny"].as_f64().unwrap_or(1.0),
                    p["shipping_method"].as_str().unwrap_or(""),
                    p["inbound_listing_fee_cny"].as_f64().unwrap_or(0.75),
                    p["outbound_operation_fee_cny"].as_f64().unwrap_or(0.70),
                    p["last_mile_delivery_fee_cny"].as_f64().unwrap_or(2.00),
                    p["other_fee_cny"].as_f64().unwrap_or(2.00),
                    p["fulfillment_fee_zar"].as_f64().unwrap_or(42.00),
                    p["manual_domestic_forwarding_cny"].as_f64(),
                    p["manual_international_shipping_cny"].as_f64(),
                    p["manual_overseas_op_cost_cny"].as_f64(),
                    p["manual_success_fee_zar"].as_f64(),
                    p["manual_fulfillment_fee_zar"].as_f64(),
                    p["manual_total_cost_zar"].as_f64(),
                    p["volume_cbm"].as_f64(),
                    p["volumetric_weight_kg"].as_f64(),
                    p["chargeable_weight_kg"].as_f64(),
                    p["domestic_forwarding_cost_per_unit_cny"].as_f64(),
                    p["unit_product_cost_cny"].as_f64(),
                    p["international_shipping_per_unit_cny"].as_f64(),
                    p["cost_to_sa_warehouse_cny"].as_f64(),
                    p["cost_to_sa_warehouse_zar"].as_f64(),
                    p["overseas_warehouse_operation_cost_cny"].as_f64(),
                    p["overseas_warehouse_operation_cost_zar"].as_f64(),
                    p["success_fee_zar"].as_f64(),
                    p["official_total_cost_zar"].as_f64(),
                    p["total_cost_zar"].as_f64(),
                    p["profit_zar"].as_f64(),
                    p["profit_margin"].as_f64(),
                    p["minimum_price_at_20_margin"].as_f64(),
                    p["minimum_price_at_15_margin"].as_f64(),
                    p["link_status"].as_str().unwrap_or("未购买"),
                    p["selection_status"].as_str().unwrap_or("数据待补充"),
                    p["exchange_rate_used"].as_f64().unwrap_or(0.41),
                    p["fee_rate_used"].as_f64(),
                    p["created_at"].as_str().unwrap_or(""),
                    p["updated_at"].as_str().unwrap_or(""),
                ],
            );
        }
    }

    // Seed fee categories
    if let Some(cats) = data["fee_categories"].as_array() {
        for c in cats {
            let _ = conn.execute(
                "INSERT OR IGNORE INTO fee_categories (id, name, fee_rate_range, success_fee_rate, active, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                rusqlite::params![
                    c["id"].as_str().unwrap_or(""),
                    c["name"].as_str().unwrap_or(""),
                    c["fee_rate_range"].as_str().unwrap_or(""),
                    c["success_fee_rate"].as_f64().unwrap_or(0.0),
                    c["active"].as_i64().unwrap_or(1),
                    c["created_at"].as_str().unwrap_or(""),
                    c["updated_at"].as_str().unwrap_or(""),
                ],
            );
        }
    }

    // Seed fee mapping rules
    if let Some(rules) = data["fee_mapping_rules"].as_array() {
        for r in rules {
            let _ = conn.execute(
                "INSERT OR IGNORE INTO fee_mapping_rules (id, takealot_category_pattern, title_keyword_pattern, fee_category, priority, active, created_by_user, created_at) VALUES (?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    r["id"].as_str().unwrap_or(""),
                    r["takealot_category_pattern"].as_str().unwrap_or(""),
                    r["title_keyword_pattern"].as_str().unwrap_or(""),
                    r["fee_category"].as_str().unwrap_or(""),
                    r["priority"].as_i64().unwrap_or(0),
                    r["active"].as_i64().unwrap_or(1),
                    r["created_by_user"].as_i64().unwrap_or(0),
                    r["created_at"].as_str().unwrap_or(""),
                ],
            );
        }
    }

    // Insert default system settings
    let _ = conn.execute(
        "INSERT OR IGNORE INTO system_settings (id, key, value, updated_at) VALUES (?,?,?,?)",
        rusqlite::params!["default", "cny_per_zar", "0.41", ""],
    );
}
