# -*- coding: utf-8 -*-
"""
本地装箱单数据迁移导出工具
=========================

读取本地版 kunjia_analyze 的 products.sqlite3 与 images/ 目录，
输出云端可导入的迁移包：

    out/
      packing_migration.json   # 产品列表（14 字段，兼容云端 packing_products）
      images/<sha256>.<ext>    # 产品图片（按内容哈希命名，与云端策略一致）

用法：
    python tools/export_local_packing.py \
        --db   <kunjia_analyze>/products.sqlite3 \
        --images <kunjia_analyze>/images \
        --out  <迁移包输出目录>

未传参时使用默认路径（参考仓库同级 kunjia_analyze）。重复执行会跳过已存在的
目标文件（幂等），不会覆盖云端或本地数据。
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO.parent / 'kunjia_analyze' / 'products.sqlite3'
DEFAULT_IMAGES = REPO.parent / 'kunjia_analyze' / 'images'

FIELDS = [
    'sku', 'name', 'name_zh', 'name_en', 'unit', 'weight', 'material', 'brand',
    'battery', 'electric', 'magnetic', 'template_row', 'default_count', 'image_file',
]


def sha256_copy(src: Path, dst_dir: Path) -> str:
    """按内容 SHA256 复制图片到 dst_dir，返回文件名；文件已存在则跳过。"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    content = src.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    ext = src.suffix.lower()
    if ext not in ('.png', '.jpg', '.jpeg'):
        ext = '.jpg' if content.startswith(b'\xff\xd8\xff') else '.png' if content.startswith(b'\x89PNG') else ''
    if not ext:
        return ''
    filename = digest + ('.jpg' if ext == '.jpeg' else ext)
    target = dst_dir / filename
    if not target.exists():
        target.write_bytes(content)
    return filename


def main() -> int:
    parser = argparse.ArgumentParser(description='导出本地装箱单迁移包')
    parser.add_argument('--db', default=str(DEFAULT_DB), help='本地 products.sqlite3 路径')
    parser.add_argument('--images', default=str(DEFAULT_IMAGES), help='本地 images 目录')
    parser.add_argument('--out', required=True, help='迁移包输出目录（需为空或不存在）')
    parser.add_argument('--force', action='store_true', help='目标已存在 packing_migration.json 时强制重建')
    args = parser.parse_args()

    db_path = Path(args.db)
    images_dir = Path(args.images)
    out_dir = Path(args.out)
    if not db_path.is_file():
        print(f'找不到本地数据库：{db_path}')
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / 'packing_migration.json'
    if manifest.exists() and not args.force:
        print(f'目标目录已有 packing_migration.json：{out_dir}')
        print('（已导入过？请换目录或加 --force 重建）')
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM products ORDER BY sku').fetchall()
    conn.close()

    products, images_out = [], 0
    for row in rows:
        item = {}
        for k in FIELDS:
            v = row[k]
            item[k] = v if v is not None else (None if k == 'template_row' else '')
        sku = str(item.get('sku') or '').strip()
        if not sku or str(item.get('name_zh') or '') == '' and str(item.get('name_en') or '') == '':
            continue
        old_image = str(item.get('image_file') or '')
        if old_image:
            src = images_dir / old_image if images_dir.joinpath(old_image).is_file() else None
            if src is None:
                # 兼容 image_file 只有文件名而实际存在的情况
                src = images_dir / Path(old_image).name
            if src.is_file():
                new_name = sha256_copy(src, out_dir / 'images')
                if new_name:
                    item['image_file'] = new_name
                    images_out += 1
                else:
                    item['image_file'] = ''
            else:
                item['image_file'] = ''
        products.append(item)

    manifest.write_text(
        json.dumps(products, ensure_ascii=False, indent=1),
        encoding='utf-8',
    )
    print(f'迁移包已生成：{out_dir}')
    print(f'  产品数：{len(products)}')
    print(f'  图片数：{images_out}')
    print(f'  manifest：{manifest}')
    print('导入云端：curl -X POST -H "X-API-Key: <key>" -F "file=@<打包后的 zip>" http://<host>/api/packing/import')
    return 0


if __name__ == '__main__':
    sys.exit(main())
