use rusqlite::Connection;
use std::sync::Mutex;
use std::path::PathBuf;

pub struct DbState {
    pub conn: Mutex<Connection>,
}

pub fn init_db(db_path: &str) -> Result<Connection, rusqlite::Error> {
    let conn = Connection::open(db_path)?;
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;

    conn.execute_batch("
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            product_no INTEGER,
            recorded_at TEXT,
            note TEXT DEFAULT '',
            takealot_url TEXT DEFAULT '',
            tsin TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            product_image_url TEXT DEFAULT '',
            product_image_path TEXT DEFAULT '',
            actual_sale_price_zar REAL,
            fee_category TEXT,
            fee_category_confirmed INTEGER DEFAULT 0,
            fee_rate_range TEXT DEFAULT '',
            success_fee_rate REAL,
            success_fee_cap REAL,
            purchase_url TEXT DEFAULT '',
            sku TEXT DEFAULT '',
            chinese_product_name TEXT DEFAULT '',
            purchase_cost_cny REAL,
            purchase_shipping_cny REAL,
            purchase_quantity INTEGER DEFAULT 4,
            length_mm REAL,
            width_mm REAL,
            height_mm REAL,
            actual_weight_kg REAL,
            packaging_cost_per_unit_cny REAL DEFAULT 1.0,
            shipping_method TEXT,
            inbound_listing_fee_cny REAL DEFAULT 0.75,
            outbound_operation_fee_cny REAL DEFAULT 0.70,
            last_mile_delivery_fee_cny REAL DEFAULT 2.00,
            other_fee_cny REAL DEFAULT 2.00,
            fulfillment_fee_zar REAL DEFAULT 42.00,
            manual_domestic_forwarding_cny REAL,
            manual_international_shipping_cny REAL,
            manual_overseas_op_cost_cny REAL,
            manual_success_fee_zar REAL,
            manual_fulfillment_fee_zar REAL,
            manual_total_cost_zar REAL,
            volume_cbm REAL,
            volumetric_weight_kg REAL,
            chargeable_weight_kg REAL,
            domestic_forwarding_cost_per_unit_cny REAL,
            unit_product_cost_cny REAL,
            international_shipping_per_unit_cny REAL,
            cost_to_sa_warehouse_cny REAL,
            cost_to_sa_warehouse_zar REAL,
            overseas_warehouse_operation_cost_cny REAL,
            overseas_warehouse_operation_cost_zar REAL,
            success_fee_zar REAL,
            official_total_cost_zar REAL,
            total_cost_zar REAL,
            profit_zar REAL,
            profit_margin REAL,
            minimum_price_at_20_margin REAL,
            minimum_price_at_15_margin REAL,
            link_status TEXT DEFAULT '未购买',
            selection_status TEXT DEFAULT '数据待补充',
            exchange_rate_used REAL DEFAULT 0.41,
            fee_rate_used REAL,
            created_at TEXT,
            updated_at TEXT,
            unit_price_cny REAL
        );

        CREATE TABLE IF NOT EXISTS fee_categories (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            fee_rate_range TEXT DEFAULT '',
            success_fee_rate REAL NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS fee_mapping_rules (
            id TEXT PRIMARY KEY,
            takealot_category_pattern TEXT DEFAULT '',
            title_keyword_pattern TEXT DEFAULT '',
            fee_category TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_by_user INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS scrape_logs (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            original_url TEXT DEFAULT '',
            scrape_time TEXT,
            original_title TEXT DEFAULT '',
            original_price TEXT DEFAULT '',
            result TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE IF NOT EXISTS system_settings (
            id TEXT PRIMARY KEY,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT
        );
    ")?;

    Ok(conn)
}

pub fn get_db_dir() -> PathBuf {
    // Use the app's data directory so the DB persists
    if let Ok(dir) = std::env::var("TAURI_APP_DATA_DIR") {
        let path = PathBuf::from(&dir);
        let _ = std::fs::create_dir_all(&path);
        return path;
    }
    // macOS fallback: ~/Library/Application Support/com.takealot.selector/
    if let Ok(home) = std::env::var("HOME") {
        let path = PathBuf::from(&home).join("Library/Application Support/com.takealot.selector");
        let _ = std::fs::create_dir_all(&path);
        return path;
    }
    // Last resort: use current working directory
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}
