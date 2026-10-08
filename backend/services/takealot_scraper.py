"""
Takealot 商品信息抓取服务
使用 Playwright 渲染页面,按优先级提取结构化数据。
先尝试 Firefox（Cloudflare 检测较宽松），失败回退 Chromium+stealth。
"""

import os
import asyncio
import re
import json
import random
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, urlunparse
from bs4 import BeautifulSoup


def _variants_from_payload(payload: Any) -> list:
    """从页面 JSON 状态中保守提取 SKU/规格/图片；只返回同时带 SKU 的对象。"""
    found = {}
    def walk(value: Any):
        if isinstance(value, dict):
            sku = next((value.get(k) for k in ('sku', 'SKU', 'seller_sku', 'sellerSku', 'barcode', 'ean') if value.get(k)), None)
            if sku:
                sku = str(sku).strip()
                label = next((value.get(k) for k in ('colour', 'color', 'size', 'variation', 'variant', 'value', 'title', 'name') if isinstance(value.get(k), (str, int, float))), '')
                image = next((value.get(k) for k in ('image_url', 'imageUrl', 'image', 'thumbnail', 'src') if isinstance(value.get(k), str) and 'http' in value.get(k)), '')
                images = value.get('images')
                if not image and isinstance(images, list) and images:
                    first = images[0]
                    image = first if isinstance(first, str) else (first.get('url') or first.get('src') or '') if isinstance(first, dict) else ''
                if image and 'media.takealot.com' in image:
                    image = image.replace('s-thumbnail', 's-pdpxl')
                if sku and (label or image):
                    found[sku] = {'sku': sku, 'label': str(label).strip(), 'image_url': image}
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    return list(found.values())


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
        host = (parsed.hostname or "").lower()
        return parsed.scheme in ("http", "https") and (host == "takealot.com" or host == "www.takealot.com") and parsed.username is None and parsed.password is None
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
        "competing_sellers_count": None,
        "stock_remaining": None,
        "review_count": None,
        "rating_value": None,
        "variants": [],
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

    # 3.5 从页面内嵌状态提取真实变体 SKU、颜色/容量/尺码和对应图片。
    try:
        scripts = await page.evaluate("""() => Array.from(document.querySelectorAll('script')).map(s => s.textContent || '').filter(t => t.trim().startsWith('{') || t.trim().startsWith('['))""")
        variants = []
        for script in scripts:
            try:
                variants.extend(_variants_from_payload(json.loads(script)))
            except Exception:
                continue
        result["variants"] = list({item['sku']: item for item in variants}.values())
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

    # 8. 提取竞品卖家数
    try:
        sellers_count = await page.evaluate("""
            () => {
                // 方法1: 从 .more-buying-choices-module_offer 文本中提取 "X offers"
                const offerEl = document.querySelector('[class*="more-buying-choices"]');
                if (offerEl) {
                    const text = offerEl.textContent || '';
                    const match = text.match(/(\\d+)\\s*offers?/i);
                    if (match) return parseInt(match[1], 10);
                }
                // 方法2: 计数 a[href*="/seller/"] 链接
                const sellerLinks = document.querySelectorAll('a[href*="/seller/"]');
                if (sellerLinks.length > 0) return sellerLinks.length;
                return null;
            }
        """)
        if sellers_count is not None:
            result["competing_sellers_count"] = sellers_count
    except Exception:
        pass

    # 9. 提取剩余库存
    try:
        stock_text = await page.evaluate("""
            () => {
                const aside = document.querySelector('aside') || document.body;
                const text = aside.textContent || '';
                const match = text.match(/Only\\s*(\\d+)\\s*left/i);
                return match ? parseInt(match[1], 10) : null;
            }
        """)
        if stock_text is not None:
            result["stock_remaining"] = stock_text
    except Exception:
        pass

    # 10. 提取评价数和评分
    try:
        rating_data = await page.evaluate("""
            () => {
                // 找评分链接 a[href*="Reviews"]
                const reviewLink = document.querySelector('a[href*="Reviews"]');
                let reviewCount = null;
                let ratingVal = null;
                
                if (reviewLink) {
                    const text = reviewLink.textContent || '';
                    // 提取评价数: "279 Reviews" 或 "(279)"
                    const countMatch = text.match(/(\\d+)\\s*Reviews?/i) || text.match(/\\((\\d+)\\)/);
                    if (countMatch) {
                        reviewCount = parseInt(countMatch[1], 10);
                    }
                }
                
                // 提取评分: 找包含数字.数字格式的元素
                const ratingEl = document.querySelector('[class*="rating"], [data-rating]');
                if (ratingEl) {
                    const text = ratingEl.textContent || ratingEl.getAttribute('data-rating') || '';
                    const ratingMatch = text.match(/(\\d+\\.?\\d*)/);
                    if (ratingMatch) {
                        ratingVal = parseFloat(ratingMatch[1]);
                    }
                }
                
                return { reviewCount, ratingVal };
            }
        """)
        if rating_data:
            if rating_data.get("reviewCount") is not None:
                result["review_count"] = rating_data["reviewCount"]
            if rating_data.get("ratingVal") is not None:
                result["rating_value"] = rating_data["ratingVal"]
    except Exception:
        pass

    soup = BeautifulSoup(await page.content(), "lxml")
    result.update(_page_product_fields(soup))
    result.update(_structured_product_fields(soup))
    return result


async def _try_scrape_with_browser(p, launcher_name: str, launcher, url: str, normalized_url: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """用指定浏览器尝试抓取，返回 (结果, 错误信息)"""
    browser = None
    try:
        browser, page = await launcher(p)

        await page.goto(normalized_url, timeout=25000, wait_until="domcontentloaded")
        title = await page.title()
        if "Just a moment" in title or title == "":
            await page.wait_for_timeout(8000)
            title = await page.title()

        if "Just a moment" in title:
            return None, f"Cloudflare blocked {launcher_name}"

        # 通用 JSON-LD（如 ViewAction）在空壳页面已存在，不能作为商品加载成功的信号。
        # 等待实际商品标题、主图和商品价格，而非广告价格、分期价格或脚本标签。
        try:
            await page.wait_for_function(PRODUCT_READY_JS, timeout=45000)
        except Exception:
            # 商品可能下架或只有部分字段；仍尝试提取，而不是吞掉可用资料。
            pass

        data = await _extract_data(page, url, normalized_url)
        if not _has_product_data(data):
            return None, f"{launcher_name} returned no product data"
        return data, ""

    except Exception as e:
        return None, f"{launcher_name} error: {str(e)}"
    finally:
        if browser is not None:
            await browser.close()


def _scrape_with_curl_cffi(url: str, normalized_url: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """使用 curl_cffi 模拟浏览器 TLS 指纹抓取（最快方案）"""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None, "curl_cffi not installed"

    try:
        resp = curl_requests.get(
            normalized_url,
            impersonate="chrome124",
            timeout=30,
            proxies={"http": os.environ.get("TAKEALOT_PROXY", ""), "https": os.environ.get("TAKEALOT_PROXY", "")} if os.environ.get("TAKEALOT_PROXY") else None,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-ZA,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        )

        if resp.status_code >= 400:
            return None, f"curl_cffi HTTP {resp.status_code}"

        text = resp.text
        if "Just a moment" in text or "Checking your browser" in text:
            return None, "curl_cffi Cloudflare blocked"

        soup = BeautifulSoup(text, "lxml")

        data = {
            "normalized_url": normalized_url,
            "tsin": None,
            "product_name": None,
            "product_image_url": None,
            "actual_sale_price_zar": None,
            "in_stock_price": None,
            "takealot_category_path": None,
            "competing_sellers_count": None,
            "stock_remaining": None,
            "review_count": None,
            "rating_value": None,
            "variants": [],
        }

        # TSIN
        plid_match = re.search(r'PLID(\d+)', url, re.IGNORECASE)
        if plid_match:
            data["tsin"] = f"PLID{plid_match.group(1)}"

        # 产品名称: <title>
        title_tag = soup.find("title")
        if title_tag and "takealot" in title_tag.text.lower():
            parts = title_tag.text.split("|")
            if parts:
                data["product_name"] = parts[0].strip()

        # 产品图片
        img = soup.select_one('img[src*="media.takealot.com/covers_images"]')
        if img and img.get("src"):
            data["product_image_url"] = img["src"].replace("s-thumbnail", "s-pdpxl")

        variants = []
        for script in soup.find_all('script'):
            text_value = script.string or script.get_text() or ''
            if not text_value.strip().startswith(('{', '[')):
                continue
            try:
                variants.extend(_variants_from_payload(json.loads(text_value)))
            except Exception:
                continue
        data["variants"] = list({item['sku']: item for item in variants}.values())

        # 售价: [class*="price-buybox"]
        price_el = soup.select_one('[class*="price-buybox"]')
        if price_el:
            price_match = re.search(r'[\d,]+\.?\d*', price_el.text.replace(" ", ""))
            if price_match:
                try:
                    data["actual_sale_price_zar"] = float(price_match.group(0).replace(",", ""))
                except (ValueError, TypeError):
                    pass

        # 分类路径
        pdp = soup.select_one(".pdp")
        if pdp:
            cats = []
            seen = set()
            keywords = [
                "computers", "electronics", "home", "kitchen", "sport",
                "fashion", "baby", "toys", "garden", "automotive",
                "camping", "beauty", "health", "office", "books",
                "appliances", "gaming", "music", "pets", "liquor",
                "stationery", "luggage", "data storage", "drives",
            ]
            for a in pdp.find_all("a", href=True):
                text = a.text.strip()
                if not text or len(text) > 50:
                    continue
                if any(kw in text.lower() for kw in keywords) and text not in seen:
                    seen.add(text)
                    cats.append(text)
            if cats:
                data["takealot_category_path"] = " > ".join(cats)

        # in_stock_price 回退到 buybox 价格
        if data["actual_sale_price_zar"] is not None:
            data["in_stock_price"] = data["actual_sale_price_zar"]

        # 竞品卖家数: 从 .more-buying-choices 文本提取 "X offers"
        offer_el = soup.select_one('[class*="more-buying-choices"]')
        if offer_el:
            offer_match = re.search(r'(\d+)\s*offers?', offer_el.text, re.IGNORECASE)
            if offer_match:
                data["competing_sellers_count"] = int(offer_match.group(1))
        # 备用: 计数 seller 链接
        if data["competing_sellers_count"] is None:
            seller_links = soup.select('a[href*="/seller/"]')
            if seller_links:
                data["competing_sellers_count"] = len(seller_links)

        # 剩余库存: aside 区域 "Only X left"
        aside = soup.select_one("aside")
        stock_src = aside.text if aside else soup.body.text if soup.body else ""
        stock_match = re.search(r'Only\s*(\d+)\s*left', stock_src, re.IGNORECASE)
        if stock_match:
            data["stock_remaining"] = int(stock_match.group(1))

        # 评价数与评分
        review_link = soup.select_one('a[href*="Reviews"]')
        if review_link:
            count_match = re.search(r'(\d+)\s*Reviews?', review_link.text, re.IGNORECASE) \
                or re.search(r'\((\d+)\)', review_link.text)
            if count_match:
                data["review_count"] = int(count_match.group(1))

        rating_el = soup.select_one('[class*="rating"], [data-rating]')
        if rating_el:
            rating_text = rating_el.get("data-rating", "") or rating_el.text or ""
            rating_match = re.search(r'(\d+\.?\d*)', rating_text)
            if rating_match:
                try:
                    data["rating_value"] = float(rating_match.group(1))
                except (ValueError, TypeError):
                    pass

        data.update(_page_product_fields(soup))
        data.update(_structured_product_fields(soup))
        return data, ""

    except Exception as e:
        return None, f"curl_cffi error: {str(e)}"


def _page_product_fields(soup) -> Dict[str, Any]:
    """从真实商品区域提取，不扫描推荐商品、广告、导航菜单或分期价格。"""
    main = soup.select_one('main')
    if not main:
        return {}
    fields = {}
    title = main.select_one('h1')
    if title and title.get_text(strip=True):
        fields['product_name'] = title.get_text(strip=True)
    image = main.select_one('img[src*="media.takealot.com/covers_images"]')
    if image and image.get('src'):
        fields['product_image_url'] = image['src'].replace('s-thumbnail', 's-pdpxl')
    price = main.select_one('[data-ref="price"] .currency, [class*="price-buybox"] .currency')
    if price:
        match = re.search(r'R\s*([\d,]+(?:\.\d+)?)', price.get_text(' ', strip=True))
        if match:
            fields['actual_sale_price_zar'] = float(match.group(1).replace(',', ''))
    for row in main.select('table tr'):
        cells = row.select('td')
        if len(cells) >= 2 and cells[0].get_text(strip=True).lower() == 'categories':
            categories = [a.get_text(' ', strip=True) for a in cells[1].select('a')]
            if categories:
                fields['takealot_category_path'] = ' > '.join(categories)
            break
    if re.search(r'\b(supplier out of stock|out of stock)\b', main.get_text(' ', strip=True), re.I):
        fields['in_stock_price'] = None
    return fields


def _structured_product_fields(soup) -> Dict[str, Any]:
    """支持页面布局变化后的 schema.org Product，忽略网站通用元数据。"""
    fields = {}
    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Product" in types:
                if isinstance(value.get("name"), str):
                    fields["product_name"] = value["name"].strip()
                image = value.get("image")
                if isinstance(image, list):
                    image = image[0] if image else None
                if isinstance(image, dict):
                    image = image.get("url")
                if isinstance(image, str) and image.startswith("https://"):
                    fields["product_image_url"] = image
                offers = value.get("offers", [])
                if isinstance(offers, dict):
                    offers = [offers]
                if isinstance(offers, list):
                    for offer in offers:
                        if not isinstance(offer, dict) or offer.get("priceCurrency") != "ZAR":
                            continue
                        try:
                            price = float(str(offer.get("price", offer.get("lowPrice"))).replace(",", ""))
                            if 0 < price < float("inf"):
                                fields["actual_sale_price_zar"] = price
                                break
                        except (ValueError, TypeError):
                            pass
            if "@graph" in value:
                walk(value["@graph"])
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.get_text()))
        except (ValueError, TypeError):
            pass
    return fields


def _has_product_data(data: Optional[Dict[str, Any]]) -> bool:
    name = (data or {}).get("product_name") or ""
    return bool(name and not name.lower().startswith(("takealot.com:", "just a moment", "access denied")))


PRODUCT_READY_JS = """() => {
    const main = document.querySelector('main');
    if (!main) return false;
    const title = main.querySelector('h1')?.textContent?.trim();
    const image = main.querySelector('img[src*="media.takealot.com/covers_images"]');
    const price = main.querySelector('[data-ref="price"] .currency, [class*="price-buybox"] .currency, [class*="price-buybox"]');
    return !!(title && image?.getAttribute('src') && /R\\s*[\\d,]+/.test(price?.textContent || ''));
}"""


def _catalog_fallback(url: str) -> Optional[Dict[str, Any]]:
    """只按 PLID 精确匹配卖家导出表；表中未提供的价格、图片不推断。"""
    from services.offer_catalog_backfill import load_offer_catalog, _plid
    try:
        group = load_offer_catalog().get(_plid(url))
    except (OSError, ValueError, KeyError):
        return None
    if not group:
        return None
    variants = group.get("variants", [])
    name = next((item.get("title") for item in variants if item.get("title")), None)
    if not name:
        return None
    return {"product_name": name, "tsin": group.get("tsin"),
            "variants": [{"sku": item.get("sku"), "label": item.get("title"), "image_url": ""}
                         for item in variants if item.get("sku")],
            "data_source": "offer_catalog"}


async def scrape_product(url: str) -> Dict[str, Any]:
    """
    抓取 Takealot 商品页面,返回结构化数据。
    策略: curl_cffi -> Firefox -> Chromium+stealth -> 报错
    """
    result = {
        "normalized_url": normalize_takealot_url(url),
        "tsin": None,
        "product_name": None,
        "product_image_url": None,
        "actual_sale_price_zar": None,
        "in_stock_price": None,
        "takealot_category_path": None,
        "competing_sellers_count": None,
        "stock_remaining": None,
        "review_count": None,
        "rating_value": None,
        "variants": [],
        "warnings": [],
        "success": False,
    }

    if not validate_takealot_url(url):
        result["warnings"].append("URL 不属于 takealot.com 域名")
        return result

    normalized_url = result["normalized_url"]
    plid_match = re.search(r'PLID(\d+)', url, re.IGNORECASE)
    if plid_match:
        result["tsin"] = f"PLID{plid_match.group(1)}"
    errors = []

    def retain(data):
        # 后续来源的空字段不覆盖已取得的真实字段。
        for key, value in data.items():
            if value is not None and value != [] and value != "":
                result[key] = value

    def complete():
        return (_has_product_data(result)
                and result.get("actual_sale_price_zar") is not None
                and bool(result.get("product_image_url")))

    def finish_official():
        result["success"] = True
        result["data_source"] = "takealot"
        catalog = _catalog_fallback(url)
        if catalog:
            if not result.get("variants"):
                result["variants"] = catalog["variants"]
            if catalog.get("tsin"):
                result["tsin"] = catalog["tsin"]
        return result

    # HTTP 的部分资料先保留；缺失价格或图片时继续渲染商品页。
    data, err = await asyncio.to_thread(_scrape_with_curl_cffi, url, normalized_url)
    if _has_product_data(data):
        retain(data)
        if complete():
            return finish_official()
    if err:
        errors.append(err)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        errors.append("Playwright unavailable")
    else:
        try:
            async with async_playwright() as p:
                for name, launcher in (("Firefox", _launch_firefox), ("Chromium", _launch_chromium)):
                    data, err = await _try_scrape_with_browser(p, name, launcher, url, normalized_url)
                    if _has_product_data(data):
                        retain(data)
                        if complete():
                            return finish_official()
                    if err:
                        errors.append(err)
        except Exception:
            errors.append("Browser runtime unavailable")

    # 卖家表只在官网各抓取步骤完成后补缺，不能提前截断官网渲染。
    catalog = _catalog_fallback(url)
    if _has_product_data(result):
        finish_official()
        missing = [label for key, label in (("actual_sale_price_zar", "当前售价"), ("product_image_url", "图片")) if result.get(key) is None]
        if missing:
            result["warnings"].append("已获取官网部分资料，但未获取" + "、".join(missing) + "。")
        return result
    if catalog:
        retain(catalog)
        result["warnings"].append("官网抓取未成功；已从卖家导出表补充标题、SKU 和 TSIN。当前售价与图片未获取。")
    else:
        blocked = any("Cloudflare" in error or "HTTP 403" in error for error in errors)
        result["warnings"].append("官网安全验证未通过。" if blocked else "官网未返回有效商品资料。")
    return result
