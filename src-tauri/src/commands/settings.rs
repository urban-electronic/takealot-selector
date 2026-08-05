use serde_json::Value;
use tauri::State;
use std::collections::HashMap;
use crate::commands::db::DbState;

#[tauri::command]
pub fn get_settings(state: State<DbState>) -> Result<HashMap<String, String>, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let mut stmt = conn.prepare("SELECT key, value FROM system_settings")
        .map_err(|e| e.to_string())?;
    let rows = stmt.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    }).map_err(|e| e.to_string())?;
    let mut map = HashMap::new();
    for r in rows { let (k, v) = r.map_err(|e| e.to_string())?; map.insert(k, v); }
    Ok(map)
}

#[tauri::command]
pub fn update_settings(state: State<DbState>, data: Value) -> Result<String, String> {
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let now = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string();

    if let Some(obj) = data.as_object() {
        for (key, val) in obj {
            let v_str = val.as_str().unwrap_or("").to_string();
            let exists: bool = conn.query_row(
                "SELECT COUNT(*) > 0 FROM system_settings WHERE key=?",
                [key], |r| r.get(0),
            ).unwrap_or(false);
            if exists {
                conn.execute("UPDATE system_settings SET value=?, updated_at=? WHERE key=?",
                    rusqlite::params![&v_str, &now, key]).map_err(|e| e.to_string())?;
            } else {
                conn.execute("INSERT INTO system_settings (id, key, value, updated_at) VALUES (?1,?2,?3,?4)",
                    rusqlite::params![uuid::Uuid::new_v4().to_string(), key, &v_str, &now])
                    .map_err(|e| e.to_string())?;
            }
        }
    }
    Ok("设置已更新".into())
}
