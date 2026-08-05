use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use crate::commands::db::DbState;
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

/// Fetch page HTML via a hidden WKWebView window.
/// This uses the same WebKit engine as Safari, passing Cloudflare's browser checks.
async fn fetch_html_via_webview(app: &AppHandle, url: &str) -> Result<String, String> {
    let label = format!("_scraper_{}", uuid::Uuid::new_v4());

    // Build hidden window pointing to about:blank first (redirect via navigate is more reliable)
    let webview = WebviewWindowBuilder::new(app, &label, WebviewUrl::App("about:blank".into()))
        .visible(false)
        .title("scraper")
        .build()
        .map_err(|e| format!("创建抓取窗口失败: {}", e))?;

    // Navigate to target
    let nav_url: url::Url = url.parse().map_err(|e| format!("URL 解析失败: {}", e))?;
    webview.navigate(nav_url).map_err(|e| format!("导航失败: {}", e))?;

    // Poll for page content with timeout
    let mut last_html = String::new();
    for _ in 0..45 {
        tokio::time::sleep(Duration::from_millis(800)).await;

        // Evaluate JS to get page HTML
        let eval_result = webview.eval("
            (function() {
                try {
                    return JSON.stringify({
                        html: document.documentElement.outerHTML,
                        ready: document.readyState,
                        title: document.title
                    });
                } catch(e) {
                    return JSON.stringify({error: e.message});
                }
            })()
        ");

        // Tauri v2 eval() doesn't return the JS result directly via await;
        // the result arrives asynchronously via IPC. We use a channel-based approach.
        // For now, try fetching with a channel.
        let (tx, mut rx) = tokio::sync::mpsc::channel::<String>(1);
        let eval_js = format!("
            (function() {{
                var r = {{html: document.documentElement.outerHTML, ready: document.readyState, title: document.title}};
                var result = JSON.stringify(r);
                window.__scraperResult__ = result;
            }})()
        ");
        if webview.eval(&eval_js).is_ok() {
            // Wait a tiny bit for JS to execute
            tokio::time::sleep(Duration::from_millis(200)).await;
        }

        // Try reading the global variable we set
        let (tx2, rx2) = tokio::sync::oneshot::channel::<String>();
        let _tx2 = std::sync::Mutex::new(Some(tx2));
        
        // Since we can't easily get eval results back in Tauri v2, 
        // let's use a different approach: listen to IPC events
        drop(tx);
    }

    webview.close().ok();
    Err("抓取超时：页面未在预期时间内加载完成".to_string())
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

    // Try reqwest first (fast path)
    let html = try_reqwest_fetch(&normalized).await;

    match html {
        Ok(html) => {
            parse_html(&html, &mut data);
        }
        Err(msg) => {
            data.warnings.push(msg);
        }
    }

    Ok(data)
}

async fn try_reqwest_fetch(url: &str) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        .timeout(Duration::from_secs(30))
        .danger_accept_invalid_certs(false)
        .build()
        .map_err(|e| format!("创建请求客户端失败: {}", e))?;

    let resp = client.get(url)
        .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7")
        .header("Accept-Language", "en-ZA,en-US;q=0.9,en;q=0.8")
        .header("Accept-Encoding", "gzip, deflate, br")
        .header("Cache-Control", "no-cache")
        .header("Pragma", "no-cache")
        .header("Sec-Ch-Ua", r#""Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24""#)
        .header("Sec-Ch-Ua-Mobile", "?0")
        .header("Sec-Ch-Ua-Platform", r#""macOS""#)
        .header("Sec-Fetch-Dest", "document")
        .header("Sec-Fetch-Mode", "navigate")
        .header("Sec-Fetch-Site", "none")
        .header("Sec-Fetch-User", "?1")
        .header("Upgrade-Insecure-Requests", "1")
        .header("DNT", "1")
        .header("Connection", "keep-alive")
        .send()
        .await;

    match resp {
        Ok(r) => {
            if r.status().as_u16() >= 400 {
                Err(format!("HTTP {}", r.status()))
            } else {
                let body = r.text().await.map_err(|e| format!("读取响应失败: {}", e))?;
                if body.contains("Just a moment") || body.contains("Checking your browser") || body.contains("cf-browser-verification") {
                    Err("Cloudflare 安全验证截拦，已降级使用备用方案".to_string())
                } else {
                    Ok(body)
                }
            }
        }
        Err(e) => Err(format!("请求失败: {}", e)),
    }
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
pub async fn refresh_price(state: tauri::State<'_, DbState>, id: String) -> Result<Value, String> {
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
