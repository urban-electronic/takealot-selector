use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::State;
use crate::commands::db::DbState;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct FeeCategory {
    pub id: String,
    pub name: String,
    pub fee_rate_range: Option<String>,
    pub success_fee_rate: f64,
    pub active: bool,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct FeeMappingRule {
    pub id: String,
    pub takealot_category_pattern: Option<String>,
    pub title_keyword_pattern: Option<String>,
    pub fee_category: String,
    pub priority: i32,
    pub active: bool,
    pub created_by_user: bool,
    pub created_at: Option<String>,
}

#[tauri::command]
pub fn get_fee_categories(state: State<DbState>) -> Result<Vec<FeeCategory>, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let mut stmt = conn.prepare("SELECT id, name, fee_rate_range, success_fee_rate, active, created_at, updated_at FROM fee_categories")
        .map_err(|e| e.to_string())?;
    let rows = stmt.query_map([], |row| {
        Ok(FeeCategory {
            id: row.get(0)?,
            name: row.get(1)?,
            fee_rate_range: row.get(2)?,
            success_fee_rate: row.get(3)?,
            active: row.get::<_, i32>(4)? != 0,
            created_at: row.get(5)?,
            updated_at: row.get(6)?,
        })
    }).map_err(|e| e.to_string())?;
    let mut cats = vec![];
    for r in rows { cats.push(r.map_err(|e| e.to_string())?); }
    Ok(cats)
}

#[tauri::command]
pub fn update_fee_category(state: State<DbState>, id: String, data: Value) -> Result<FeeCategory, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let now = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string();

    if let Some(v) = data["success_fee_rate"].as_f64() {
        conn.execute("UPDATE fee_categories SET success_fee_rate=?, updated_at=? WHERE id=?",
            rusqlite::params![v, &now, &id]).map_err(|e| e.to_string())?;
    }
    if let Some(v) = data["fee_rate_range"].as_str() {
        conn.execute("UPDATE fee_categories SET fee_rate_range=?, updated_at=? WHERE id=?",
            rusqlite::params![v, &now, &id]).map_err(|e| e.to_string())?;
    }
    if let Some(v) = data["active"].as_bool() {
        conn.execute("UPDATE fee_categories SET active=?, updated_at=? WHERE id=?",
            rusqlite::params![v as i32, &now, &id]).map_err(|e| e.to_string())?;
    }

    conn.query_row(
        "SELECT id, name, fee_rate_range, success_fee_rate, active, created_at, updated_at FROM fee_categories WHERE id=?",
        [&id], |row| Ok(FeeCategory {
            id: row.get(0)?, name: row.get(1)?, fee_rate_range: row.get(2)?,
            success_fee_rate: row.get(3)?, active: row.get::<_, i32>(4)? != 0,
            created_at: row.get(5)?, updated_at: row.get(6)?,
        }),
    ).map_err(|e| format!("费率类别不存在: {}", e))
}

#[tauri::command]
pub fn get_fee_mapping_rules(state: State<DbState>) -> Result<Vec<FeeMappingRule>, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let mut stmt = conn.prepare(
        "SELECT id, takealot_category_pattern, title_keyword_pattern, fee_category, priority, active, created_by_user, created_at FROM fee_mapping_rules"
    ).map_err(|e| e.to_string())?;
    let rows = stmt.query_map([], |row| {
        Ok(FeeMappingRule {
            id: row.get(0)?, takealot_category_pattern: row.get(1)?,
            title_keyword_pattern: row.get(2)?, fee_category: row.get(3)?,
            priority: row.get(4)?, active: row.get::<_, i32>(5)? != 0,
            created_by_user: row.get::<_, i32>(6)? != 0,
            created_at: row.get(7)?,
        })
    }).map_err(|e| e.to_string())?;
    let mut rules = vec![];
    for r in rows { rules.push(r.map_err(|e| e.to_string())?); }
    Ok(rules)
}

#[tauri::command]
pub fn create_fee_mapping_rule(state: State<DbState>, data: Value) -> Result<FeeMappingRule, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let id = uuid::Uuid::new_v4().to_string();
    let now = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string();
    conn.execute(
        "INSERT INTO fee_mapping_rules (id, takealot_category_pattern, title_keyword_pattern, fee_category, priority, active, created_by_user, created_at) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
        rusqlite::params![
            &id,
            data["takealot_category_pattern"].as_str().unwrap_or(""),
            data["title_keyword_pattern"].as_str().unwrap_or(""),
            data["fee_category"].as_str().unwrap_or(""),
            data["priority"].as_i64().unwrap_or(0) as i32,
            data["active"].as_bool().unwrap_or(true) as i32,
            data["created_by_user"].as_bool().unwrap_or(true) as i32,
            &now,
        ],
    ).map_err(|e| e.to_string())?;
    conn.query_row(
        "SELECT id, takealot_category_pattern, title_keyword_pattern, fee_category, priority, active, created_by_user, created_at FROM fee_mapping_rules WHERE id=?",
        [&id], |row| Ok(FeeMappingRule {
            id: row.get(0)?, takealot_category_pattern: row.get(1)?,
            title_keyword_pattern: row.get(2)?, fee_category: row.get(3)?,
            priority: row.get(4)?, active: row.get::<_, i32>(5)? != 0,
            created_by_user: row.get::<_, i32>(6)? != 0,
            created_at: row.get(7)?,
        }),
    ).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn update_fee_mapping_rule(state: State<DbState>, id: String, data: Value) -> Result<FeeMappingRule, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let mut updates = vec![];
    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = vec![];
    if let Some(v) = data["takealot_category_pattern"].as_str() { updates.push("takealot_category_pattern = ?"); params.push(Box::new(v.to_string())); }
    if let Some(v) = data["title_keyword_pattern"].as_str() { updates.push("title_keyword_pattern = ?"); params.push(Box::new(v.to_string())); }
    if let Some(v) = data["fee_category"].as_str() { updates.push("fee_category = ?"); params.push(Box::new(v.to_string())); }
    if let Some(v) = data["priority"].as_i64() { updates.push("priority = ?"); params.push(Box::new(v as i32)); }
    if let Some(v) = data["active"].as_bool() { updates.push("active = ?"); params.push(Box::new(v as i32)); }
    if !updates.is_empty() {
        let sql = format!("UPDATE fee_mapping_rules SET {} WHERE id=?", updates.join(", "));
        params.push(Box::new(id.clone()));
        let refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
        conn.execute(&sql, refs.as_slice()).map_err(|e| e.to_string())?;
    }
    conn.query_row(
        "SELECT id, takealot_category_pattern, title_keyword_pattern, fee_category, priority, active, created_by_user, created_at FROM fee_mapping_rules WHERE id=?",
        [&id], |row| Ok(FeeMappingRule {
            id: row.get(0)?, takealot_category_pattern: row.get(1)?,
            title_keyword_pattern: row.get(2)?, fee_category: row.get(3)?,
            priority: row.get(4)?, active: row.get::<_, i32>(5)? != 0,
            created_by_user: row.get::<_, i32>(6)? != 0,
            created_at: row.get(7)?,
        }),
    ).map_err(|e| format!("规则不存在: {}", e))
}

/// Initialize default fee categories and mapping rules
pub fn init_default_data(conn: &rusqlite::Connection) {
    let count: i32 = conn.query_row("SELECT COUNT(*) FROM fee_categories", [], |r| r.get(0)).unwrap_or(0);
    if count > 0 { return; }

    let cats = [
        ("Clothing & Footwear", "10.0%–18.0%", 0.18),
        ("Sport", "12.0%–15.0%", 0.15),
        ("Music & DVD", "10.0%–15.0%", 0.15),
        ("Luggage & Travel", "15.0%–15.0%", 0.15),
        ("Homeware", "15.0%–15.0%", 0.15),
        ("Games", "5.5%–15.0%", 0.15),
        ("Camping & Outdoor", "8.0%–15.0%", 0.15),
        ("Beauty", "10.0%–15.0%", 0.15),
        ("Baby", "12.0%–15.0%", 0.15),
        ("Stationery", "10.0%–14.0%", 0.14),
        ("Smart Home & Connected Living", "5.0%–14.0%", 0.14),
        ("Garden, Pool & Patio", "12.0%–14.0%", 0.14),
        ("Electronic Accessories", "10.0%–14.0%", 0.14),
        ("Books", "14.0%–14.0%", 0.14),
        ("TV & Audio", "5.5%–12.0%", 0.12),
        ("Toys", "12.0%–12.0%", 0.12),
        ("Small Appliances", "10.0%–12.0%", 0.12),
        ("Office", "7.0%–12.0%", 0.12),
        ("Musical Instruments", "8.0%–12.0%", 0.12),
        ("Health", "10.0%–12.0%", 0.12),
        ("DIY & Automotive", "10.0%–12.0%", 0.12),
        ("Cameras", "4.0%–12.0%", 0.12),
        ("Pets", "10.0%–10.0%", 0.10),
        ("Office Furniture", "10.0%–10.0%", 0.10),
        ("Liquor", "7.0%–10.0%", 0.10),
        ("Large Appliances", "8.0%–10.0%", 0.10),
        ("Computers & Laptops", "5.0%–9.0%", 0.09),
        ("Computer Components", "6.0%–9.0%", 0.09),
        ("Non-Perishable", "8.0%–8.0%", 0.08),
    ];
    let now = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string();
    for (name, range, rate) in &cats {
        let _ = conn.execute(
            "INSERT INTO fee_categories (id, name, fee_rate_range, success_fee_rate, active, created_at, updated_at) VALUES (?1,?2,?3,?4,1,?5,?5)",
            rusqlite::params![uuid::Uuid::new_v4().to_string(), name, range, rate, &now],
        );
    }

    // Init settings
    let _ = conn.execute(
        "INSERT OR IGNORE INTO system_settings (id, key, value, updated_at) VALUES (?1,'cny_per_zar','0.41',?2)",
        rusqlite::params![uuid::Uuid::new_v4().to_string(), &now],
    );

    // Init mapping rules
    let rules = [
        ("Computers", "Computers & Laptops", 100),
        ("Computer Components", "Computer Components", 100),
        ("TV & Audio", "TV & Audio", 100),
        ("Cameras", "Cameras", 100),
        ("Small Appliances", "Small Appliances", 90),
        ("Large Appliances", "Large Appliances", 90),
        ("Sport", "Sport", 90),
        ("Camping", "Camping & Outdoor", 90),
        ("Beauty", "Beauty", 90),
        ("Health", "Health", 90),
        ("Baby", "Baby", 90),
        ("Toys", "Toys", 90),
        ("Books", "Books", 90),
        ("Pets", "Pets", 90),
        ("Liquor", "Liquor", 90),
        ("Garden", "Garden, Pool & Patio", 90),
        ("Stationery", "Stationery", 90),
        ("Office", "Office", 90),
        ("Musical Instruments", "Musical Instruments", 90),
        ("Homeware", "Homeware", 80),
        ("Clothing", "Clothing & Footwear", 80),
        ("Fashion", "Clothing & Footwear", 80),
        ("Luggage", "Luggage & Travel", 80),
        ("DIY", "DIY & Automotive", 80),
        ("Automotive", "DIY & Automotive", 80),
        ("Electronic Accessories", "Electronic Accessories", 80),
        ("Music", "Music & DVD", 70),
        ("Games", "Games", 70),
        ("Smart Home", "Smart Home & Connected Living", 70),
        ("Drives & Storage", "Computer Components", 60),
        ("Data Storage", "Computer Components", 60),
    ];
    for (pattern, cat, priority) in &rules {
        let _ = conn.execute(
            "INSERT INTO fee_mapping_rules (id, takealot_category_pattern, fee_category, priority, active, created_by_user, created_at) VALUES (?1,?2,?3,?4,1,0,?5)",
            rusqlite::params![uuid::Uuid::new_v4().to_string(), pattern, cat, priority, &now],
        );
    }
}
