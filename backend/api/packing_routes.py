# -*- coding: utf-8 -*-
"""
云端装箱单 API 路由
=================

对齐本地版 kunjia_analyze（server.py + app.py）的接口语义，共 10 个接口：

1.  GET    /api/packing/products            产品列表（按 sku 排序）
2.  POST   /api/packing/products            批量 UPSERT（按 sku 幂等）
3.  POST   /api/packing/product             单产品 UPSERT（支持 old_sku 改名）
4.  POST   /api/packing/delete              删除产品
5.  POST   /api/packing/image               base64 上传图片（SHA256 命名 + 魔数校验）/ 移除
6.  GET    /api/packing/images/{filename}   图片访问
7.  POST   /api/packing/export              生成装箱单 Excel（DISPIMG 图片）
8.  POST   /api/packing/import              迁移导入（zip：packing_migration.json + images，幂等）
9.  GET    /api/packing/backup              全量备份 zip（JSON + 图片）
10. GET    /packing-ui                      远程版前端静态页

鉴权：复用 main.py 的 X-API-Key 中间件（所有 /api 前缀接口），无需重复实现。
"""

import base64
import datetime as dt
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import PackingProduct
from services import packing_excel as px

router = APIRouter(prefix="/api/packing", tags=["packing"])

SKU_RE = re.compile(r'[A-Za-z0-9_-]{1,50}')
PUBLIC_FIELDS = (
    'sku', 'name_zh', 'name_en', 'unit', 'weight', 'material', 'brand',
    'battery', 'electric', 'magnetic', 'default_count', 'image_file', 'template_row',
)


# ---- Pydantic Schemas ----

class ProductIn(BaseModel):
    sku: str = ''
    name_zh: str = ''
    name_en: str = ''
    unit: str = ''
    weight: str = ''
    material: str = ''
    brand: str = ''
    battery: str = ''
    electric: str = ''
    magnetic: str = ''
    template_row: Optional[int] = None
    default_count: Optional[int] = None
    old_sku: Optional[str] = None


class BatchIn(BaseModel):
    products: List[ProductIn] = []


class DeleteIn(BaseModel):
    sku: str = ''


class ImageIn(BaseModel):
    sku: str = ''
    data: str = ''
    remove: bool = False


class ExportLine(BaseModel):
    sku: str = ''
    cartons: str = '1'
    count: str = '1'


class ExportIn(BaseModel):
    date: str = ''
    mark: str = 'KPLDJ'
    shipping: str = '空'
    address: str = '找客服提供'
    items: List[ExportLine] = []


# ---- Helpers ----

def public(p: PackingProduct) -> dict:
    return {k: getattr(p, k) for k in PUBLIC_FIELDS}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={'error': msg})


def _find(db: Session, sku: str) -> Optional[PackingProduct]:
    return db.query(PackingProduct).filter(PackingProduct.sku == sku).first()


def _apply_values(p: PackingProduct, data: ProductIn, old: Optional[PackingProduct]) -> PackingProduct:
    """对齐本地 server.py /api/product：自动推断 electric/magnetic/material。"""
    val = {k: str(getattr(data, k) or '').strip() for k in
           ('sku', 'name_zh', 'name_en', 'unit', 'weight', 'material', 'brand', 'battery', 'electric', 'magnetic')}
    if not SKU_RE.fullmatch(val['sku']) or not (val['name_zh'] or val['name_en']):
        raise ValueError('请填写有效 SKU 和产品名称')
    inferred_electric, inferred_magnetic = px.infer_flags(val['name_zh'], val['name_en'])
    val['electric'] = val['electric'] or inferred_electric
    val['magnetic'] = val['magnetic'] or inferred_magnetic
    val['material'] = val['material'] or px.infer_material(val['name_zh'], val['name_en'])
    p.sku = val['sku']
    p.name = ' / '.join(x for x in (val['name_zh'], val['name_en']) if x)
    p.name_zh = val['name_zh']
    p.name_en = val['name_en']
    p.unit = val['unit']
    p.weight = val['weight']
    p.material = val['material']
    p.brand = val['brand']
    p.battery = val['battery']
    p.electric = val['electric']
    p.magnetic = val['magnetic']
    p.template_row = data.template_row if data.template_row is not None else (old.template_row if old else None)
    p.default_count = data.default_count if data.default_count is not None else (old.default_count if old else 1)
    p.image_file = old.image_file if old else ''
    return p


def _upsert(db: Session, data: ProductIn) -> PackingProduct:
    old_sku = (data.old_sku or '').strip() or data.sku
    old = _find(db, old_sku)
    if data.sku != old_sku and _find(db, data.sku):
        raise ValueError('SKU 已存在')
    if old and old_sku != data.sku:
        db.delete(old)
        db.flush()
        old = None
    p = _find(db, data.sku)
    if p is None:
        p = PackingProduct(sku=data.sku)
        db.add(p)
    _apply_values(p, data, old)
    return p


def _parse_migration_zip(content: bytes) -> tuple:
    """解析迁移包 zip（packing_migration.json + images/*），返回 (products, images)"""
    products = []
    images = {}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = z.namelist()
        if 'packing_migration.json' not in names:
            raise ValueError('迁移包缺少 packing_migration.json')
        products = json.loads(z.read('packing_migration.json').decode('utf-8'))
        if not isinstance(products, list):
            raise ValueError('packing_migration.json 应为产品数组')
        for n in names:
            if n.startswith('images/') and not n.endswith('/'):
                images[Path(n).name] = z.read(n)
    return products, images


# ---- 1. GET /api/packing/products ----

@router.get('/products')
def list_products(db: Session = Depends(get_db)):
    rows = db.query(PackingProduct).order_by(PackingProduct.sku).all()
    return [public(p) for p in rows]


# ---- 2. POST /api/packing/products（批量 UPSERT） ----

@router.post('/products')
def upsert_products(data: BatchIn, db: Session = Depends(get_db)):
    count = 0
    for item in data.products:
        try:
            _upsert(db, item)
            count += 1
        except ValueError as e:
            return fail(400, f'第 {count + 1} 条失败：{e}')
    db.commit()
    return {'ok': True, 'saved': count}


# ---- 3. POST /api/packing/product ----

@router.post('/product')
def upsert_product(data: ProductIn, db: Session = Depends(get_db)):
    try:
        _upsert(db, data)
    except ValueError as e:
        return fail(400, str(e))
    db.commit()
    return {'ok': True}


# ---- 4. POST /api/packing/delete ----

@router.post('/delete')
def delete_product(data: DeleteIn, db: Session = Depends(get_db)):
    p = _find(db, data.sku.strip())
    if p:
        db.delete(p)
        db.commit()
    return {'ok': True}


# ---- 5. POST /api/packing/image ----

@router.post('/image')
def upload_image(data: ImageIn, db: Session = Depends(get_db)):
    sku = data.sku.strip()
    p = _find(db, sku)
    if not p:
        return fail(400, '产品不存在')
    if data.remove:
        p.image_file = ''
        db.commit()
        return {'ok': True}
    try:
        blob = base64.b64decode(data.data, validate=True)
    except Exception:
        return fail(400, '图片数据不是有效的 base64')
    if len(blob) > 20 * 1024 * 1024:
        return fail(400, '图片不能超过 20 MB')
    try:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(blob)
            tmp = f.name
        try:
            filename = px.add_image(sku, tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)
    except ValueError as e:
        return fail(400, str(e))
    p.image_file = filename
    db.commit()
    return {'ok': True, 'filename': filename}


# ---- 6. GET /api/packing/images/{filename} ----

@router.get('/images/{filename}')
def get_image(filename: str):
    if Path(filename).name != filename:
        return fail(400, 'invalid path')
    image = px.IMAGE_DIR / filename
    if not image.is_file():
        return fail(404, 'missing image')
    try:
        px.check_image(image)
    except Exception:
        return fail(400, 'invalid image')
    return FileResponse(str(image), media_type='image/png' if image.suffix.lower() == '.png' else 'image/jpeg')


# ---- 7. POST /api/packing/export ----

@router.post('/export')
def export_excel(data: ExportIn, db: Session = Depends(get_db)):
    if not data.items or len(data.items) > 500:
        return fail(400, '请选择 1 至 500 个产品')
    try:
        date = dt.date.fromisoformat(data.date.strip())
    except ValueError:
        return fail(400, '请填写 YYYY-MM-DD 格式的有效日期')
    items = []
    for line in data.items:
        p = _find(db, line.sku.strip())
        if not p:
            return fail(400, '找不到产品 ' + line.sku)
        cartons = line.cartons.strip()
        count = line.count.strip()
        if not cartons.isdecimal() or not count.isdecimal() or int(cartons) < 1 or int(count) < 1:
            return fail(400, '箱数和每箱件数须为正整数')
        items.append((public(p), cartons, count))
    try:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / 'list.xlsx'
            px.generate(items, date.isoformat(), data.mark, data.shipping, data.address, output)
            content = output.read_bytes()
    except FileNotFoundError as e:
        return fail(400, str(e))
    except Exception as e:
        return fail(400, f'生成失败：{e}')
    return Response(
        content=content,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="packing_{date.isoformat()}.xlsx"'},
    )


# ---- 8. POST /api/packing/import ----

@router.post('/import')
async def import_migration(file: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    """迁移导入，幂等：
    - 支持上传 backup 产出的 zip（packing_migration.json + images/*）
    - 产品按 sku INSERT OR IGNORE：已存在的 SKU 跳过，不覆盖云端已有编辑
    - 图片按 SHA256 文件名写入 packing_images，同名跳过
    """
    if file is None:
        return fail(400, '请上传迁移包 zip')
    try:
        content = await file.read()
        if len(content) > 200 * 1024 * 1024:
            return fail(400, '迁移包不能超过 200 MB')
        products, images = _parse_migration_zip(content)
    except Exception as e:
        return fail(400, f'迁移包解析失败：{e}')

    px.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    imported = skipped = 0
    for row in products:
        sku = str(row.get('sku') or '').strip()
        if not sku:
            continue
        if _find(db, sku):
            skipped += 1
            continue
        p = PackingProduct(sku=sku)
        for k in ('name', 'name_zh', 'name_en', 'unit', 'weight', 'material',
                  'brand', 'battery', 'electric', 'magnetic', 'image_file'):
            v = row.get(k)
            setattr(p, k, str(v) if v is not None else '')
        try:
            p.template_row = int(row['template_row']) if row.get('template_row') is not None else None
        except (TypeError, ValueError):
            p.template_row = None
        try:
            p.default_count = int(row.get('default_count') or 1)
        except (TypeError, ValueError):
            p.default_count = 1
        if not p.name:
            p.name = ' / '.join(x for x in (p.name_zh, p.name_en) if x)
        db.add(p)
        imported += 1

    images_written = 0
    for filename, content_bytes in images.items():
        if Path(filename).name != filename:
            continue
        target = px.IMAGE_DIR / filename
        if target.exists():
            continue
        # 写入临时文件做魔数校验，通过后按 SHA256 文件名落盘
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(content_bytes)
            tmp = f.name
        try:
            ext = px.check_image(tmp)
        except ValueError:
            Path(tmp).unlink(missing_ok=True)
            continue
        if not filename.endswith('.' + ext):
            Path(tmp).unlink(missing_ok=True)
            continue
        target.write_bytes(content_bytes)
        Path(tmp).unlink(missing_ok=True)
        images_written += 1

    db.commit()
    return {'ok': True, 'imported': imported, 'skipped': skipped, 'images_written': images_written}


# ---- 9. GET /api/packing/backup ----

@router.get('/backup')
def backup(db: Session = Depends(get_db)):
    products = [public(p) for p in db.query(PackingProduct).order_by(PackingProduct.sku).all()]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('packing_migration.json', json.dumps(products, ensure_ascii=False, indent=1))
        seen = set()
        for p in products:
            fn = p.get('image_file') or ''
            if fn and fn not in seen:
                seen.add(fn)
                image = px.IMAGE_DIR / fn
                if image.is_file():
                    z.writestr('images/' + fn, image.read_bytes())
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="packing_backup.zip"'},
    )


# ---- 10. GET /packing-ui（静态托管远程版前端） ----

ui_router = APIRouter(tags=["packing-ui"])

_PACKING_UI = Path(__file__).resolve().parent.parent / 'packing_ui' / 'index.html'


@ui_router.get('/packing-ui')
def packing_ui():
    if not _PACKING_UI.is_file():
        return fail(404, 'packing_ui/index.html 未部署')
    return FileResponse(str(_PACKING_UI), media_type='text/html; charset=utf-8')
