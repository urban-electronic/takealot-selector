use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, State, WebviewWindowBuilder, WebviewUrl};
use crate::commands::db::DbState;
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Debug, Serialize, Deserialize)]
pub struct ScrapeResult {
    pub normalized_url: String,
    pub tsin: Option<String>,
    pub product_name: Option<String>,
    pub product_image_url: Option<String>,
    pub actual_sale_price_zar: Option<f64>,
    pub in_stock_price: Option<f64>,
    pub takealot_category_path: Option<String>,
    pub recommended_fee_category: Option<String>,
    pub fee_category_confidence: Option<String>,
    pub fee_match_reason: Option<String>,
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

/// Fetch page HTML using a hidden WKWebView window.
/// WKWebView uses Safari's WebKit engine, which has a real browser TLS fingerprint
/// and executes JavaScript — this is the only reliable way to bypass Cloudflare.
async fn fetch_via_hidden_webview(app: &AppHandle, url: &str) -> Result<String, String> {
    use base64::Engine;
    let engine = base64::engine::general_purpose::STANDARD;

    let (tx, rx) = tokio::sync::oneshot::channel::<String>();
    let tx = Arc::new(Mutex::new(Some(tx)));
    let tx_clone = tx.clone();

    let label = format!("_scraper_{}", uuid::Uuid::new_v4());
    let target_url: url::Url = url.parse().map_err(|e| format!("URL解析失败: {}", e))?;

    // Create hidden WebView window — navigates directly to target URL
    // on_navigation must be on the builder BEFORE .build() (Tauri v2)
    let webview = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(target_url))
        .visible(false)
        .title("")
        .on_navigation(move |nav_url| {
            let s = nav_url.as_str();
            if let Some(encoded) = s.strip_prefix("scraper-callback://result/") {
                // Decode base64 back to UTF-8 HTML
                if let Ok(bytes) = engine.decode(encoded) {
                    if let Some(tx) = tx_clone.lock().unwrap().take() {
                        let html = String::from_utf8_lossy(&bytes).to_string();
                        let _ = tx.send(html);
                    }
                }
                false // Cancel navigation — no actual page change
            } else {
                true // Allow normal navigation (e.g., Cloudflare redirect)
            }
        })
        .build()
        .map_err(|e| format!("创建抓取窗口失败: {}", e))?;

    // Wait for Cloudflare challenge JS to execute and page to fully render
    tokio::time::sleep(Duration::from_secs(10)).await;

    // Inject JS that extracts HTML, converts to base64, then navigates to callback URL
    let script = r#"
    setTimeout(function() {
        var html = document.documentElement.outerHTML;
        // Convert UTF-8 → Latin-1 → base64 (works in all WKWebView versions)
        var encoded = btoa(unescape(encodeURIComponent(html)));
        window.location.href = 'scraper-callback://result/' + encoded;
    }, 3000);
    "#;

    webview.eval(script).map_err(|e| format!("注入脚本失败: {}", e))?;

    // Wait for result with 25s total timeout (10s sleep + 3s setTimeout + 12s buffer)
    match tokio::time::timeout(Duration::from_secs(25), rx).await {
        Ok(Ok(html)) => {
            let _ = webview.close();
            if html.len() < 500 {
                Err("页面内容过短，可能被拦截".to_string())
            } else {
                Ok(html)
            }
        }
        Ok(Err(_)) => {
            let _ = webview.close();
            Err("接收HTML失败".to_string())
        }
        Err(_) => {
            let _ = webview.close();
            Err("页面抓取超时".to_string())
        }
    }
}

#[tauri::command]
pub async fn scrape_takealot(app: AppHandle, product_url: String) -> Result<ScrapeResult, String> {
    let normalized = normalize_url(&product_url);

    if !normalized.contains("takealot.com") {
        return Ok(ScrapeResult {
            normalized_url: normalized,
            tsin: None, product_name: None, product_image_url: None,
            actual_sale_price_zar: None, in_stock_price: None,
            takealot_category_path: None,
            recommended_fee_category: None, fee_category_confidence: None, fee_match_reason: None,
            warnings: vec!["URL 不属于 takealot.com".into()],
            success: false,
        });
    }

    let mut data = ScrapeResult {
        normalized_url: normalized.clone(),
        tsin: None, product_name: None, product_image_url: None,
        actual_sale_price_zar: None, in_stock_price: None,
        takealot_category_path: None,
        recommended_fee_category: None, fee_category_confidence: None, fee_match_reason: None,
        warnings: vec![],
        success: false,
    };

    // Extract TSIN from URL
    if let Some(caps) = regex_lite::Regex::new(r"PLID(\d+)").ok()
        .and_then(|re| re.captures(&product_url))
    {
        data.tsin = caps.get(1).map(|m| format!("PLID{}", m.as_str()));
    }

    // Fetch page via hidden WKWebView (real Safari engine, bypasses all Cloudflare)
    match fetch_via_hidden_webview(&app, &normalized).await {
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

    // Product name — try multiple sources, priority: og:title > h1 > <title>
    let mut product_name: Option<String> = None;

    // 1. Try og:title meta tag
    let og_sel = scraper::Selector::parse("meta[property=\"og:title\"]").unwrap();
    if let Some(og) = doc.select(&og_sel).next() {
        if let Some(content) = og.value().attr("content") {
            let ct = content.trim().to_string();
            if !ct.is_empty() && !ct.eq_ignore_ascii_case("Takealot") {
                product_name = Some(ct);
            }
        }
    }

    // 2. Try h1 tag
    if product_name.is_none() {
        let h1_sel = scraper::Selector::parse("h1").unwrap();
        if let Some(h1) = doc.select(&h1_sel).next() {
            let text = h1.text().collect::<String>().trim().to_string();
            if !text.is_empty() && !text.contains("takealot") && text.len() > 3 {
                product_name = Some(text);
            }
        }
    }

    // 3. Fallback to <title> split by |
    if product_name.is_none() {
        if let Some(title) = doc.select(&scraper::Selector::parse("title").unwrap()).next() {
            let title_text = title.text().collect::<String>();
            if title_text.to_lowercase().contains("takealot") {
                if let Some(part) = title_text.split('|').next() {
                    let name = part.trim().to_string();
                    if !name.is_empty() && !name.contains("Online Shopping") && !name.contains("takealot.com") {
                        product_name = Some(name);
                    }
                }
            }
        }
    }

    data.product_name = product_name;

    // Product image
    let img_sel = scraper::Selector::parse("img[src*=\"media.takealot.com/covers_images\"]").unwrap();
    if let Some(img) = doc.select(&img_sel).next() {
        if let Some(src) = img.value().attr("src") {
            data.product_image_url = Some(src.replace("s-thumbnail", "s-pdpxl"));
        }
    }

    // Price — try multiple selectors (Takealot changes CSS classes often)
    let price_selectors = [
        "[class*=\"price-buybox\"]",
        "[data-ref=\"buybox-price-main\"]",
        ".buybox-actions-container [class*=\"price\"]",
        ".stock-info-container [class*=\"price\"]",
        "[class*=\"buybox\"] [class*=\"price\"]",
        ".pdp [class*=\"price\"] span",
    ];
    let mut found_price = false;
    for sel_str in &price_selectors {
        if found_price { break; }
        if let Ok(sel) = scraper::Selector::parse(sel_str) {
            for el in doc.select(&sel) {
                let text = el.text().collect::<String>().replace(" ", "").replace("R", "").replace("r", "");
                if let Some(caps) = regex_lite::Regex::new(r"[\d,]+\.?\d*").ok()
                    .and_then(|re| re.captures(&text))
                {
                    if let Some(m) = caps.get(0) {
                        if let Ok(v) = m.as_str().replace(",", "").parse::<f64>() {
                            if v > 0.0 {
                                data.actual_sale_price_zar = Some(v);
                                data.in_stock_price = Some(v);
                                found_price = true;
                                break;
                            }
                        }
                    }
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

    // Fee category recommendation
    recommend_fee_category(data);

    data.success = true;
}

fn recommend_fee_category(data: &mut ScrapeResult) {
    let cat_path = match &data.takealot_category_path {
        Some(p) => p,
        None => {
            data.recommended_fee_category = None;
            data.fee_category_confidence = Some("low".into());
            data.fee_match_reason = None;
            return;
        }
    };

    let lower = cat_path.to_lowercase();

    let rules: Vec<(&str, &str, &str)> = vec![
        ("computers", "Computer Components", "分类含 Computers"),
        ("electronics", "Electronics", "分类含 Electronics"),
        ("home", "Home & Kitchen", "分类含 Home"),
        ("kitchen", "Home & Kitchen", "分类含 Kitchen"),
        ("sport", "Sports & Outdoors", "分类含 Sport"),
        ("fashion", "Fashion", "分类含 Fashion"),
        ("baby", "Baby", "分类含 Baby"),
        ("toys", "Toys", "分类含 Toys"),
        ("garden", "Garden & Outdoor", "分类含 Garden"),
        ("automotive", "Automotive", "分类含 Automotive"),
        ("camping", "Camping & Outdoor", "分类含 Camping"),
        ("beauty", "Beauty", "分类含 Beauty"),
        ("health", "Health & Personal Care", "分类含 Health"),
        ("office", "Office", "分类含 Office"),
        ("books", "Books", "分类含 Books"),
        ("appliances", "Appliances", "分类含 Appliances"),
        ("gaming", "Gaming", "分类含 Gaming"),
        ("music", "Music", "分类含 Music"),
        ("pets", "Pet Supplies", "分类含 Pets"),
        ("liquor", "Liquor", "分类含 Liquor"),
        ("stationery", "Stationery", "分类含 Stationery"),
        ("luggage", "Luggage", "分类含 Luggage"),
        ("data storage", "Computer Components", "分类含 Data Storage"),
        ("drives", "Computer Components", "分类含 Drives"),
    ];

    for (keyword, fee_name, reason) in &rules {
        if lower.contains(keyword) {
            data.recommended_fee_category = Some(fee_name.to_string());
            data.fee_category_confidence = Some("high".into());
            data.fee_match_reason = Some(reason.to_string());
            return;
        }
    }

    data.recommended_fee_category = None;
    data.fee_category_confidence = Some("medium".into());
    data.fee_match_reason = None;
}

#[tauri::command]
pub async fn refresh_price(app: AppHandle, state: State<'_, DbState>, id: String) -> Result<Value, String> {
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

    let result = scrape_takealot(app, url).await?;
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

/// Translate an English product name to Chinese via MyMemory API (free, no key needed).
/// Returns up to 10 Chinese characters — truncated if longer.
#[tauri::command]
pub async fn translate_product_name(text: String) -> Result<serde_json::Value, String> {
    if text.trim().is_empty() {
        return Ok(serde_json::json!({ "chinese_name": "" }));
    }

    // Use system curl to call MyMemory translation API
    let encoded = urlencoding_for_curl(&text);
    let api_url = format!(
        "https://api.mymemory.translated.net/get?q={}&langpair=en|zh&de=me@email.com",
        encoded
    );

    let output = std::process::Command::new("curl")
        .args(["-s", "--connect-timeout", "10", "--max-time", "15", &api_url])
        .output()
        .map_err(|e| format!("curl 执行失败: {}", e))?;

    if !output.status.success() {
        return Ok(serde_json::json!({ "chinese_name": "" }));
    }

    let body = String::from_utf8_lossy(&output.stdout);
    let parsed: serde_json::Value =
        serde_json::from_str(&body).map_err(|e| format!("JSON 解析失败: {}", e))?;

    let cn = parsed
        .get("responseData")
        .and_then(|d| d.get("translatedText"))
        .and_then(|t| t.as_str())
        .map(|s| truncate_chinese(s, 10))
        .unwrap_or_default();

    Ok(serde_json::json!({ "chinese_name": cn }))
}

/// URL-encode a string in the same way curl --data-urlencode would
fn urlencoding_for_curl(s: &str) -> String {
    let mut result = String::with_capacity(s.len() * 3);
    for byte in s.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                result.push(byte as char);
            }
            b' ' => result.push_str("%20"),
            _ => {
                result.push_str(&format!("%{:02X}", byte));
            }
        }
    }
    result
}

/// Truncate to max_len Chinese characters — CJK chars count as 1, ASCII as 0.5
fn truncate_chinese(s: &str, max_len: usize) -> String {
    let mut result = String::with_capacity(s.len());
    let mut width: f64 = 0.0;
    for ch in s.chars() {
        let w = if ch > '\u{2e80}' { 1.0 } else { 0.5 };
        if width + w > max_len as f64 + 0.1 {
            break;
        }
        width += w;
        result.push(ch);
    }
    result
}
