mod commands;

use tauri::Manager;
use commands::db::{init_db, get_db_dir, DbState};
use commands::categories::init_default_data;
use commands::seed::run_seed;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let db_dir = get_db_dir();
            let db_path = db_dir.join("takealot_selector.db");
            let conn = init_db(db_path.to_str().unwrap_or("takealot_selector.db"))
                .expect("Failed to initialize database");
            init_default_data(&conn);
            run_seed(&conn);
            app.manage(DbState { conn: std::sync::Mutex::new(conn) });
            Ok(())
        })
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            commands::products::get_products,
            commands::products::get_product,
            commands::products::create_product,
            commands::products::update_product,
            commands::products::delete_product,
            commands::products::get_dashboard,
            commands::scraper::scrape_takealot,
            commands::scraper::refresh_price,
            commands::scraper::translate_product_name,
            commands::categories::get_fee_categories,
            commands::categories::update_fee_category,
            commands::categories::get_fee_mapping_rules,
            commands::categories::create_fee_mapping_rule,
            commands::categories::update_fee_mapping_rule,
            commands::settings::get_settings,
            commands::settings::update_settings,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
