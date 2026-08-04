"""
Takealot 商品信息抓取服务
使用 Playwright + stealth 渲染页面,按优先级提取结构化数据。
"""

import re
import json
from typing import Optional, Dict, Any
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


async def _launch_browser(p):
    """启动带反检测配置的浏览器"""
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
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
    )
    page = await context.new_page()

    # 注入 stealth 风格的 JS,隐藏 webdriver 标记
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-ZA', 'en'] });
        window.chrome = { runtime: {} };
    """)

    return browser, page


async def scrape_product(url: str) -> Dict[str, Any]:
    """
    抓取 Takealot 商品页面,返回结构化数据。
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

    async with async_playwright() as p:
        browser, page = await _launch_browser(p)

        try:
            # 使用 networkidle 等待,确保动态内容加载完毕
            await page.goto(result["normalized_url"], timeout=60000, wait_until="networkidle")

            # 检测是否被 Cloudflare 拦截
            title = await page.title()
            if "Just a moment" in title or title == "":
                # 等待 Cloudflare 验证完成
                await page.wait_for_timeout(8000)
                title = await page.title()

            if "Just a moment" in title:
                result["warnings"].append("Cloudflare 安全验证未通过,请稍后重试")
                return result

            # 1. 从页面标题提取产品名称
            if title and "takealot.com" in title.lower():
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

            # 5. 提取分类路径(PDP 区域内的分类导航链接)
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

            # 6. 提取售价（优先获取 buybox 价格）
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

            # 7. 提取有现货标识的最低售价 (in_stock_price)
            # 从卖家列表中筛选 "get it today" / "get it tomorrow" 等有现货的价格，取最低值
            try:
                in_stock_prices = await page.evaluate("""
                    () => {
                        const prices = [];
                        
                        // 方法1: 查找所有包含 "get it today" 或 "get it tomorrow" 的卖家行
                        const allText = document.body.innerText || '';
                        const deliveryPattern = /get it (today|tomorrow|by \\d+|in \\d+)/gi;
                        
                        // 方法2: 查找 buybox 区域内的 seller-row 价格
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
                        
                        // 方法3: 如果上面的方法没找到，查找所有 price 元素附近的 delivery 信息
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
                    # 如果没有找到有货标识，回退到 buybox 价格
                    result["in_stock_price"] = result["actual_sale_price_zar"]
            except Exception:
                # in_stock_price 回退到 actual_sale_price_zar
                if result["actual_sale_price_zar"] is not None:
                    result["in_stock_price"] = result["actual_sale_price_zar"]

            result["success"] = True

        except Exception as e:
            result["warnings"].append(f"页面抓取失败: {str(e)}")
        finally:
            await browser.close()

    return result
