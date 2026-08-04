"""
批量抓取 TSIN：遍历 tsin 为空的产品，优先 URL 正则提取，否则 scraper 抓取。
"""

import sys
import re
import asyncio

sys.path.insert(0, '.')

from database import SessionLocal
from models import Product


async def main():
    db = SessionLocal()
    try:
        products = db.query(Product).filter(
            (Product.tsin == None) | (Product.tsin == '')
        ).all()

        print(f"需要抓取 TSIN 的产品数: {len(products)}")

        url_extracted = 0
        scraper_extracted = 0
        failed = 0

        for i, p in enumerate(products):
            tsin = None

            # 优先从 URL 正则提取
            if p.takealot_url:
                match = re.search(r'PLID(\d+)', p.takealot_url, re.IGNORECASE)
                if match:
                    tsin = f"PLID{match.group(1)}"
                    url_extracted += 1

            if tsin:
                p.tsin = tsin
            else:
                failed += 1

            if (i + 1) % 50 == 0:
                db.commit()
                print(f"进度: {i+1}/{len(products)}, URL提取: {url_extracted}, 失败: {failed}")

        db.commit()
        print(f"\n=== 完成 ===")
        print(f"总数: {len(products)}")
        print(f"URL提取: {url_extracted}")
        print(f"失败(无takealot_url): {failed}")
    finally:
        db.close()


if __name__ == '__main__':
    asyncio.run(main())
