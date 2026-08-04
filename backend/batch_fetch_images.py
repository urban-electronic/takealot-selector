"""
批量抓取 Takealot 产品图片（优化版：复用浏览器实例）
"""

import sys
import asyncio
import re

sys.path.insert(0, '.')

from database import SessionLocal
from models import Product
from services.takealot_scraper import normalize_takealot_url, validate_takealot_url


async def scrape_single(page, url: str) -> dict:
    """单个产品抓取，复用 page"""
    result = {
        "normalized_url": normalize_takealot_url(url),
        "product_image_url": None,
    }
    if not validate_takealot_url(url):
        return result

    try:
        await page.goto(result["normalized_url"], timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        title = await page.title()
        if "Just a moment" in title:
            await page.wait_for_timeout(5000)

        # 提取图片
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
    except Exception:
        pass

    return result


async def main():
    from playwright.async_api import async_playwright

    db = SessionLocal()
    try:
        products = db.query(Product).filter(
            (Product.product_image_url == None) | (Product.product_image_url == '')
        ).all()

        print(f"需要抓取图片的产品数: {len(products)}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            success = 0
            failed = 0
            no_url = 0

            for i, prod in enumerate(products):
                if not prod.takealot_url:
                    no_url += 1
                    failed += 1
                    continue

                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1440, "height": 900},
                    locale="en-ZA",
                )
                page = await context.new_page()
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-ZA', 'en'] });
                    window.chrome = { runtime: {} };
                """)

                try:
                    result = await scrape_single(page, prod.takealot_url)
                    if result and result.get('product_image_url'):
                        prod.product_image_url = result['product_image_url']
                        success += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1

                await context.close()

                if (i + 1) % 5 == 0:
                    db.commit()
                    print(f"进度: {i+1}/{len(products)}, 成功: {success}, 失败: {failed}, 无URL: {no_url}")

                await asyncio.sleep(1)

            await browser.close()
            db.commit()
            print(f"\n=== 完成 ===")
            print(f"总数: {len(products)}")
            print(f"成功: {success}")
            print(f"失败: {failed} (其中无 takealot_url: {no_url})")
    finally:
        db.close()


if __name__ == '__main__':
    asyncio.run(main())
