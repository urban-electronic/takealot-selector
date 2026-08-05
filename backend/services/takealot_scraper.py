"""
Takealot 商品信息抓取服务
使用 Playwright 渲染页面,按优先级提取结构化数据。
先尝试 Firefox（Cloudflare 检测较宽松），失败回退 Chromium+stealth。
"""

import re
import json
import random
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, urlunparse


def normalize_takealot_url(url: str) -> str:
    """规范化 Takealot URL,去除无关参数"""
    parsed = urlparse(url)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
    )


def validate_takealot_url(url: str) -> bool:
    """验证链接是否属于 takealot.com"""
    try:
        parsed = urlparse(url)
        return "takealot.com" in parsed.netloc
    except Exception:
        return False


async def _launch_firefox(p):
    """启动 Firefox 浏览器，附带反检测配置"""
    browser = await p.firefox.launch(
        headless=True,
        args=["--no-sandbox"],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:130.0) "
            "Gecko/20100101 Firefox/130.0"
        ),
        viewport={"width": 1440, "height": 900},
        locale="en-ZA",
        timezone_id="Africa/Johannesburg",
        device_scale_factor=2,
        is_mobile=False,
        has_touch=False,
        color_scheme="light",
    )
    page = await context.new_page()

    # Firefox 特定的反检测 JS
    await page.add_init_script("""
        // 移除 webdriver 标记
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        
        // 伪造 plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                ];
                arr.item = (i) => arr[i] || null;
                arr.namedItem = (name) => arr.find(p => p.name === name) || null;
                arr.refresh = () => {};
                return arr;
            }
        });
        
        // 伪造 languages
        Object.defineProperty(navigator, 'languages', { get: () => ['en-ZA', 'en', 'en-US'] });
        
        // 伪造 platform
        Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
        
        // 伪造 hardwareConcurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        
        // 伪造 chrome 对象（Firefox 中通常不存在）
        Object.defineProperty(window, 'chrome', {
            get: () => ({ runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} }),
        });
    """)

    return browser, page


async def _launch_chromium(p):
    """启动 Chromium 浏览器，附带完整反检测配置（回退方案）"""
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-setuid-sandbox",
            "--disable-gpu",
        ],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        locale="en-ZA",
        timezone_id="Africa/Johannesburg",
        device_scale_factor=2,
        is_mobile=False,
        has_touch=False,
        color_scheme="light",
    )
    page = await context.new_page()

    # 注入 playwright-stealth
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
    except ImportError:
        pass

    # 补充反检测 JS
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = [1, 2, 3, 4, 5];
                plugins.item = (i) => plugins[i];
                plugins.namedItem = (name) => null;
                plugins.refresh = () => {};
                return plugins;
            }
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-ZA', 'en', 'en-US'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (params) => (
            params.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            origQuery(params)
        );
    """)

    return browser, page


async def _extract_data(page, url: str, normalized_url: str) -> Dict[str, Any]:
    """从加载完成的页面中提取商品数据"""
    result = {
        "normalized_url": normalized_url,
        "tsin": None,
        "product_name": None,
        "product_image_url": None,
        "actual_sale_price_zar": None,
        "in_stock_price": None,
        "takealot_category_path": None,
    }

    # 1. 从页面标题提取产品名称
    title = await page.title()
    if title and "takealot" in title.lower():
        parts = title.split("|")
        if len(parts) >= 1:
            name = parts[0].strip()
            if name:
                result["product_name"] = name

    # 2. 从 meta description 补充产品名称
    if not result["product_name"]:
        try:
            desc = await page.evaluate(
                '() => document.querySelector("meta[name=\'description\']")?.getAttribute("content") || null'
            )
            if desc and 10 < len(desc) < 300:
                result["product_name"] = desc.split(".")[0].strip()
        except Exception:
            pass

    # 3. 提取产品图片
    try:
        img_url = await page.evaluate("""
            () => {
                const img = document.querySelector('img[src*="media.takealot.com/covers_images"]');
                if (img) return img.src.replace('s-thumbnail', 's-pdpxl');
                const og = document.querySelector('meta[property="og:image"]');
                return og ? og.getAttribute('content') : null;
            }
        """)
        if img_url:
            result["product_image_url"] = img_url
    except Exception:
        pass

    # 4. 从 URL 提取 TSIN (PLID)
    try:
        plid_match = re.search(r'PLID(\d+)', url, re.IGNORECASE)
        if plid_match:
            result["tsin"] = f"PLID{plid_match.group(1)}"
    except Exception:
        pass

    # 5. 提取分类路径
    try:
        breadcrumbs = await page.evaluate("""
            () => {
                const pdp = document.querySelector('.pdp');
                if (!pdp) return null;
                const links = pdp.querySelectorAll('a[href^="/"]');
                const categories = [];
                const seen = new Set();
                const keywords = [
                    'computers', 'electronics', 'home', 'kitchen', 'sport',
                    'fashion', 'baby', 'toys', 'garden', 'automotive',
                    'camping', 'beauty', 'health', 'office', 'books',
                    'appliances', 'gaming', 'music', 'pets', 'liquor',
                    'stationery', 'luggage', 'data storage', 'drives',
                    'storage', 'computer components', 'tv', 'audio',
                    'cameras', 'musical instruments', 'diy',
                ];
                for (const link of links) {
                    const text = link.textContent.trim();
                    const href = link.getAttribute('href');
                    if (!text || !href || text.length > 50) continue;
                    if (keywords.some(kw => text.toLowerCase().includes(kw)) && !seen.has(text)) {
                        seen.add(text);
                        categories.push(text);
                    }
                }
                return categories.length > 0 ? categories.join(' > ') : null;
            }
        """)
        if breadcrumbs:
            result["takealot_category_path"] = breadcrumbs
    except Exception:
        pass

    # 6. 提取售价
    try:
        price_text = await page.evaluate("""
            () => {
                const sel = document.querySelector('[class*="price-buybox"]');
                if (sel) return sel.textContent.trim();
                return null;
            }
        """)
        if price_text:
            price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(" ", ""))
            if price_match:
                try:
                    result["actual_sale_price_zar"] = float(
                        price_match.group(0).replace(",", "")
                    )
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    # 7. 提取有现货标识的最低售价
    try:
        in_stock_prices = await page.evaluate("""
            () => {
                const prices = [];
                const deliveryPattern = /get it (today|tomorrow|by \\d+|in \\d+)/gi;
                
                const sellerRows = document.querySelectorAll('[class*="seller"]');
                sellerRows.forEach(row => {
                    const text = row.textContent || '';
                    if (deliveryPattern.test(text)) {
                        const priceMatch = text.match(/R\\s*([\\d,]+(?:\\.\\d{2})?)/i);
                        if (priceMatch) {
                            prices.push(parseFloat(priceMatch[1].replace(/,/g, '')));
                        }
                    }
                });
                
                if (prices.length === 0) {
                    const allElements = document.querySelectorAll('[class*="price"], [class*="price-module"], [class*="buybox"]');
                    allElements.forEach(el => {
                        const nearby = el.closest('[class*="seller"], div[class*="card"], div[class*="panel"], div[class*="module"]');
                        const container = nearby ? nearby.textContent : '';
                        const parentText = el.parentElement ? el.parentElement.textContent : '';
                        const combinedText = container + ' ' + parentText;
                        if (deliveryPattern.test(combinedText)) {
                            const priceText = el.textContent || '';
                            const priceMatch = priceText.match(/R\\s*([\\d,]+(?:\\.\\d{2})?)/i);
                            if (priceMatch) {
                                prices.push(parseFloat(priceMatch[1].replace(/,/g, '')));
                            }
                        }
                    });
                }
                
                return prices.length > 0 ? prices : null;
            }
        """)
        if in_stock_prices and len(in_stock_prices) > 0:
            result["in_stock_price"] = min(in_stock_prices)
        elif result["actual_sale_price_zar"] is not None:
            result["in_stock_price"] = result["actual_sale_price_zar"]
    except Exception:
        if result["actual_sale_price_zar"] is not None:
            result["in_stock_price"] = result["actual_sale_price_zar"]

    return result


async def _try_scrape_with_browser(p, launcher_name: str, launcher, url: str, normalized_url: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """用指定浏览器尝试抓取，返回 (结果, 错误信息)"""
    browser = None
    try:
        browser, page = await launcher(p)

        await page.goto(normalized_url, timeout=60000, wait_until="networkidle")

        title = await page.title()
        if "Just a moment" in title or title == "":
            await page.wait_for_timeout(8000)
            title = await page.title()

        if "Just a moment" in title:
            return None, f"Cloudflare blocked {launcher_name}"

        data = await _extract_data(page, url, normalized_url)
        return data, ""

    except Exception as e:
        return None, f"{launcher_name} error: {str(e)}"
    finally:
        if browser is not None:
            await browser.close()


async def scrape_product(url: str) -> Dict[str, Any]:
    """
    抓取 Takealot 商品页面,返回结构化数据。
    策略: Firefox -> Chromium+stealth -> 报错
    """
    result = {
        "normalized_url": normalize_takealot_url(url),
        "tsin": None,
        "product_name": None,
        "product_image_url": None,
        "actual_sale_price_zar": None,
        "in_stock_price": None,
        "takealot_category_path": None,
        "warnings": [],
        "success": False,
    }

    if not validate_takealot_url(url):
        result["warnings"].append("URL 不属于 takealot.com 域名")
        return result

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        result["warnings"].append("Playwright 未安装,无法执行页面抓取")
        return result

    normalized_url = result["normalized_url"]
    errors = []

    async with async_playwright() as p:
        # 策略 1: Firefox
        data, err = await _try_scrape_with_browser(
            p, "Firefox", _launch_firefox, url, normalized_url
        )
        if data is not None:
            result.update(data)
            result["success"] = True
            return result
        if err:
            errors.append(err)

        # 策略 2: Chromium + stealth
        data, err = await _try_scrape_with_browser(
            p, "Chromium", _launch_chromium, url, normalized_url
        )
        if data is not None:
            result.update(data)
            result["success"] = True
            return result
        if err:
            errors.append(err)

    # 所有策略均失败
    result["warnings"].append("Cloudflare 安全验证未通过,请稍后重试")
    return result
