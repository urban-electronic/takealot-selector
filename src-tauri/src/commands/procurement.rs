use rusqlite::params;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::State;
use uuid::Uuid;

use crate::DbState;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcurementRecord {
    pub id: String,
    pub product_id: Option<String>,
    pub product_no: Option<i32>,
    pub product_name: String,
    pub quantity: i32,
    pub total_amount: f64,
    pub unit_price: f64,
    pub notes: String,
    pub recorded_at: String,
}

impl ProcurementRecord {
    fn from_row(row: &rusqlite::Row) -> Result<Self, rusqlite::Error> {
        Ok(Self {
            id: row.get(0)?,
            product_id: row.get(1)?,
            product_no: row.get(2)?,
            product_name: row.get::<_, String>(3).unwrap_or_default(),
            quantity: row.get(4)?,
            total_amount: row.get(5)?,
            unit_price: row.get(6)?,
            notes: row.get::<_, String>(7).unwrap_or_default(),
            recorded_at: row.get(8)?,
        })
    }
}

fn now_str() -> String {
    chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string()
}

#[tauri::command]
pub fn list_procurement_records(state: State<DbState>) -> Result<Vec<ProcurementRecord>, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let mut stmt = conn
        .prepare("SELECT id, product_id, product_no, product_name, quantity, total_amount, unit_price, notes, recorded_at FROM procurement_records ORDER BY recorded_at DESC")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |row| ProcurementRecord::from_row(row))
        .map_err(|e| e.to_string())?;
    let mut records = Vec::new();
    for r in rows {
        records.push(r.map_err(|e| e.to_string())?);
    }
    Ok(records)
}

#[tauri::command]
pub fn get_procurement_record(state: State<DbState>, id: String) -> Result<ProcurementRecord, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    conn.query_row(
        "SELECT id, product_id, product_no, product_name, quantity, total_amount, unit_price, notes, recorded_at FROM procurement_records WHERE id=?",
        [&id],
        |row| ProcurementRecord::from_row(row),
    )
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn create_procurement_record(
    state: State<DbState>,
    data: Value,
) -> Result<ProcurementRecord, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let id = Uuid::new_v4().to_string();
    let now = now_str();

    let product_id = data["product_id"].as_str().map(|s| s.to_string());
    let product_no = data["product_no"].as_i64().map(|v| v as i32);
    let product_name = data["product_name"].as_str().unwrap_or("").to_string();
    let quantity = data["quantity"].as_i64().unwrap_or(1) as i32;
    let total_amount = data["total_amount"].as_f64().unwrap_or(0.0);
    let unit_price = if quantity > 0 {
        (total_amount / quantity as f64 * 100.0).round() / 100.0
    } else {
        0.0
    };
    let notes = data["notes"].as_str().unwrap_or("").to_string();

    // 校验 product_id 对应的产品必须存在
    if let Some(ref pid) = product_id {
        let exists: bool = conn
            .query_row(
                "SELECT COUNT(*) > 0 FROM products WHERE id = ?1",
                params![pid],
                |row| row.get(0),
            )
            .unwrap_or(false);
        if !exists {
            return Err(format!("产品不存在（id: {}）", pid));
        }
    } else {
        return Err("请先选择产品".to_string());
    }

    conn.execute(
        "INSERT INTO procurement_records (id, product_id, product_no, product_name, quantity, total_amount, unit_price, notes, recorded_at) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
        params![id, product_id, product_no, product_name, quantity, total_amount, unit_price, notes, now],
    )
    .map_err(|e| e.to_string())?;

    get_procurement_record_inner(&conn, &id)
}

#[tauri::command]
pub fn update_procurement_record(
    state: State<DbState>,
    id: String,
    data: Value,
) -> Result<ProcurementRecord, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;

    // Fetch current record for fallback
    let current = get_procurement_record_inner(&conn, &id)?;

    let product_no = if data.get("product_no").is_some() {
        data["product_no"].as_i64().map(|v| v as i32)
    } else {
        current.product_no
    };
    let product_name = data["product_name"].as_str().unwrap_or(&current.product_name).to_string();
    let quantity = if let Some(v) = data["quantity"].as_i64() {
        v as i32
    } else {
        current.quantity
    };
    let total_amount = data["total_amount"].as_f64().unwrap_or(current.total_amount);
    let unit_price = if quantity > 0 {
        (total_amount / quantity as f64 * 100.0).round() / 100.0
    } else {
        0.0
    };
    let notes = data["notes"].as_str().unwrap_or(&current.notes).to_string();
    let product_id = if data.get("product_id").is_some() {
        data["product_id"].as_str().map(|s| s.to_string())
    } else {
        current.product_id
    };

    conn.execute(
        "UPDATE procurement_records SET product_id=?1, product_no=?2, product_name=?3, quantity=?4, total_amount=?5, unit_price=?6, notes=?7 WHERE id=?8",
        params![product_id, product_no, product_name, quantity, total_amount, unit_price, notes, id],
    )
    .map_err(|e| e.to_string())?;

    get_procurement_record_inner(&conn, &id)
}

fn get_procurement_record_inner(
    conn: &rusqlite::Connection,
    id: &str,
) -> Result<ProcurementRecord, String> {
    conn.query_row(
        "SELECT id, product_id, product_no, product_name, quantity, total_amount, unit_price, notes, recorded_at FROM procurement_records WHERE id=?",
        [id],
        |row| ProcurementRecord::from_row(row),
    )
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn delete_procurement_record(state: State<DbState>, id: String) -> Result<String, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    conn.execute("DELETE FROM procurement_records WHERE id=?", [&id])
        .map_err(|e| e.to_string())?;
    Ok("已删除".into())
}

#[tauri::command]
pub fn search_products_by_no(state: State<DbState>, product_no: i32) -> Result<Value, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    conn.query_row(
        "SELECT product_no, product_name, id FROM products WHERE product_no=?",
        [product_no],
        |row| {
            Ok(serde_json::json!({
                "product_no": row.get::<_, i32>(0)?,
                "product_name": row.get::<_, String>(1)?,
                "id": row.get::<_, String>(2)?,
            }))
        },
    )
    .map_err(|e| e.to_string())
}
