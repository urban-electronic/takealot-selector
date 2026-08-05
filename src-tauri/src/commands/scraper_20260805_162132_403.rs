use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::State;
use crate::commands::db::DbState;
use std::process::Command;

#[derive(Debug, Serialize, Deserialize)]
pub struct ScrapeResult {
    pub normalized_url: String,
    pub tsin: Option<String>,
    pub product_name: Option<String>,
    pub product_image_url: Option<String>,
    pub actual_sale_price_zar: Option<f64>,
    pub in_stock_price: Option<f64>,
    pub takealot_category_path: Option<String>,
    pub warnings: Vec<String>,
    pub success: bool,
}

fn normalize_url(url: &str) -> String {
    if let Ok(parsed) = url::Url::parse(url) {
        format!("{}://{}{}", parsed.scheme(), parsed.host_str().unwrap_or(""), parsed.path())
    } else {
        url.to_string()
    }
}

/// Fetch page HTML using system curl (macOS curl uses Apple's TLS stack, bypasses Cloudflare).
fn fetch_via_curl(url: &str) -> Result<String, String> {
    let ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15";
    let output = Command::new("curl")
        .args([
            "-sL",
            "--max-time", "30",
            "--compressed",
            "-H", &format!("User-Agent: {}", ua),
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Language: en-ZA,en-US;q=0.9,en;q=0.8",
            "-H", "Accept-Encoding: gzip, deflate, br",
            "-H", "Cache-Control: no-cache",
            "-H", "DNT: 1",
            url,
        ])
        .output()
        .map_err(|e| format!("curl 执行失败: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("curl 返回错误: {}", stderr));
    }

    let html = String::from_utf8_lossy(&output.stdout).to_string();

    if html.len() < 500 {
        return Err("页面内容过短，可能被拦截".to_string());
    }

    if html.contains("Just a moment") || html.contains("Checking your browser") || html.contains("cf-browser-verification") {
        return Err("Cloudflare 安全验证截拦，请手动在浏览器中打开该页面".to_string());
    }

    Ok(html)
}

#[tauri::command]
pub async fn scrape_takealot(product_url: String) -> Result<ScrapeResult, String> {
    let normalized = normalize_url(&product_url);

    if !normalized.contains("takealot.com") {
        return Ok(ScrapeResult {
            normalized_url: normalized,
            tsin: None, product_name: None, product_image_url: None,
            actual_sale_price_zar: None, in_stock_price: None,
            takealot_category_path: None,
            warnings: vec!["URL 不属于 takealot.com".into()],
            success: false,
        });
    }

    let mut data = ScrapeResult {
        normalized_url: normalized.clone(),
        tsin: None, product_name: None, product_image_url: None,
        actual_sale_price_zar: None, in_stock_price: None,
        takealot_category_path: None,
        warnings: vec![],
        success: false,
    };

    // Extract TSIN from URL
    if let Some(caps) = regex_lite::Regex::new(r"PLID(\d+)").ok()
        .and_then(|re| re.captures(&product_url))
    {
        data.tsin = caps.get(1).map(|m| format!("PLID{}", m.as_str()));
    }

    // Fetch page via system curl (uses Apple's TLS stack)
    match fetch_via_curl(&normalized) {
        Ok(html) => {
            parse_html(&html, &mut data);
        }
        Err(msg) => {
            data.warnings.push(msg);
        }
    }

    Ok(data)
}

fn parse_html(html: &str, data: &mut ScrapeResult) {
    let doc = scraper::Html::parse_document(html);

    // Product name from <title>
    if let Some(title) = doc.select(&scraper::Selector::parse("title").unwrap()).next() {
        let title_text = title.text().collect::<String>();
        if title_text.to_lowercase().contains("takealot") {
            if let Some(part) = title_text.split('|').next() {
                let name = part.trim();
                if !name.is_empty() {
                    data.product_name = Some(name.to_string());
                }
            }
        }
    }

    // Product image
    let img_sel = scraper::Selector::parse("img[src*=\"media.takealot.com/covers_images\"]").unwrap();
    if let Some(img) = doc.select(&img_sel).next() {
        if let Some(src) = img.value().attr("src") {
            data.product_image_url = Some(src.replace("s-thumbnail", "s-pdpxl"));
        }
    }

    // Price
    let price_sel = scraper::Selector::parse("[class*=\"price-buybox\"]").unwrap();
    if let Some(el) = doc.select(&price_sel).next() {
        let text = el.text().collect::<String>().replace(" ", "");
        if let Some(caps) = regex_lite::Regex::new(r"[\d,]+\.?\d*").ok()
            .and_then(|re| re.captures(&text))
        {
            if let Some(m) = caps.get(0) {
                if let Ok(v) = m.as_str().replace(",", "").parse::<f64>() {
                    data.actual_sale_price_zar = Some(v);
                    data.in_stock_price = Some(v);
                }
            }
        }
    }

    // Category path
    let pdp_sel = scraper::Selector::parse(".pdp").unwrap();
    if let Some(pdp) = doc.select(&pdp_sel).next() {
        let a_sel = scraper::Selector::parse("a[href]").unwrap();
        let keywords = [
            "computers", "electronics", "home", "kitchen", "sport",
            "fashion", "baby", "toys", "garden", "automotive",
            "camping", "beauty", "health", "office", "books",
            "appliances", "gaming", "music", "pets", "liquor",
            "stationery", "luggage", "data storage", "drives",
        ];
        let mut cats: Vec<String> = vec![];
        let mut seen = std::collections::HashSet::new();
        for a in pdp.select(&a_sel) {
            let text = a.text().collect::<String>().trim().to_string();
            if text.is_empty() || text.len() > 50 { continue; }
            let lt = text.to_lowercase();
            if keywords.iter().any(|k| lt.contains(k)) && !seen.contains(&text) {
                seen.insert(text.clone());
                cats.push(text);
            }
        }
        if !cats.is_empty() {
            data.takealot_category_path = Some(cats.join(" > "));
        }
    }

    data.success = true;
}

#[tauri::command]
pub async fn refresh_price(state: State<'_, DbState>, id: String) -> Result<Value, String> {
    // Get URL with lock dropped before await
    let url = {
        let conn = state.conn.lock().map_err(|e| e.to_string())?;
        let u: String = conn.query_row("SELECT takealot_url FROM products WHERE id=?", [&id], |r| r.get(0))
            .map_err(|_| "产品不存在".to_string())?;
        if u.is_empty() {
            return Err("产品没有 Takealot 链接".into());
        }
        u
    };

    let result = scrape_takealot(url).await?;
    let mut updated = serde_json::json!({});
    let conn = state.conn.lock().map_err(|e| e.to_string())?;

    if let Some(price) = result.actual_sale_price_zar {
        conn.execute("UPDATE products SET actual_sale_price_zar=? WHERE id=?", rusqlite::params![price, &id])
            .map_err(|e| e.to_string())?;
        updated["actual_sale_price_zar"] = serde_json::json!(price);
    }
    if result.in_stock_price.is_some() {
        updated["in_stock_price"] = serde_json::json!(result.in_stock_price);
    }
    if let Some(ref img) = result.product_image_url {
        let has_img: bool = conn.query_row(
            "SELECT COALESCE(product_image_url,'') = '' FROM products WHERE id=?",
            [&id], |r| r.get(0),
        ).unwrap_or(false);
        if has_img {
            conn.execute("UPDATE products SET product_image_url=? WHERE id=?", rusqlite::params![img, &id])
                .map_err(|e| e.to_string())?;
            updated["product_image_url"] = serde_json::json!(img);
        }
    }
    if let Some(ref tsin) = result.tsin {
        let has_tsin: bool = conn.query_row(
            "SELECT COALESCE(tsin,'') = '' FROM products WHERE id=?",
            [&id], |r| r.get(0),
        ).unwrap_or(false);
        if has_tsin {
            conn.execute("UPDATE products SET tsin=? WHERE id=?", rusqlite::params![tsin, &id])
                .map_err(|e| e.to_string())?;
        }
    }

    // Recalculate
    let p: super::products::Product = conn.query_row(
        "SELECT * FROM products WHERE id=?", [&id],
        |row| super::products::Product::from_row(row),
    ).map_err(|e| e.to_string())?;
    super::products::recalc_and_update(&conn, &p)?;

    let p2: super::products::Product = conn.query_row(
        "SELECT * FROM products WHERE id=?", [&id],
        |row| super::products::Product::from_row(row),
    ).map_err(|e| e.to_string())?;

    updated["profit_margin"] = serde_json::json!(p2.profit_margin);
    updated["profit_zar"] = serde_json::json!(p2.profit_zar);
    updated["total_cost_zar"] = serde_json::json!(p2.total_cost_zar);
    updated["minimum_price_at_20_margin"] = serde_json::json!(p2.minimum_price_at_20_margin);
    updated["minimum_price_at_15_margin"] = serde_json::json!(p2.minimum_price_at_15_margin);
    updated["selection_status"] = serde_json::json!(p2.selection_status);

    Ok(updated)
}
