"""
批量翻译：遍历 chinese_product_name 为空但有 product_name 的产品，翻译并更新。
"""

import sys

sys.path.insert(0, '.')

from database import SessionLocal
from models import Product
from services.translator import translate_to_chinese_sync


def main():
    db = SessionLocal()
    try:
        products = db.query(Product).filter(
            (Product.chinese_product_name == None) | (Product.chinese_product_name == ''),
            Product.product_name != None,
            Product.product_name != ''
        ).all()

        print(f"需要翻译的产品数: {len(products)}")

        success = 0
        failed = 0

        for i, p in enumerate(products):
            try:
                translated = translate_to_chinese_sync(p.product_name, max_chars=10)
                if translated:
                    p.chinese_product_name = translated
                    success += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            if (i + 1) % 50 == 0:
                db.commit()
                print(f"进度: {i+1}/{len(products)}, 成功: {success}, 失败: {failed}")

        db.commit()
        print(f"\n=== 完成 ===")
        print(f"总数: {len(products)}")
        print(f"成功: {success}")
        print(f"失败: {failed}")
    finally:
        db.close()


if __name__ == '__main__':
    main()
