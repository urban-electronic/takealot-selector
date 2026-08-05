use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::State;
use std::collections::HashMap;
use crate::commands::db::DbState;
use crate::commands::calculator::{calculate_all, determine_selection_status, CalcInput};

// ---- Product types ----

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Product {
    pub id: String,
    pub product_no: Option<i32>,
    pub recorded_at: Option<String>,
    pub note: Option<String>,
    pub takealot_url: Option<String>,
    pub tsin: Option<String>,
    pub product_name: Option<String>,
    pub product_image_url: Option<String>,
    pub product_image_path: Option<String>,
    pub actual_sale_price_zar: Option<f64>,
    pub fee_category: Option<String>,
    pub fee_category_confirmed: Option<bool>,
    pub fee_rate_range: Option<String>,
    pub success_fee_rate: Option<f64>,
    pub success_fee_cap: Option<f64>,
    pub purchase_url: Option<String>,
    pub sku: Option<String>,
    pub chinese_product_name: Option<String>,
    pub purchase_cost_cny: Option<f64>,
    pub purchase_shipping_cny: Option<f64>,
    pub purchase_quantity: Option<i32>,
    pub length_mm: Option<f64>,
    pub width_mm: Option<f64>,
    pub height_mm: Option<f64>,
    pub actual_weight_kg: Option<f64>,
    pub packaging_cost_per_unit_cny: Option<f64>,
    pub unit_price_cny: Option<f64>,
    pub shipping_method: Option<String>,
    pub inbound_listing_fee_cny: Option<f64>,
    pub outbound_operation_fee_cny: Option<f64>,
    pub last_mile_delivery_fee_cny: Option<f64>,
    pub other_fee_cny: Option<f64>,
    pub fulfillment_fee_zar: Option<f64>,
    pub manual_domestic_forwarding_cny: Option<f64>,
    pub manual_international_shipping_cny: Option<f64>,
    pub manual_overseas_op_cost_cny: Option<f64>,
    pub manual_success_fee_zar: Option<f64>,
    pub manual_fulfillment_fee_zar: Option<f64>,
    pub manual_total_cost_zar: Option<f64>,
    pub volume_cbm: Option<f64>,
    pub volumetric_weight_kg: Option<f64>,
    pub chargeable_weight_kg: Option<f64>,
    pub domestic_forwarding_cost_per_unit_cny: Option<f64>,
    pub unit_product_cost_cny: Option<f64>,
    pub international_shipping_per_unit_cny: Option<f64>,
    pub cost_to_sa_warehouse_cny: Option<f64>,
    pub cost_to_sa_warehouse_zar: Option<f64>,
    pub overseas_warehouse_operation_cost_cny: Option<f64>,
    pub overseas_warehouse_operation_cost_zar: Option<f64>,
    pub success_fee_zar: Option<f64>,
    pub official_total_cost_zar: Option<f64>,
    pub total_cost_zar: Option<f64>,
    pub profit_zar: Option<f64>,
    pub profit_margin: Option<f64>,
    pub minimum_price_at_20_margin: Option<f64>,
    pub minimum_price_at_15_margin: Option<f64>,
    pub link_status: Option<String>,
    pub selection_status: Option<String>,
    pub exchange_rate_used: Option<f64>,
    pub fee_rate_used: Option<f64>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
}

impl Product {
    pub(crate) fn from_row(row: &rusqlite::Row) -> rusqlite::Result<Self> {
        Ok(Product {
            id: row.get(0)?, product_no: row.get(1)?, recorded_at: row.get(2)?,
            note: row.get(3)?, takealot_url: row.get(4)?, tsin: row.get(5)?,
            product_name: row.get(6)?, product_image_url: row.get(7)?, product_image_path: row.get(8)?,
            actual_sale_price_zar: row.get(9)?, fee_category: row.get(10)?,
            fee_category_confirmed: row.get::<_, Option<i32>>(11)?.map(|x| x != 0),
            fee_rate_range: row.get(12)?, success_fee_rate: row.get(13)?, success_fee_cap: row.get(14)?,
            purchase_url: row.get(15)?, sku: row.get(16)?, chinese_product_name: row.get(17)?,
            purchase_cost_cny: row.get(18)?, purchase_shipping_cny: row.get(19)?,
            purchase_quantity: row.get(20)?, length_mm: row.get(21)?, width_mm: row.get(22)?,
            height_mm: row.get(23)?, actual_weight_kg: row.get(24)?,
            packaging_cost_per_unit_cny: row.get(25)?, shipping_method: row.get(26)?,
            inbound_listing_fee_cny: row.get(27)?, outbound_operation_fee_cny: row.get(28)?,
            last_mile_delivery_fee_cny: row.get(29)?, other_fee_cny: row.get(30)?,
            fulfillment_fee_zar: row.get(31)?,
            manual_domestic_forwarding_cny: row.get(32)?,
            manual_international_shipping_cny: row.get(33)?,
            manual_overseas_op_cost_cny: row.get(34)?,
            manual_success_fee_zar: row.get(35)?,
            manual_fulfillment_fee_zar: row.get(36)?,
            manual_total_cost_zar: row.get(37)?,
            volume_cbm: row.get(38)?, volumetric_weight_kg: row.get(39)?, chargeable_weight_kg: row.get(40)?,
            domestic_forwarding_cost_per_unit_cny: row.get(41)?, unit_product_cost_cny: row.get(42)?,
            international_shipping_per_unit_cny: row.get(43)?, cost_to_sa_warehouse_cny: row.get(44)?,
            cost_to_sa_warehouse_zar: row.get(45)?, overseas_warehouse_operation_cost_cny: row.get(46)?,
            overseas_warehouse_operation_cost_zar: row.get(47)?, success_fee_zar: row.get(48)?,
            official_total_cost_zar: row.get(49)?, total_cost_zar: row.get(50)?, profit_zar: row.get(51)?,
            profit_margin: row.get(52)?, minimum_price_at_20_margin: row.get(53)?,
            minimum_price_at_15_margin: row.get(54)?, link_status: row.get(55)?,
            selection_status: row.get(56)?, exchange_rate_used: row.get(57)?,
            fee_rate_used: row.get(58)?, created_at: row.get(59)?, updated_at: row.get(60)?,
            unit_price_cny: row.get(61)?,
        })
    }

    fn to_calc_input(&self) -> CalcInput {
        CalcInput {
            length_mm: self.length_mm,
            width_mm: self.width_mm,
            height_mm: self.height_mm,
            actual_weight_kg: self.actual_weight_kg,
            actual_sale_price_zar: self.actual_sale_price_zar,
            purchase_cost_cny: self.purchase_cost_cny,
            purchase_shipping_cny: self.purchase_shipping_cny,
            purchase_quantity: self.purchase_quantity,
            packaging_cost_per_unit_cny: self.packaging_cost_per_unit_cny,
            shipping_method: self.shipping_method.clone(),
            success_fee_rate: self.success_fee_rate,
            inbound_listing_fee_cny: self.inbound_listing_fee_cny,
            outbound_operation_fee_cny: self.outbound_operation_fee_cny,
            last_mile_delivery_fee_cny: self.last_mile_delivery_fee_cny,
            other_fee_cny: self.other_fee_cny,
            fulfillment_fee_zar: self.fulfillment_fee_zar,
            fee_category: self.fee_category.clone(),
            fee_category_confirmed: self.fee_category_confirmed,
            manual_domestic_forwarding_cny: self.manual_domestic_forwarding_cny,
            manual_international_shipping_cny: self.manual_international_shipping_cny,
            manual_overseas_op_cost_cny: self.manual_overseas_op_cost_cny,
            manual_success_fee_zar: self.manual_success_fee_zar,
            manual_fulfillment_fee_zar: self.manual_fulfillment_fee_zar,
            manual_total_cost_zar: self.manual_total_cost_zar,
        }
    }
}

fn get_exchange_rate(conn: &rusqlite::Connection) -> f64 {
    conn.query_row(
        "SELECT value FROM system_settings WHERE key='cny_per_zar'",
        [],
        |row| row.get::<_, String>(0),
    )
    .ok()
    .and_then(|s| s.parse::<f64>().ok())
    .unwrap_or(0.41)
}

fn get_fee_rate(conn: &rusqlite::Connection, cat: &Option<String>) -> Option<f64> {
    cat.as_ref().and_then(|c| {
        conn.query_row(
            "SELECT success_fee_rate FROM fee_categories WHERE name=?1 AND active=1",
            [c],
            |row| row.get(0),
        ).ok()
    })
}

fn now_str() -> String {
    chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string()
}

fn gen_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn apply_calculations(p: &Product, conn: &rusqlite::Connection) -> HashMap<String, Value> {
    let rate = get_exchange_rate(conn);
    let input = p.to_calc_input();
    let mut calc = calculate_all(&input, rate);
    let margin = calc.get("profit_margin").and_then(|v| v.as_f64());
    let status = determine_selection_status(&input, margin);
    calc.insert("exchange_rate_used".into(), Value::Number(serde_json::Number::from_f64(rate).unwrap()));
    calc.insert("selection_status".into(), Value::String(status));
    calc
}

// ---- Tauri Commands ----

#[tauri::command]
pub fn get_products(
    state: State<DbState>,
    selection_status: Option<String>,
    fee_category: Option<String>,
    shipping_method: Option<Vec<String>>,
    link_status: Option<Vec<String>>,
    min_margin: Option<f64>,
    max_margin: Option<f64>,
    search: Option<String>,
    sort_by: Option<String>,
    sort_order: Option<String>,
) -> Result<Vec<Product>, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let mut sql = "SELECT * FROM products WHERE 1=1".to_string();
    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = vec![];

    if let Some(ref s) = selection_status {
        sql.push_str(" AND selection_status=?");
        params.push(Box::new(s.clone()));
    }
    if let Some(ref s) = fee_category {
        sql.push_str(" AND fee_category=?");
        params.push(Box::new(s.clone()));
    }
    if let Some(ref vals) = shipping_method {
        if !vals.is_empty() {
            let placeholders = vals.iter().map(|_| "?").collect::<Vec<&str>>().join(",");
            sql.push_str(&format!(" AND shipping_method IN ({})", placeholders));
            for v in vals {
                params.push(Box::new(v.clone()));
            }
        }
    }
    if let Some(ref vals) = link_status {
        if !vals.is_empty() {
            let placeholders = vals.iter().map(|_| "?").collect::<Vec<&str>>().join(",");
            sql.push_str(&format!(" AND link_status IN ({})", placeholders));
            for v in vals {
                params.push(Box::new(v.clone()));
            }
        }
    }
    if let Some(m) = min_margin {
        sql.push_str(" AND profit_margin >= ?");
        params.push(Box::new(m));
    }
    if let Some(m) = max_margin {
        sql.push_str(" AND profit_margin <= ?");
        params.push(Box::new(m));
    }
    if let Some(ref s) = search {
        let like = format!("%{}%", s);
        sql.push_str(" AND (product_name LIKE ? OR tsin LIKE ? OR takealot_url LIKE ?)");
        params.push(Box::new(like.clone()));
        params.push(Box::new(like.clone()));
        params.push(Box::new(like));
    }

    let col = sort_by.as_deref().unwrap_or("created_at");
    let dir = if sort_order.as_deref() == Some("asc") { "ASC" } else { "DESC" };
    sql.push_str(&format!(" ORDER BY {} {}", col, dir));

    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let rows = stmt.query_map(param_refs.as_slice(), |row| Product::from_row(row))
        .map_err(|e| e.to_string())?;
    let mut products = vec![];
    for row in rows {
        products.push(row.map_err(|e| e.to_string())?);
    }
    Ok(products)
}

#[tauri::command]
pub fn get_product(state: State<DbState>, id: String) -> Result<Product, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    conn.query_row("SELECT * FROM products WHERE id=?", [&id], |row| Product::from_row(row))
        .map_err(|e| format!("产品不存在: {}", e))
}

#[tauri::command]
pub fn create_product(state: State<DbState>, data: Value) -> Result<Product, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let id = gen_id();
    let now = now_str();
    let takealot_url = data["takealot_url"].as_str().unwrap_or("").to_string();

    // Check duplicate
    if !takealot_url.is_empty() {
        let exists: bool = conn.query_row(
            "SELECT COUNT(*) > 0 FROM products WHERE takealot_url=?", [&takealot_url],
            |row| row.get(0),
        ).unwrap_or(false);
        if exists {
            return Err("该 Takealot 链接已存在".into());
        }
    }

    // Auto number
    let max_no: Option<i32> = conn.query_row(
        "SELECT MAX(product_no) FROM products", [],
        |row| row.get(0),
    ).ok().flatten();
    let product_no = max_no.map_or(1, |n| n + 1);

    let fee_cat = data["fee_category"].as_str().map(|s| s.to_string());
    let fee_rate = get_fee_rate(&conn, &fee_cat);

    // Compute purchase_cost_cny from unit_price × quantity if unit_price is given
    let unit_price = data["unit_price_cny"].as_f64();
    let quantity = data["purchase_quantity"].as_i64().unwrap_or(4);
    let purchase_cost = match (unit_price, data["purchase_cost_cny"].as_f64()) {
        (Some(up), _) => Some(up * quantity as f64),  // unit_price always wins
        (None, pc) => pc,
    };

    conn.execute(
        "INSERT INTO products (id, product_no, recorded_at, takealot_url, tsin, product_name,
        product_image_url, actual_sale_price_zar, fee_category, fee_category_confirmed,
        purchase_url, sku, chinese_product_name, purchase_cost_cny, purchase_shipping_cny,
        purchase_quantity, length_mm, width_mm, height_mm, actual_weight_kg,
        packaging_cost_per_unit_cny, unit_price_cny, shipping_method, success_fee_rate, note,
        link_status, created_at, updated_at, selection_status, exchange_rate_used)
        VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,?21,?22,?23,?24,?25,?26,?27,?28,?29,?30)",
        rusqlite::params![
            &id, product_no, &now,
            &takealot_url,
            data["tsin"].as_str().unwrap_or(""),
            data["product_name"].as_str().unwrap_or(""),
            data["product_image_url"].as_str().unwrap_or(""),
            data["actual_sale_price_zar"].as_f64(),
            fee_cat.as_deref().unwrap_or(""),
            data["fee_category_confirmed"].as_bool().unwrap_or(false) as i32,
            data["purchase_url"].as_str().unwrap_or(""),
            data["sku"].as_str().unwrap_or(""),
            data["chinese_product_name"].as_str().unwrap_or(""),
            purchase_cost,
            data["purchase_shipping_cny"].as_f64(),
            quantity as i32,
            data["length_mm"].as_f64(),
            data["width_mm"].as_f64(),
            data["height_mm"].as_f64(),
            data["actual_weight_kg"].as_f64(),
            data["packaging_cost_per_unit_cny"].as_f64().unwrap_or(1.0),
            unit_price,
            data["shipping_method"].as_str().unwrap_or(""),
            fee_rate,
            data["note"].as_str().unwrap_or(""),
            data["link_status"].as_str().unwrap_or("未购买"),
            &now, &now, "数据待补充", 0.41,
        ],
    ).map_err(|e| e.to_string())?;

    let p = get_product_state(&conn, &id)?;
    recalc_and_update(&conn, &p)?;
    get_product_state(&conn, &id)
}

#[tauri::command]
pub fn update_product(state: State<DbState>, id: String, data: Value) -> Result<Product, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;

    let mut updates: Vec<String> = vec![];
    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = vec![];

    macro_rules! set_opt {
        ($field:expr, $key:expr) => {
            if data.get($key).is_some() {
                let v = data[$key].as_f64();
                updates.push(format!("{} = ?", $field));
                params.push(Box::new(v));
            }
        };
    }
    macro_rules! set_str {
        ($field:expr, $key:expr) => {
            if let Some(v) = data[$key].as_str() {
                updates.push(format!("{} = ?", $field));
                params.push(Box::new(v.to_string()));
            }
        };
    }

    set_str!("takealot_url", "takealot_url");
    set_str!("tsin", "tsin");
    set_str!("product_name", "product_name");
    set_str!("product_image_url", "product_image_url");
    set_str!("product_image_path", "product_image_path");
    set_opt!("actual_sale_price_zar", "actual_sale_price_zar");
    set_str!("fee_category", "fee_category");
    if let Some(v) = data["fee_category_confirmed"].as_bool() {
        updates.push("fee_category_confirmed = ?".into());
        params.push(Box::new(v as i32));
    }
    set_str!("note", "note");
    set_str!("purchase_url", "purchase_url");
    set_str!("sku", "sku");
    set_str!("chinese_product_name", "chinese_product_name");
    set_opt!("purchase_cost_cny", "purchase_cost_cny");
    set_opt!("purchase_shipping_cny", "purchase_shipping_cny");
    if let Some(v) = data["purchase_quantity"].as_i64() {
        updates.push("purchase_quantity = ?".into());
        params.push(Box::new(v as i32));
    }
    set_opt!("length_mm", "length_mm");
    set_opt!("width_mm", "width_mm");
    set_opt!("height_mm", "height_mm");
    set_opt!("actual_weight_kg", "actual_weight_kg");
    set_opt!("packaging_cost_per_unit_cny", "packaging_cost_per_unit_cny");
    set_opt!("unit_price_cny", "unit_price_cny");
    set_str!("shipping_method", "shipping_method");
    set_opt!("inbound_listing_fee_cny", "inbound_listing_fee_cny");
    set_opt!("outbound_operation_fee_cny", "outbound_operation_fee_cny");
    set_opt!("last_mile_delivery_fee_cny", "last_mile_delivery_fee_cny");
    set_opt!("other_fee_cny", "other_fee_cny");
    set_opt!("fulfillment_fee_zar", "fulfillment_fee_zar");
    set_str!("link_status", "link_status");
    // Manual overrides
    set_opt!("manual_domestic_forwarding_cny", "manual_domestic_forwarding_cny");
    set_opt!("manual_international_shipping_cny", "manual_international_shipping_cny");
    set_opt!("manual_overseas_op_cost_cny", "manual_overseas_op_cost_cny");
    set_opt!("manual_success_fee_zar", "manual_success_fee_zar");
    set_opt!("manual_fulfillment_fee_zar", "manual_fulfillment_fee_zar");
    set_opt!("manual_total_cost_zar", "manual_total_cost_zar");

    // Update fee_rate if fee_category changed
    if data.get("fee_category").is_some() {
        let new_fc = data["fee_category"].as_str().map(|s| s.to_string());
        let new_rate = get_fee_rate(&conn, &new_fc);
        updates.push("success_fee_rate = ?".into());
        params.push(Box::new(new_rate));
        // Update fee_rate_range
        if let Some(ref fc) = new_fc {
            let range: Option<String> = conn.query_row(
                "SELECT fee_rate_range FROM fee_categories WHERE name=?1 AND active=1",
                [fc], |row| row.get(0),
            ).ok();
            if let Some(r) = range {
                updates.push("fee_rate_range = ?".into());
                params.push(Box::new(r));
            }
        }
    }

    // Recompute purchase_cost_cny if unit_price_cny or purchase_quantity changed
    let unit_price_changed = data.get("unit_price_cny").is_some();
    let qty_changed = data.get("purchase_quantity").is_some();
    if unit_price_changed || qty_changed {
        // Fetch current unit_price and quantity from DB to compute
        let (cur_up, cur_qty) = conn.query_row(
            "SELECT COALESCE(unit_price_cny, 0), COALESCE(purchase_quantity, 4) FROM products WHERE id=?",
            [&id],
            |row| Ok((row.get::<_, f64>(0)?, row.get::<_, i32>(1)?)),
        ).unwrap_or((0.0, 4));
        let new_up = data["unit_price_cny"].as_f64().unwrap_or(cur_up);
        let new_qty = if let Some(v) = data["purchase_quantity"].as_i64() { v as f64 } else { cur_qty as f64 };
        let new_pc = new_up * new_qty;
        // push to updates and params - but need to be careful: set_opt! may have already pushed
        // Remove existing purchase_cost_cny update if present
        updates.retain(|u| !u.starts_with("purchase_cost_cny"));
        params.retain(|p| {
            // Can't easily filter params, just rebuild. Simpler: always append after.
            true
        });
        // Actually, just push as additional update; if set_opt! pushed a duplicate, second one wins in SQL
        updates.push("purchase_cost_cny = ?".into());
        params.push(Box::new(new_pc));
    }

    // Clear manual fields if not explicitly set (so recalc uses formula)
    let manual_fields = [
        "manual_domestic_forwarding_cny", "manual_international_shipping_cny",
        "manual_overseas_op_cost_cny", "manual_success_fee_zar",
        "manual_fulfillment_fee_zar", "manual_total_cost_zar",
    ];
    for mf in manual_fields {
        if !data.get(mf).is_some() {
            updates.push(format!("{} = NULL", mf));
        }
    }

    if !updates.is_empty() {
        updates.push("updated_at = ?".into());
        params.push(Box::new(now_str()));
        let sql = format!("UPDATE products SET {} WHERE id = ?", updates.join(", "));
        params.push(Box::new(id.clone()));
        let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
        conn.execute(&sql, param_refs.as_slice()).map_err(|e| e.to_string())?;
    }

    let p = get_product_state(&conn, &id)?;
    recalc_and_update(&conn, &p)?;
    get_product_state(&conn, &id)
}

#[tauri::command]
pub fn delete_product(state: State<DbState>, id: String) -> Result<String, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    conn.execute("DELETE FROM products WHERE id=?", [&id]).map_err(|e| e.to_string())?;
    Ok("已删除".into())
}

#[tauri::command]
pub fn get_dashboard(state: State<DbState>) -> Result<Value, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let total: i32 = conn.query_row("SELECT COUNT(*) FROM products", [], |r| r.get(0)).unwrap_or(0);
    let di: i32 = conn.query_row("SELECT COUNT(*) FROM products WHERE selection_status='数据待补充'", [], |r| r.get(0)).unwrap_or(0);
    let cp: i32 = conn.query_row("SELECT COUNT(*) FROM products WHERE selection_status='待确认品类'", [], |r| r.get(0)).unwrap_or(0);
    let q: i32 = conn.query_row("SELECT COUNT(*) FROM products WHERE selection_status='合格选品'", [], |r| r.get(0)).unwrap_or(0);
    let nr: i32 = conn.query_row("SELECT COUNT(*) FROM products WHERE selection_status='不建议选品'", [], |r| r.get(0)).unwrap_or(0);

    let avg_margin: Option<f64> = conn.query_row(
        "SELECT AVG(profit_margin) FROM products WHERE profit_margin IS NOT NULL",
        [], |r| r.get(0),
    ).ok().flatten();

    let (top_name, top_margin): (Option<String>, Option<f64>) = conn.query_row(
        "SELECT product_name, profit_margin FROM products WHERE profit_margin IS NOT NULL ORDER BY profit_margin DESC LIMIT 1",
        [], |r| Ok((r.get(0)?, r.get(1)?)),
    ).ok().unwrap_or((None, None));

    Ok(serde_json::json!({
        "total": total,
        "data_incomplete": di,
        "category_pending": cp,
        "qualified": q,
        "not_recommended": nr,
        "avg_profit_margin": avg_margin.map(|x| (x * 10000.0).round() / 10000.0),
        "top_product_name": top_name,
        "top_profit_margin": top_margin,
    }))
}

// ---- Helper functions ----

fn get_product_state(conn: &rusqlite::Connection, id: &str) -> Result<Product, String> {
    conn.query_row("SELECT * FROM products WHERE id=?", [id], |row| Product::from_row(row))
        .map_err(|e| format!("产品不存在: {}", e))
}

pub(crate) fn recalc_and_update(conn: &rusqlite::Connection, p: &Product) -> Result<(), String> {
    let calc = apply_calculations(p, conn);
    let rate = calc.get("exchange_rate_used").and_then(|v| v.as_f64()).unwrap_or(0.41);
    let status = calc.get("selection_status").and_then(|v| v.as_str()).unwrap_or("数据待补充");

    let set = |key: &str| -> Option<f64> { calc.get(key).and_then(|v| v.as_f64()) };

    conn.execute(
        "UPDATE products SET volume_cbm=?, volumetric_weight_kg=?, chargeable_weight_kg=?,
        domestic_forwarding_cost_per_unit_cny=?, unit_product_cost_cny=?,
        international_shipping_per_unit_cny=?, cost_to_sa_warehouse_cny=?,
        cost_to_sa_warehouse_zar=?, overseas_warehouse_operation_cost_cny=?,
        overseas_warehouse_operation_cost_zar=?, success_fee_zar=?,
        official_total_cost_zar=?, total_cost_zar=?, profit_zar=?, profit_margin=?,
        minimum_price_at_20_margin=?, minimum_price_at_15_margin=?,
        selection_status=?, exchange_rate_used=?, fee_rate_used=?, updated_at=?
        WHERE id=?",
        rusqlite::params![
            set("volume_cbm"), set("volumetric_weight_kg"), set("chargeable_weight_kg"),
            set("domestic_forwarding_cost_per_unit_cny"), set("unit_product_cost_cny"),
            set("international_shipping_per_unit_cny"), set("cost_to_sa_warehouse_cny"),
            set("cost_to_sa_warehouse_zar"), set("overseas_warehouse_operation_cost_cny"),
            set("overseas_warehouse_operation_cost_zar"), set("success_fee_zar"),
            set("official_total_cost_zar"), set("total_cost_zar"), set("profit_zar"),
            set("profit_margin"), set("minimum_price_at_20_margin"),
            set("minimum_price_at_15_margin"), status, rate,
            p.success_fee_rate, now_str(), p.id,
        ],
    ).map_err(|e| e.to_string())?;

    Ok(())
}
