# -*- coding: utf-8 -*-
"""
云端装箱单 API 路由
=================

对齐本地版 kunjia_analyze（server.py + app.py）的接口语义，共 14 个接口：

1.  GET    /api/packing/products            产品列表（按 sku 排序，带选品库图片回退）
2.  POST   /api/packing/products            批量 UPSERT（按 sku 幂等）
3.  POST   /api/packing/product             单产品 UPSERT（支持 old_sku 改名，保留图片/模板行）
4.  POST   /api/packing/delete              删除产品
5.  POST   /api/packing/sync-from-products  选品库轻量同步（幂等、并发安全）
6.  POST   /api/packing/import-from-products 按产品 id 导入（抓图转存 SHA256）
7.  POST   /api/packing/sync-images-from-products 按 SKU 补图（只补空图）
8.  POST   /api/packing/sync-variants-from-takealot 官网结构化变体匹配
9.  POST   /api/packing/image               base64 上传图片（SHA256 命名 + 魔数校验）/ 移除
10. GET    /api/packing/images/{filename}   图片访问
11. POST   /api/packing/export              生成装箱单 Excel（DISPIMG 图片）+ 逐箱清单落库
12. GET    /api/packing/cartons             逐箱清单查询（按日期看当天装箱 / 按 SKU 反查在哪几箱）
13. POST   /api/packing/import              迁移导入（zip：packing_migration.json + images，幂等）
14. GET    /api/packing/backup              全量备份 zip（JSON + 图片）
15. GET    /packing-ui                      远程版前端静态页

鉴权：复用 main.py 的 X-API-Key 中间件（所有 /api 前缀接口），无需重复实现。
"""

import base64
import datetime as dt
import io
import json
import re
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import (
    PackingProduct, Product, Shipment, ShipmentAllocation, ShipmentCarton, ShipmentCartonItem,
    ShipmentLine, Store,
)
from api.inventory_routes import inventory_balance_for_sku
from api.store_routes import get_store_id
from services import packing_excel as px
from services.operation_log import record_operation

router = APIRouter(prefix="/api/packing", tags=["packing"])

SKU_RE = re.compile(r'[A-Za-z0-9_-]{1,50}')
PUBLIC_FIELDS = (
    'sku', 'name_zh', 'name_en', 'unit', 'weight', 'material', 'brand',
    'battery', 'electric', 'magnetic', 'default_count', 'image_file', 'template_row',
    'variant_group', 'variant_label', 'is_primary_variant',
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
    variant_group: str = ''
    variant_label: str = ''
    is_primary_variant: bool = True


class BatchIn(BaseModel):
    products: List[ProductIn] = []


class DeleteIn(BaseModel):
    sku: str = ''


class ImageIn(BaseModel):
    sku: str = ''
    data: str = ''
    remove: bool = False


class ImportFromProductsIn(BaseModel):
    product_ids: List[str] = []


class SyncImagesIn(BaseModel):
    limit: int = 100


class SyncVariantsIn(BaseModel):
    limit: int = 20


class ExportLine(BaseModel):
    sku: str = ''
    box_no: str = ''
    cartons: str = '1'
    count: str = '1'


class CartonItemIn(BaseModel):
    sku: str = ''
    quantity: str = '1'


class CartonIn(BaseModel):
    box_no: str = ''
    note: str = ''
    items: List[CartonItemIn] = []


class ExportIn(BaseModel):
    record_shipment: bool = False
    date: str = ''
    # 唛头留空时用 inbound_no，再没有才退回 KPLDJ。默认值不能直接写 KPLDJ：
    # 那样「留空」和「明确要 KPLDJ」就分不出来了，用户填的入库单号也就永远用不上。
    mark: str = ''
    shipping: str = '空'
    address: str = '找客服提供'
    # OMS 入库单号。装箱单模板里「客户唛头」和 A 列「箱唛」应当同一套编号，
    # 前端把入库单号一填，箱唛是 INH1501522-001、唛头是 INH1501522，两张纸能对上。
    inbound_no: str = ''
    items: List[ExportLine] = []
    draft_key: str = ''
    # 逐箱清单：不传时按「一箱一个 SKU」自动展开，传了就以传的为准（混装必须传）
    cartons: List[CartonIn] = []


# ---- Helpers ----

def public(p: PackingProduct) -> dict:
    return {k: getattr(p, k) for k in PUBLIC_FIELDS}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={'error': msg})


def _find(db: Session, store_id: str, sku: str) -> Optional[PackingProduct]:
    """按 (店铺, SKU) 定位装箱产品；不同店铺的同名 SKU 是两条独立记录。"""
    return (
        db.query(PackingProduct)
        .filter(PackingProduct.store_id == store_id, PackingProduct.sku == sku)
        .first()
    )


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


def _upsert(db: Session, store_id: str, data: ProductIn) -> PackingProduct:
    old_sku = (data.old_sku or '').strip() or data.sku
    old = _find(db, store_id, old_sku)
    if data.sku != old_sku and _find(db, store_id, data.sku):
        raise ValueError('SKU 已存在')
    if old and old_sku != data.sku:
        # 改名不是新建：图片、模板行、默认件数和变体归属都是人工维护的资料，
        # 旧实现删旧建新会把它们静默清空（远程装箱单页每次编辑都带 old_sku）。
        renamed = PackingProduct(
            store_id=store_id,
            sku=data.sku,
            variant_group=old.variant_group or '',
            variant_label=old.variant_label or '',
            is_primary_variant=old.is_primary_variant,
        )
        _apply_values(renamed, data, old)
        db.delete(old)
        db.flush()
        db.add(renamed)
        return renamed
    p = _find(db, store_id, data.sku)
    if p is None:
        p = PackingProduct(store_id=store_id, sku=data.sku)
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

def _catalog_images_by_sku(products):
    """A group cover identifies its first SKU only; exact SKU records take priority."""
    images = {}
    for product in products:
        skus = [x for x in re.split(r'[\s,，;；]+', (product.sku or '').strip()) if x]
        image_url = (product.product_image_url or '').strip()
        if skus and image_url:
            images.setdefault(skus[0], image_url)
    for product in products:
        skus = [x for x in re.split(r'[\s,，;；]+', (product.sku or '').strip()) if x]
        if len(skus) == 1 and product.product_image_url:
            images[skus[0]] = product.product_image_url.strip()
    return images

@router.get('/products')
def list_products(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    store_products = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False).all()
    store_skus = {sku for product in store_products for sku in re.split(r'[\s,，;；]+', (product.sku or '').strip()) if sku}
    rows = db.query(PackingProduct).filter(PackingProduct.store_id == store_id, PackingProduct.sku.in_(store_skus)).order_by(PackingProduct.sku).all() if store_skus else []
    rows.sort(key=lambda p: (p.variant_group or p.sku, 0 if p.is_primary_variant else 1, p.sku))
    source_images = _catalog_images_by_sku(store_products)
    result = []
    for p in rows:
        item = public(p)
        item['source_image_url'] = source_images.get(p.sku, '')
        result.append(item)
    return result


# ---- 1.5 POST /api/packing/sync-from-products ----

# 同步在装箱单页挂载、用户重试或双标签页下可能被并发触发：两个事务都通过
# 内存 seen 检查后重复 INSERT 同一 SKU，先提交者赢、后提交者触发
# UNIQUE constraint failed: packing_products.sku 500（F11，2026-10-15 C 域审计
# 实测复现）。同店同步串行执行，从根上消除该竞态。
_sync_locks: dict = {}
_sync_locks_guard = threading.Lock()


def _sync_lock_for(store_id: str) -> threading.Lock:
    with _sync_locks_guard:
        lock = _sync_locks.get(store_id)
        if lock is None:
            lock = threading.Lock()
            _sync_locks[store_id] = lock
        return lock


@router.post('/sync-from-products')
def sync_from_products(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """轻量同步完整产品库到装箱单库（幂等，可重复/并发调用）。

    只补充尚不存在的 SKU，不覆盖装箱单中的人工编辑；图片直接由列表接口
    回退到产品库图片，避免批量下载图片导致请求超时。

    并发安全：同店同步串行执行；多 worker 部署下跨进程并发若仍触发
    SKU UNIQUE 冲突，回滚后从最新快照有界重试；未完成不能假报已同步。
    """
    with _sync_lock_for(store_id):
        for attempt in range(3):
            try:
                return _sync_from_products(db, store_id)
            except IntegrityError as exc:
                db.rollback()
                msg = str(exc.orig).strip().lower()
                # 主键现在是 (store_id, sku)，SQLite 报错串会带上两列，
                # 用包含判断兼容单列/复合主键两种形态。
                if 'unique constraint failed' not in msg or 'packing_products' not in msg or 'sku' not in msg:
                    raise
                if attempt == 2:
                    raise HTTPException(status_code=409, detail='装箱同步发生并发冲突，请重试') from None


def _sync_from_products(db: Session, store_id: str):
    products = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False).order_by(Product.created_at.asc()).all()
    existing = {
        sku for (sku,) in db.query(PackingProduct.sku).filter(PackingProduct.store_id == store_id).all()
    }
    imported = skipped_no_sku = 0
    seen = set(existing)
    # 一个 SKU 被第二个产品再次认领 = 装箱单无法把它当成独立产品，前端会提示
    # 「N 条 SKU 重复」。旧实现只声明并回传 0，提示里的数字永远是 0（假通过）。
    claimed = {}       # sku -> 首个认领它的产品 id
    duplicates = set()  # 被后续产品重复认领的 SKU
    for prod in products:
        raw_sku = (prod.sku or '').strip()
        sku_variants = [x for x in re.split(r'[\s,，;；]+', raw_sku) if x]
        if not sku_variants:
            skipped_no_sku += 1
            continue
        name_zh = (prod.chinese_product_name or '').strip()
        name_en = (prod.product_name or '').strip()
        zh_variants = [x for x in re.split(r'[\s,，;；/]+', name_zh) if x]
        # 中文名与 SKU 数量对不上时，不能把整串名字塞给每条分支——那会让 5 个变体
        # 全显示成同一句长名字。这种情况保留装箱单里原有的分支名，等人去产品列表补。
        labels_align = len(zh_variants) == len(sku_variants)
        electric_hint = '是' if '带电' in (prod.shipping_method or '') else ''
        electric, magnetic = px.infer_flags(name_zh, name_en, electric_hint)
        legacy = _find(db, store_id, raw_sku) if len(sku_variants) > 1 else None
        for idx, sku in enumerate(sku_variants):
            if claimed.setdefault(sku, prod.id) != prod.id:
                duplicates.add(sku)
            current = _find(db, store_id, sku) if sku in seen else None
            if labels_align:
                variant_name = zh_variants[idx]
            elif len(sku_variants) == 1:
                variant_name = name_zh
            else:
                variant_name = (current.variant_label or current.name_zh or '') if current else ''
            if sku in seen:
                if current:
                    # 每次同步都刷新产品库名称与分支关系；保留装箱单人工维护的物流属性和上传图片。
                    current.name_zh = variant_name or current.name_zh
                    current.name_en = name_en
                    current.name = ' / '.join(x for x in (current.name_zh, name_en) if x)
                    if prod.actual_weight_kg:
                        current.weight = str(prod.actual_weight_kg)
                    current.variant_group = prod.id if len(sku_variants) > 1 else ''
                    current.variant_label = (variant_name or current.variant_label) if len(sku_variants) > 1 else ''
                    current.is_primary_variant = idx == 0
                continue
            p = PackingProduct(
                store_id=store_id,
                sku=sku,
                name=' / '.join(x for x in (variant_name, name_en) if x),
                name_zh=variant_name,
                name_en=name_en,
                unit=legacy.unit if legacy else '个',
                weight=legacy.weight if legacy else str(prod.actual_weight_kg or ''),
                material=legacy.material if legacy else px.infer_material(variant_name, name_en),
                brand=legacy.brand if legacy else '',
                battery=legacy.battery if legacy else '',
                electric=legacy.electric if legacy else electric,
                magnetic=legacy.magnetic if legacy else magnetic,
                template_row=None,
                default_count=legacy.default_count if legacy else 1,
                image_file=legacy.image_file if legacy else '',
                variant_group=prod.id if len(sku_variants) > 1 else '',
                variant_label=variant_name if len(sku_variants) > 1 else '',
                is_primary_variant=idx == 0,
            )
            db.add(p)
            seen.add(sku)
            imported += 1
        if legacy and raw_sku not in sku_variants:
            db.delete(legacy)
            seen.discard(raw_sku)
    db.commit()
    return {
        'ok': True,
        'total_products': len(products),
        'packing_products': len(seen),
        'imported': imported,
        'skipped_no_sku': skipped_no_sku,
        'duplicate_sku': len(duplicates),
    }


# ---- 2. POST /api/packing/products（批量 UPSERT） ----

@router.post('/products')
def upsert_products(data: BatchIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    count = 0
    for item in data.products:
        try:
            _upsert(db, store_id, item)
            count += 1
        except ValueError as e:
            return fail(400, f'第 {count + 1} 条失败：{e}')
    db.commit()
    return {'ok': True, 'saved': count}


# ---- 3. POST /api/packing/product ----

@router.post('/product')
def upsert_product(data: ProductIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    try:
        _upsert(db, store_id, data)
    except ValueError as e:
        return fail(400, str(e))
    db.commit()
    return {'ok': True}


# ---- 4. POST /api/packing/delete ----

@router.post('/delete')
def delete_product(data: DeleteIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    p = _find(db, store_id, data.sku.strip())
    if p:
        db.delete(p)
        db.commit()
    return {'ok': True}


# ---- 4.5 POST /api/packing/import-from-products（选品库导入） ----

_TAKEALOT_HEADERS = {
    "Referer": "https://www.takealot.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
}


def _fetch_takealot_image(url: str) -> str:
    """抓取 Takealot 图片（复用 image-proxy 的 Referer/UA 头）并按 SHA256 命名入库，
    返回图片文件名；任何失败抛 ValueError，由调用方降级为空图不阻断导入。
    """
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers=_TAKEALOT_HEADERS)
    except Exception as e:
        raise ValueError(f'图片抓取异常：{e}')
    if resp.status_code != 200:
        raise ValueError(f'图片抓取失败 HTTP {resp.status_code}')
    blob = resp.content
    if len(blob) > 20 * 1024 * 1024:
        raise ValueError('图片超过 20 MB')
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(blob)
        tmp = f.name
    try:
        filename = px.add_image('import', tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return filename


def _catalog_product_for_sku(db: Session, sku: str, store_id: str) -> Optional[Product]:
    """按独立 SKU 或历史多 SKU 字段找到选品库产品。"""
    exact = db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False, Product.sku == sku).first()
    if exact:
        return exact
    for candidate in db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False, Product.sku.isnot(None)).all():
        variants = [x for x in re.split(r'[\s,，;；]+', (candidate.sku or '').strip()) if x]
        if sku in variants:
            return candidate
    return None


def _auto_cartons(items):
    """「一箱一个 SKU」时按箱数自动展开成逐箱清单（C01、C02…）。

    装箱页最常见的情况就是每箱只装一个产品：填了「3 箱 × 每箱 10 件」，逐箱清单
    不用再手填一遍——展开就是 C01→10 件、C02→10 件、C03→10 件。混装才需要人工录，
    那种情况前端会把 cartons 一起传上来。
    """
    out = []
    seq = 0
    for p, cartons, count in items:
        for _ in range(int(cartons)):
            seq += 1
            out.append({
                'box_no': 'C%02d' % seq,
                'note': '',
                'items': [{'sku': p['sku'], 'quantity': int(count)}],
            })
    return out


def _normalize_cartons(raw_cartons, items):
    """校验并规整逐箱清单；返回 (箱列表, 每个 SKU 的真实件数, 错误信息)。

    **有逐箱清单就以它为准**：每个 SKU 在箱内的件数合计，才是这一票真正装走的数量。
    「箱数 × 每箱件数」只是一箱一个 SKU 时的快捷写法 —— 混装时它算不出总数
    （2 箱分别装 20 / 15 件，箱数 × 每箱件数凑不出 35），拿它当标准就永远对不上，
    混装票根本导不出来。所以这里只校验「箱里的 SKU 都在本单内」「每个 SKU 至少
    装进一箱」，数量以箱子为准。

    没传逐箱清单时才按「一箱一个 SKU」展开，件数 = 箱数 × 每箱件数。
    """
    names = {}
    for p, _cartons, _count in items:
        names[p['sku']] = (p.get('name_zh', ''), p.get('name_en', ''))

    if not raw_cartons:
        totals = {}
        for p, cartons, count in items:
            totals[p['sku']] = totals.get(p['sku'], 0) + int(cartons) * int(count)
        return _auto_cartons(items), totals, ''

    cartons_out = []
    counted = {}
    for idx, carton in enumerate(raw_cartons, start=1):
        box_no = (carton.box_no or '').strip() or 'C%02d' % idx
        rows = []
        for item in carton.items:
            sku = (item.sku or '').strip()
            if not sku:
                continue
            if sku not in names:
                return None, None, f'第 {idx} 箱里有不在本次装箱单中的 SKU：{sku}'
            try:
                qty = int(str(item.quantity).strip())
            except (TypeError, ValueError):
                return None, None, f'第 {idx} 箱的 {sku} 数量不是整数'
            if qty < 1:
                return None, None, f'第 {idx} 箱的 {sku} 数量必须 ≥1'
            counted[sku] = counted.get(sku, 0) + qty
            rows.append({'sku': sku, 'quantity': qty})
        if not rows:
            return None, None, f'第 {idx} 箱没有装任何产品，请删除空箱或补上明细'
        cartons_out.append({'box_no': box_no, 'note': (carton.note or '').strip(), 'items': rows})

    if not cartons_out:
        return None, None, '逐箱清单为空，请至少保留 1 箱'

    # 勾了产品却一箱都没装 —— 这才是真错（箱子封上了才发现少装，只能补发）
    for sku in names:
        if sku not in counted:
            label = names[sku][0] or sku
            return None, None, f'{label}（{sku}）还没装进任何一箱'
    return cartons_out, counted, ''


def _sku_box_counts(cartons):
    """每个 SKU 装进了几箱 —— ShipmentLine 的「箱数」列就用这个。"""
    boxes = {}
    for carton in cartons:
        for item in carton['items']:
            boxes[item['sku']] = boxes.get(item['sku'], 0) + 1
    return boxes


def _line_quantities(items, shipped, boxes):
    """[(产品, 箱数, 每箱件数, 总件数), ...]，总件数以逐箱清单为准。

    混装时「每箱件数」没有意义（同一 SKU 各箱数量不同），这里取向上取整只作展示，
    真正参与库存核减的是 total_quantity。
    """
    out = []
    for p, cartons, count in items:
        sku = p['sku']
        total = int(shipped.get(sku, 0))
        box_count = int(boxes.get(sku, 0)) or int(cartons)
        per_carton = -(-total // box_count) if box_count else int(count)
        out.append((p, box_count, max(1, per_carton), total))
    return out


def _write_cartons(db: Session, shipment: Shipment, store_id: str, cartons, items):
    """把逐箱清单落到 shipment_cartons / shipment_carton_items。"""
    names = {p['sku']: (p.get('name_zh', ''), p.get('name_en', '')) for p, _, _ in items}
    for seq, carton in enumerate(cartons, start=1):
        row = ShipmentCarton(
            id=str(uuid.uuid4()),
            shipment_id=shipment.id,
            store_id=store_id,
            seq=seq,
            box_no=carton['box_no'],
            note=carton.get('note', ''),
        )
        db.add(row)
        db.flush()
        for item in carton['items']:
            name_zh, name_en = names.get(item['sku'], ('', ''))
            db.add(ShipmentCartonItem(
                id=str(uuid.uuid4()),
                carton_id=row.id,
                sku=item['sku'],
                name_zh=name_zh,
                name_en=name_en,
                quantity=item['quantity'],
            ))


def _replace_cartons(db: Session, shipment: Shipment, store_id: str, cartons, items):
    """重写某票货的逐箱清单（先删旧箱和箱内明细，再按新布局写入）。

    同一草稿重复导出时，装箱单 Excel 是按**本次**数量重新生成的；如果箱清单只写第一次，
    就会出现「Excel 上是新数量、箱唛还是旧的」——印错标签比没有标签更糟。
    """
    old = db.query(ShipmentCarton).filter(ShipmentCarton.shipment_id == shipment.id).all()
    if old:
        db.query(ShipmentCartonItem).filter(
            ShipmentCartonItem.carton_id.in_([c.id for c in old])
        ).delete(synchronize_session=False)
        db.query(ShipmentCarton).filter(
            ShipmentCarton.shipment_id == shipment.id
        ).delete(synchronize_session=False)
    _write_cartons(db, shipment, store_id, cartons, items)


def _hydrate_export_product(db: Session, product: PackingProduct, store_id: str) -> None:
    """导出前补齐选品库中的重量和本地图片，不让远程图片只停留在页面预览。"""
    source = _catalog_product_for_sku(db, product.sku, store_id)
    if source is None:
        return
    if source.actual_weight_kg:
        product.weight = str(source.actual_weight_kg)
    image_url = _catalog_images_by_sku([source]).get(product.sku, '')
    if not product.image_file and image_url:
        try:
            product.image_file = _fetch_takealot_image(image_url)
        except Exception:
            # 单张图片抓取失败时保留空图，避免阻断整份装箱单。
            pass


@router.post('/import-from-products')
def import_from_products(data: ImportFromProductsIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """选品 products 批量导入装箱单库（幂等：SKU 已存在则跳过，不覆盖云端已有编辑）。
    字段映射 + Takealot 图片自动抓取转存 SHA256；图片失败不阻断导入。
    """
    imported = skipped = 0
    failed = []
    for pid in data.product_ids:
        prod = db.query(Product).filter(Product.id == pid, Product.store_id == store_id, Product.is_archived == False).first()
        if prod is None:
            failed.append({'sku': '', 'reason': f'选品产品 {pid} 不存在'})
            continue
        sku = (prod.sku or '').strip()
        name_zh = (prod.chinese_product_name or '').strip()
        name_en = (prod.product_name or '').strip()
        if not sku:
            skipped += 1
            failed.append({'sku': sku, 'reason': 'SKU 为空'})
            continue
        if _find(db, store_id, sku):
            skipped += 1
            continue
        electric_hint = '是' if (prod.shipping_method or '').find('带电') >= 0 else ''
        electric, magnetic = px.infer_flags(name_zh, name_en, electric_hint)
        p = PackingProduct(store_id=store_id, sku=sku)
        p.name = ' / '.join(x for x in (name_zh, name_en) if x)
        p.name_zh = name_zh
        p.name_en = name_en
        p.unit = '个'
        p.weight = str(prod.actual_weight_kg or '')
        p.material = px.infer_material(name_zh, name_en)
        p.brand = ''
        p.battery = ''
        p.electric = electric
        p.magnetic = magnetic
        p.template_row = None
        p.default_count = 1
        image_url = (prod.product_image_url or '').strip()
        if image_url:
            try:
                p.image_file = _fetch_takealot_image(image_url)
            except Exception as e:
                p.image_file = ''
                failed.append({'sku': sku, 'reason': f'图片抓取失败：{e}'})
        db.add(p)
        imported += 1
    db.commit()
    return {'ok': True, 'imported': imported, 'skipped': skipped, 'failed': failed}


# ---- 4.6 POST /api/packing/sync-images-from-products（按 SKU 从选品库匹配图片） ----

@router.post('/sync-images-from-products')
def sync_images_from_products(data: SyncImagesIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """把装箱单库中"待补图"（image_file 为空）的产品，按 SKU 去选品库 products
    匹配 product_image_url 并抓取转存为 SHA256 图片。

    - 仅处理 image_file 为空的产品，不覆盖用户手动上传的图
    - 按 SKU 精确匹配（str 直接比较，与导入逻辑一致）
    - 图片抓取失败不阻断，记入 failed 返回
    """
    limit = max(0, int(getattr(data, 'limit', 100) or 100))
    pending = (
        db.query(PackingProduct)
        .filter(PackingProduct.store_id == store_id, PackingProduct.image_file == '')
        .order_by(PackingProduct.sku)
        .all()
    )
    if limit > 0:
        pending = pending[:limit]
    source_images = _catalog_images_by_sku(db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False).all())
    updated = 0
    failed = []
    for p in pending:
        image_url = source_images.get(p.sku, '')
        if not image_url:
            continue
        try:
            p.image_file = _fetch_takealot_image(image_url)
            db.commit()
            updated += 1
        except Exception as e:
            db.rollback()
            failed.append({'sku': p.sku, 'reason': str(e)})
    db.commit()
    return {
        'ok': True,
        'updated': updated,
        'total_pending': len(pending),
        'failed': failed,
    }


@router.post('/sync-variants-from-takealot')
async def sync_variants_from_takealot(data: SyncVariantsIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """从 Takealot 页面结构化状态匹配真实 SKU 变体。

    只有官网同时给出 SKU 与规格/图片时才写入，绝不按顺序猜测颜色或复制主图。
    """
    from services.takealot_scraper import scrape_product
    limit = max(1, min(int(data.limit or 20), 50))
    candidates = []
    for product in db.query(Product).filter(Product.store_id == store_id, Product.is_archived == False, Product.takealot_url.isnot(None), Product.sku.isnot(None)).all():
        skus = [x for x in re.split(r'[\s,，;；]+', (product.sku or '').strip()) if x]
        if len(skus) > 1 and product.takealot_url:
            candidates.append((product, skus))
    matched = images = 0
    unresolved = []
    for product, known_skus in candidates[:limit]:
        result = await scrape_product(product.takealot_url)
        official = {str(item.get('sku', '')).strip(): item for item in result.get('variants', []) if item.get('sku')}
        group_matched = 0
        for sku in known_skus:
            item = official.get(sku)
            row = _find(db, store_id, sku)
            if not item or not row:
                continue
            label = str(item.get('label') or '').strip()
            if label:
                row.variant_label = label
                row.name_zh = label if not row.name_zh or re.fullmatch(r'款式\s*\d+', row.name_zh) else row.name_zh
                group_matched += 1
                matched += 1
            image_url = str(item.get('image_url') or '').strip()
            if image_url:
                try:
                    row.image_file = _fetch_takealot_image(image_url)
                    images += 1
                except Exception:
                    pass
        if group_matched == 0:
            unresolved.append({'product_id': product.id, 'skus': known_skus, 'reason': '官网未返回可验证的 SKU 变体关系'})
        db.commit()
    return {'ok': True, 'groups_checked': min(len(candidates), limit), 'matched': matched, 'images': images, 'unresolved': unresolved}


# ---- 5. POST /api/packing/image ----

@router.post('/image')
def upload_image(data: ImageIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    sku = data.sku.strip()
    p = _find(db, store_id, sku)
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
def export_excel(data: ExportIn, db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    if not data.items or len(data.items) > 500:
        return fail(400, '请选择 1 至 500 个产品')
    try:
        date = dt.date.fromisoformat(data.date.strip())
    except ValueError:
        return fail(400, '请填写 YYYY-MM-DD 格式的有效日期')
    # 唛头的三层兜底：手填 > OMS 入库单号 > KPLDJ。
    # 中间那层是关键 —— 装箱单模板里「客户唛头」和 A 列「箱唛」是同一套编号，
    # 用户只填一次入库单号，两处就自动一致，不用记着再抄一遍。
    mark = data.mark.strip() or data.inbound_no.strip() or 'KPLDJ'
    items = []
    existing_shipment = None
    if data.draft_key.strip():
        existing_shipment = db.query(Shipment).filter(Shipment.draft_key == data.draft_key.strip(), Shipment.store_id == store_id).first()
    expected_status = 'confirmed' if data.record_shipment else 'exported'
    if existing_shipment and existing_shipment.status != expected_status:
        return fail(409, '此草稿已按其他方式留档，请新建装箱单后导出，避免重复扣库存')
    requested_by_sku = {}
    for line in data.items:
        p = _find(db, store_id, line.sku.strip())
        if not p:
            return fail(400, '找不到产品 ' + line.sku)
        if not _catalog_product_for_sku(db, p.sku, store_id):
            return fail(400, f'{p.sku} 不属于当前店铺，请先加入当前店铺产品列表')
        _hydrate_export_product(db, p, store_id)
        cartons = line.cartons.strip()
        count = line.count.strip()
        if not cartons.isdecimal() or not count.isdecimal() or int(cartons) < 1 or int(count) < 1:
            return fail(400, '箱数和每箱件数须为正整数')
        row = public(p)
        row['box_no'] = line.box_no.strip()
        items.append((row, cartons, count))
    # 逐箱清单先校验：箱里的 SKU 必须都在本单内、每个 SKU 至少装进一箱。
    cartons_plan, shipped_by_sku, cartons_error = _normalize_cartons(data.cartons, items)
    if cartons_error:
        return fail(400, cartons_error)
    # 数量一律以逐箱清单为准。混装时「箱数 × 每箱件数」凑不出真实总数，
    # 拿它去核库存会把「箱里装了 35 件、账上扣 34 件」的错记下来。
    boxes_by_sku = _sku_box_counts(cartons_plan)
    line_plan = _line_quantities(items, shipped_by_sku, boxes_by_sku)
    for _p, _boxes, _per, _total in line_plan:
        requested_by_sku[_p['sku']] = requested_by_sku.get(_p['sku'], 0) + _total
    db.commit()
    if data.record_shipment and not existing_shipment:
        for sku, requested in requested_by_sku.items():
            available = inventory_balance_for_sku(db, sku, store_id)
            if available < requested:
                return fail(400, f'{sku} 库存不足：可用 {available}，本次发货 {requested}')
    try:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / 'list.xlsx'
            # 装箱单也按箱展开：A 列（箱唛）写箱唛号，物流按「箱唛 + 箱数」认箱。
            # 没填入库单号时箱号是 C01、C02，同样能对上逐箱清单。
            px.generate(
                items, date.isoformat(), mark, data.shipping, data.address, output,
                boxes=[(c['box_no'], [(it['sku'], it['quantity']) for it in c['items']]) for c in cartons_plan],
            )
            content = output.read_bytes()
    except FileNotFoundError as e:
        return fail(400, str(e))
    except Exception as e:
        return fail(400, f'生成失败：{e}')
    if not existing_shipment:
        # 导出快照和正式发货分别留档，只有 confirmed 记录参与库存核减。
        day_count = db.query(Shipment).filter(Shipment.store_id == store_id, Shipment.shipment_date == date.isoformat()).count()
        shipment = Shipment(
            id=str(uuid.uuid4()),
            store_id=store_id,
            draft_key=data.draft_key.strip() or str(uuid.uuid4()),
            shipment_no=f'PK-{date.strftime("%Y%m%d")}-{day_count + 1:03d}',
            shipment_date=date.isoformat(),
            mark=mark,
            shipping=data.shipping,
            address=data.address,
            status=expected_status,
        )
        db.add(shipment)
        db.flush()
        for p, box_count, per_carton, total in line_plan:
            db.add(ShipmentLine(
                id=str(uuid.uuid4()),
                shipment_id=shipment.id,
                sku=p['sku'],
                name_zh=p.get('name_zh', ''),
                name_en=p.get('name_en', ''),
                image_file=p.get('image_file', ''),
                cartons=box_count,
                count_per_carton=per_carton,
                total_quantity=total,
            ))
        # 逐箱清单：一箱一条，箱唛就按这个印「第几箱 / 共几箱」
        _write_cartons(db, shipment, store_id, cartons_plan, items)
        record_operation(db, store_id, "shipment", shipment.id, "confirm" if data.record_shipment else "export", f'{"确认发货单" if data.record_shipment else "导出装箱单"} {shipment.shipment_no}', {
            "shipment_date": shipment.shipment_date,
            "sku_count": len(items),
            "total_quantity": sum(row[3] for row in line_plan),
            "carton_count": len(cartons_plan),
            "inventory_deducted": data.record_shipment,
        })
        db.commit()
    else:
        # 同一草稿再导出：Excel 是按本次 payload 重新生成的，快照必须跟着更新，
        # 否则 Excel 上是新数量、库里还是旧数量，箱唛也会印错。
        stored = db.query(ShipmentLine).filter(ShipmentLine.shipment_id == existing_shipment.id).all()
        stored_totals = {l.sku: l.total_quantity for l in stored}
        new_totals = {}
        for _p, _boxes, _per, _total in line_plan:
            new_totals[_p['sku']] = new_totals.get(_p['sku'], 0) + _total
        if existing_shipment.status == 'confirmed' and stored_totals != new_totals:
            return fail(409, '这张装箱单已确认发货（库存已扣减），不能改数量；要改请先作废再重开')
        dropped = [l for l in stored if l.sku not in new_totals]
        if dropped:
            used = db.query(ShipmentAllocation).filter(
                ShipmentAllocation.line_id.in_([l.id for l in dropped])
            ).count()
            if used:
                return fail(409, '该发货单的明细已被库存分摊引用，不能删除；请作废后重新开单')
            for line in dropped:
                db.delete(line)
        for p, box_count, per_carton, total in line_plan:
            line = next((l for l in stored if l.sku == p['sku']), None)
            if line is None:
                db.add(ShipmentLine(
                    id=str(uuid.uuid4()),
                    shipment_id=existing_shipment.id,
                    sku=p['sku'],
                    name_zh=p.get('name_zh', ''),
                    name_en=p.get('name_en', ''),
                    image_file=p.get('image_file', ''),
                    cartons=box_count,
                    count_per_carton=per_carton,
                    total_quantity=total,
                ))
            else:
                line.cartons = box_count
                line.count_per_carton = per_carton
                line.total_quantity = total
        _replace_cartons(db, existing_shipment, store_id, cartons_plan, items)
        db.commit()
    return Response(
        content=content,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="packing_{date.isoformat()}.xlsx"'},
    )


# ---- 8. GET /api/packing/cartons ----

@router.get('/cartons')
def list_cartons(date: str = '', sku: str = '', limit: int = 300,
                 db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    """逐箱清单查询：某天装了哪些箱，或反查某个 SKU 在哪几箱。

    - `date`：按发货日期过滤（业务时区 YYYY-MM-DD）；不传则最近 50 票
    - `sku`：只返回装有该 SKU 的箱，但**仍带该票货的总箱数**，箱唛才能印「3/5」

    箱子封上以后外观一样，「哪箱装了什么」只能查这里。
    """
    query = db.query(Shipment).filter(Shipment.store_id == store_id)
    if date.strip():
        query = query.filter(Shipment.shipment_date == date.strip())
    shipments = query.order_by(Shipment.created_at.desc()).limit(50).all()
    if not shipments:
        return {'date': date.strip(), 'total_cartons': 0, 'cartons': []}

    ids = [s.id for s in shipments]
    cartons = (db.query(ShipmentCarton)
               .filter(ShipmentCarton.shipment_id.in_(ids))
               .order_by(ShipmentCarton.seq)
               .all())
    if not cartons:
        return {'date': date.strip(), 'total_cartons': 0, 'cartons': []}

    items_by_carton = {}
    for item in (db.query(ShipmentCartonItem)
                 .filter(ShipmentCartonItem.carton_id.in_([c.id for c in cartons]))
                 .all()):
        items_by_carton.setdefault(item.carton_id, []).append(item)

    # 每票货的总箱数，用于「第 3 箱 / 共 5 箱」
    total_by_shipment = {}
    for c in cartons:
        total_by_shipment[c.shipment_id] = total_by_shipment.get(c.shipment_id, 0) + 1
    shipment_by_id = {s.id: s for s in shipments}

    wanted_sku = sku.strip()
    rows = []
    for carton in cartons:
        items = items_by_carton.get(carton.id, [])
        if wanted_sku and not any(i.sku == wanted_sku for i in items):
            continue
        shipment = shipment_by_id[carton.shipment_id]
        rows.append({
            'shipment_id': carton.shipment_id,
            'shipment_no': shipment.shipment_no,
            'shipment_date': shipment.shipment_date,
            'mark': shipment.mark or '',
            'seq': carton.seq,
            'total': total_by_shipment.get(carton.shipment_id, 0),
            'box_no': carton.box_no,
            'note': carton.note or '',
            'items': [
                {'sku': i.sku, 'name_zh': i.name_zh or '', 'name_en': i.name_en or '', 'quantity': i.quantity}
                for i in items
            ],
        })
        if len(rows) >= max(1, min(limit, 1000)):
            break
    return {'date': date.strip(), 'total_cartons': len(rows), 'cartons': rows}


# ---- 9. POST /api/packing/import ----

@router.post('/import')
async def import_migration(file: Optional[UploadFile] = File(None), db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
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
        if _find(db, store_id, sku):
            skipped += 1
            continue
        p = PackingProduct(store_id=store_id, sku=sku)
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


# ---- 10. GET /api/packing/backup ----

@router.get('/backup')
def backup(db: Session = Depends(get_db), store_id: str = Depends(get_store_id)):
    products = [public(p) for p in db.query(PackingProduct).filter(PackingProduct.store_id == store_id).order_by(PackingProduct.sku).all()]
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


# ---- 11. GET /packing-ui（静态托管远程版前端） ----

ui_router = APIRouter(tags=["packing-ui"])

_PACKING_UI = Path(__file__).resolve().parent.parent / 'packing_ui' / 'index.html'


@ui_router.get('/packing-ui')
def packing_ui():
    if not _PACKING_UI.is_file():
        return fail(404, 'packing_ui/index.html 未部署')
    return FileResponse(str(_PACKING_UI), media_type='text/html; charset=utf-8')
