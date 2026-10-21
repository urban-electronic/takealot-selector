"""
Takealot 选品系统 - 主入口
"""

import hmac
import os
import sys

# 确保 backend 目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base, SessionLocal, get_db
from models import DEFAULT_STORE_ID, FeeCategory, FeeMappingRule, Store, SystemSettings
from api import product_routes, scraper_routes, category_routes, settings_routes, image_proxy, procurement_routes, packing_routes, inventory_routes, store_routes, activity_routes
from migrate import migrate_from_dump
from api import operations_routes
from api import pricing_routes, buybox_routes, collector_routes, cloud_browser_routes
from api import diag_routes
from api import market_price_routes

app = FastAPI(title="Takealot 选品与利润测算系统", version="1.0.0")


@app.on_event("startup")
async def ensure_playwright_browsers():
    """确保 Playwright 浏览器已安装。若是 Docker / 无头环境，自动下载。"""
    import subprocess, sys
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            pass  # 仅仅测试是否能启动，不能启动则自动下载
        print("[startup] Playwright 浏览器已就绪", flush=True)
    except ImportError:
        print("[startup] Playwright 未安装，尝试安装...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"], check=True)
        print("[startup] Playwright 安装完成", flush=True)
    except Exception as e:
        print(f"[startup] Playwright 浏览器缺失，正在自动安装: {e}", flush=True)
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
                           check=True, timeout=300)
            print("[startup] Playwright 浏览器安装完成", flush=True)
        except Exception as e2:
            print(f"[startup] Playwright 浏览器安装失败: {e2}", flush=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- API Key 鉴权（可选）----
# 设置环境变量 API_KEY 后，所有 /api 请求必须携带 X-API-Key 请求头；
# 未设置环境变量时保持放行（兼容本地开发）。
from fastapi import Request
from fastapi.responses import JSONResponse

_API_KEY = os.environ.get("API_KEY", "").strip()


def _cors_headers(request: Request) -> dict:
    """为 401 等提前返回的响应手动补 CORS 头（CORSMiddleware 理论上会处理，
    但实测部分 Starlette 版本对中间件内直接返回的响应不追加头，导致浏览器拦截）。"""
    origin = request.headers.get("origin", "")
    return {
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
        "Vary": "Origin",
    }


@app.middleware("http")
async def api_key_check(request: Request, call_next):
    # CORS 预检（OPTIONS）不带 X-API-Key，必须放行交给 CORSMiddleware 处理，
    # 否则浏览器跨域请求 preflight 401 导致 "Failed to fetch"
    if request.method == "OPTIONS":
        return await call_next(request)
    # image-proxy 由路由层自己校验 token/header（<img> 无法带 header）
    if _API_KEY and request.url.path.startswith("/api") and not request.url.path.startswith("/api/image-proxy"):
        key = request.headers.get("X-API-Key", "")
        if request.url.path.startswith("/api/collector/"):
            key = key or request.headers.get("X-Collector-Key", "")
        if not hmac.compare_digest(key.encode('utf-8', 'ignore'), _API_KEY.encode('utf-8', 'ignore')):
            return JSONResponse(
                status_code=401,
                content={"detail": "无效或缺失 API Key，请在设置页填写"},
                headers=_cors_headers(request),
            )
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未处理异常的 500 响应也带 CORS 头（F7 同型修复的延伸）。

    Starlette 的 ServerErrorMiddleware 位于 CORS 中间件外层，未包装的 500
    默认不带 Access-Control-Allow-Origin，浏览器会把真实 500 误报为
    “CORS 错误”，前端与排障都看不到真实原因（2026-10-15 C 域审计实测：
    /api/packing/sync-from-products 的 UNIQUE 500 即被掩盖为 CORS 报错）。
    Starlette 发送本响应后仍会 re-raise，服务端 traceback 日志不受影响。
    """
    import logging
    from uuid import uuid4
    request_id = uuid4().hex
    logging.getLogger(__name__).error('Unhandled request id=%s type=%s', request_id, type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试", "request_id": request_id},
        headers={**_cors_headers(request), "X-Request-ID": request_id},
    )


# 注册路由
app.include_router(product_routes.router)
app.include_router(scraper_routes.router)
app.include_router(category_routes.router)
app.include_router(settings_routes.router)
app.include_router(image_proxy.router)
app.include_router(procurement_routes.router)
app.include_router(packing_routes.router)
app.include_router(inventory_routes.router)
app.include_router(store_routes.router)
app.include_router(activity_routes.router)
app.include_router(operations_routes.router)
app.include_router(pricing_routes.router)
app.include_router(buybox_routes.router)
app.include_router(collector_routes.router)
app.include_router(cloud_browser_routes.router)
app.include_router(packing_routes.ui_router)
app.include_router(diag_routes.router)
app.include_router(market_price_routes.router)


# 默认费率表
DEFAULT_FEE_CATEGORIES = [
    ("Clothing & Footwear", "10.0%–18.0%", 0.18),
    ("Sport", "12.0%–15.0%", 0.15),
    ("Music & DVD", "10.0%–15.0%", 0.15),
    ("Luggage & Travel", "15.0%–15.0%", 0.15),
    ("Homeware", "15.0%–15.0%", 0.15),
    ("Games", "5.5%–15.0%", 0.15),
    ("Camping & Outdoor", "8.0%–15.0%", 0.15),
    ("Beauty", "10.0%–15.0%", 0.15),
    ("Baby", "12.0%–15.0%", 0.15),
    ("Stationery", "10.0%–14.0%", 0.14),
    ("Smart Home & Connected Living", "5.0%–14.0%", 0.14),
    ("Garden, Pool & Patio", "12.0%–14.0%", 0.14),
    ("Electronic Accessories", "10.0%–14.0%", 0.14),
    ("Books", "14.0%–14.0%", 0.14),
    ("TV & Audio", "5.5%–12.0%", 0.12),
    ("Toys", "12.0%–12.0%", 0.12),
    ("Small Appliances", "10.0%–12.0%", 0.12),
    ("Office", "7.0%–12.0%", 0.12),
    ("Musical Instruments", "8.0%–12.0%", 0.12),
    ("Health", "10.0%–12.0%", 0.12),
    ("DIY & Automotive", "10.0%–12.0%", 0.12),
    ("Cameras", "4.0%–12.0%", 0.12),
    ("Pets", "10.0%–10.0%", 0.10),
    ("Office Furniture", "10.0%–10.0%", 0.10),
    ("Liquor", "7.0%–10.0%", 0.10),
    ("Large Appliances", "8.0%–10.0%", 0.10),
    ("Computers & Laptops", "5.0%–9.0%", 0.09),
    ("Computer Components", "6.0%–9.0%", 0.09),
    ("Non-Perishable", "8.0%–8.0%", 0.08),
]


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _ensure_default_store()
    migrate_from_dump()
    _init_default_data()
    _backfill_offer_catalog()
    _repair_product_metadata()
    # 线程/进程用量定期落日志。2026-10-21 线程耗尽导致图片全线 500 时，
    # 系统里没有任何地方能看到这个数字，只能靠重启试探；有了这行日志，
    # 「一开始就高」和「跑着跑着涨上去」能当场分清。
    from services import runtime_health
    runtime_health.start_watchdog()
    print(f"[startup] 运行时基线 {runtime_health.format_snapshot()}", flush=True)


def _backfill_offer_catalog():
    """用卖家导出的权威清单补现有产品，不导入清单中的额外产品。"""
    from services.offer_catalog_backfill import apply_offer_catalog_backfill

    db = SessionLocal()
    try:
        stats = apply_offer_catalog_backfill(db, store_id=DEFAULT_STORE_ID)
        print(f"[startup] Offer Export 补齐完成: {stats}", flush=True)
    except Exception as exc:
        db.rollback()
        print(f"[startup] Offer Export 补齐失败: {exc}", flush=True)
    finally:
        db.close()


def _repair_product_metadata():
    """固化历史编号，并确认已经落在有效费率表中的 Fee 品类。"""
    from models import Product
    from api.product_routes import _apply_calculated_fields

    db = SessionLocal()
    try:
        valid_categories = {row.name: row for row in db.query(FeeCategory).filter(FeeCategory.active == True).all()}
        changed_numbers = confirmed_categories = 0
        store_ids = [row[0] for row in db.query(Product.store_id).distinct().all()]
        for store_id in store_ids:
            products = db.query(Product).filter(Product.store_id == store_id).order_by(Product.created_at.asc(), Product.id.asc()).all()
            next_no = max((p.product_no or 0 for p in products), default=0)
            for product in products:
                if not product.product_no:
                    next_no += 1
                    product.product_no = next_no
                    changed_numbers += 1
                category = valid_categories.get((product.fee_category or "").strip())
                if category and not product.fee_category_confirmed:
                    product.fee_category_confirmed = True
                    product.success_fee_rate = category.success_fee_rate
                    product.fee_rate_range = category.fee_rate_range
                    _apply_calculated_fields(db, product)
                    confirmed_categories += 1
        if changed_numbers or confirmed_categories:
            db.commit()
        print(f"[startup] 产品元数据返检: 固化编号 {changed_numbers}, 自动确认 Fee 品类 {confirmed_categories}", flush=True)
    except Exception as exc:
        db.rollback()
        print(f"[startup] 产品元数据返检失败: {exc}", flush=True)
    finally:
        db.close()


def _strip_foreign_keys(table):
    """重建临时表时去掉外键：目标表不在同一个 MetaData 里，SQLite 建表会报找不到被引用表。"""
    from sqlalchemy import ForeignKeyConstraint

    for con in list(table.constraints):
        if isinstance(con, ForeignKeyConstraint):
            table.constraints.discard(con)
    for col in table.columns:
        col.foreign_keys.clear()


def _migrate_packing_products_store_scope():
    """packing_products：全局 sku 主键 → (store_id, sku) 复合主键。

    两个店铺共用一张装箱产品表时，同名 SKU 会互相覆盖重量/材质/图片/模板行。
    create_all 不会改已有表，只能重建：老数据全部归入默认店铺。
    """
    from sqlalchemy import inspect, text, MetaData, ForeignKeyConstraint
    from models import PackingProduct

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("packing_products")}
    pk = insp.get_pk_constraint("packing_products") or {}
    pk_cols = list(pk.get("constrained_columns") or [])
    if "store_id" in cols and pk_cols == ["store_id", "sku"]:
        return

    tmp_name = "packing_products__new"
    target_cols = [c.name for c in PackingProduct.__table__.columns]
    old_cols = [c for c in target_cols if c in cols]
    if "store_id" not in old_cols:
        old_cols.append("store_id")
    select_cols = ", ".join(
        f"'{DEFAULT_STORE_ID}'" if c == "store_id" else c for c in old_cols
    )
    tmp = PackingProduct.__table__.to_metadata(MetaData(), name=tmp_name)
    _strip_foreign_keys(tmp)
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {tmp_name}"))
        tmp.create(conn)
        conn.execute(text(
            f"INSERT INTO {tmp_name} ({', '.join(old_cols)}) SELECT {select_cols} FROM packing_products"
        ))
        conn.execute(text("DROP TABLE packing_products"))
        conn.execute(text(f"ALTER TABLE {tmp_name} RENAME TO packing_products"))
    print("[startup] packing_products 已按店铺隔离重建（老数据归入默认店铺）", flush=True)


def _migrate_shipments_store_scope():
    """shipments：发货单号从「全局唯一」改为「按店铺唯一」。

    否则两个店铺不能同时拥有 PK-YYYYMMDD-001，编号会被别店占用。
    """
    from sqlalchemy import inspect, text, MetaData, UniqueConstraint
    from models import Shipment

    insp = inspect(engine)
    ucs = insp.get_unique_constraints("shipments") or []
    single_no = [
        u for u in ucs
        if [c for c in (u.get("column_names") or [])] == ["shipment_no"]
    ]
    if not single_no:
        return

    tmp_name = "shipments__new"
    cols = [c.name for c in Shipment.__table__.columns]
    tmp = Shipment.__table__.to_metadata(MetaData(), name=tmp_name)
    _strip_foreign_keys(tmp)
    tmp.indexes = set()
    for col in tmp.columns:
        col.index = False
        col.unique = False
    for con in list(tmp.constraints):
        if isinstance(con, UniqueConstraint):
            tmp.constraints.discard(con)
    tmp.append_constraint(UniqueConstraint("draft_key", name="uq_shipments_draft_key"))
    tmp.append_constraint(UniqueConstraint("store_id", "shipment_no", name="ux_shipments_store_no"))

    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {tmp_name}"))
        tmp.create(conn)
        conn.execute(text(
            f"INSERT INTO {tmp_name} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM shipments"
        ))
        conn.execute(text("DROP TABLE shipments"))
        conn.execute(text(f"ALTER TABLE {tmp_name} RENAME TO shipments"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shipments_store_id ON shipments (store_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shipments_shipment_no ON shipments (shipment_no)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shipments_draft_key ON shipments (draft_key)"))
    print("[startup] shipments 发货单号已改为按店铺唯一", flush=True)


def _ensure_columns():
    """轻量迁移：为已存在的表补齐新增列（create_all 不会修改已有表）"""
    from sqlalchemy import inspect, text
    try:
        insp = inspect(engine)
        if "products" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("products")}
            if "unit_price_cny" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE products ADD COLUMN unit_price_cny REAL"))
                print("[startup] products 表已补列 unit_price_cny", flush=True)
            if "store_id" not in cols:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE products ADD COLUMN store_id TEXT DEFAULT '{DEFAULT_STORE_ID}'"))
                    conn.execute(text(f"UPDATE products SET store_id = '{DEFAULT_STORE_ID}' WHERE store_id IS NULL OR store_id = ''"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_store_id ON products (store_id)"))
                print("[startup] products 表已归入默认店铺", flush=True)
            product_additions = {
                "is_archived": "BOOLEAN DEFAULT 0",
                "archived_at": "DATETIME",
                "market_prices": "TEXT DEFAULT ''",
            }
            with engine.begin() as conn:
                for name, sql_type in product_additions.items():
                    if name not in cols:
                        conn.execute(text(f"ALTER TABLE products ADD COLUMN {name} {sql_type}"))
                        print(f"[startup] products 表已补列 {name}", flush=True)
                conn.execute(text("UPDATE products SET is_archived = 0 WHERE is_archived IS NULL"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_is_archived ON products (is_archived)"))
        if "packing_products" in insp.get_table_names():
            packing_cols = {c["name"] for c in insp.get_columns("packing_products")}
            additions = {
                "variant_group": "TEXT DEFAULT ''",
                "variant_label": "TEXT DEFAULT ''",
                "is_primary_variant": "BOOLEAN DEFAULT 1",
            }
            with engine.begin() as conn:
                for name, sql_type in additions.items():
                    if name not in packing_cols:
                        conn.execute(text(f"ALTER TABLE packing_products ADD COLUMN {name} {sql_type}"))
                        print(f"[startup] packing_products 表已补列 {name}", flush=True)
            _migrate_packing_products_store_scope()
        if "shipments" in insp.get_table_names():
            _migrate_shipments_store_scope()
        if "stores" in insp.get_table_names():
            store_cols = {c["name"] for c in insp.get_columns("stores")}
            store_additions = {
                "sync_interval_minutes": "INTEGER DEFAULT 60",
                "external_store_ref": "TEXT DEFAULT ''",
                "allow_negative_inventory_shipments": "BOOLEAN DEFAULT 0",
            }
            with engine.begin() as conn:
                for name, sql_type in store_additions.items():
                    if name not in store_cols:
                        conn.execute(text(f"ALTER TABLE stores ADD COLUMN {name} {sql_type}"))
                        if name == "allow_negative_inventory_shipments":
                            conn.execute(text("UPDATE stores SET allow_negative_inventory_shipments = 1 WHERE id = :store_id"), {"store_id": DEFAULT_STORE_ID})
                        print(f"[startup] stores 表已补列 {name}", flush=True)
        store_scoped_tables = ("procurement_records", "inventory_adjustments", "shipments")
        with engine.begin() as conn:
            for table_name in store_scoped_tables:
                if table_name not in insp.get_table_names():
                    continue
                cols = {c["name"] for c in insp.get_columns(table_name)}
                if "store_id" not in cols:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN store_id TEXT DEFAULT '{DEFAULT_STORE_ID}'"))
                    conn.execute(text(f"UPDATE {table_name} SET store_id = '{DEFAULT_STORE_ID}' WHERE store_id IS NULL OR store_id = ''"))
                    print(f"[startup] {table_name} 表已补列 store_id", flush=True)
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_store_id ON {table_name} (store_id)"))
        if "procurement_records" in insp.get_table_names():
            procurement_cols = {c["name"] for c in insp.get_columns("procurement_records")}
            with engine.begin() as conn:
                if "status" not in procurement_cols:
                    conn.execute(text("ALTER TABLE procurement_records ADD COLUMN status TEXT DEFAULT 'received'"))
                    conn.execute(text("UPDATE procurement_records SET status = 'received' WHERE status IS NULL OR status = ''"))
                    print("[startup] 历史采购记录已标记为已入库", flush=True)
                if "status_updated_at" not in procurement_cols:
                    conn.execute(text("ALTER TABLE procurement_records ADD COLUMN status_updated_at DATETIME"))
    except Exception as e:
        print(f"[startup] _ensure_columns 迁移失败: {e}", flush=True)


def _ensure_default_store():
    db = SessionLocal()
    try:
        default_store = db.query(Store).filter(Store.id == DEFAULT_STORE_ID).first()
        if not default_store:
            db.add(Store(id=DEFAULT_STORE_ID, name="自营店铺", platform="Takealot", status="active", sync_method="manual", allow_negative_inventory_shipments=True))
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"[startup] 默认店铺初始化失败: {e}", flush=True)
    finally:
        db.close()


def _init_default_data():
    db = SessionLocal()
    try:
        # 初始化费率表
        existing = db.query(FeeCategory).count()
        if existing == 0:
            for name, rate_range, rate in DEFAULT_FEE_CATEGORIES:
                db.add(FeeCategory(name=name, fee_rate_range=rate_range, success_fee_rate=rate))

        # 初始化系统设置
        setting = db.query(SystemSettings).filter(SystemSettings.key == "cny_per_zar").first()
        if not setting:
            db.add(SystemSettings(key="cny_per_zar", value="0.41"))
        inventory_start = db.query(SystemSettings).filter(SystemSettings.key == "inventory_start_date").first()
        if not inventory_start:
            db.add(SystemSettings(key="inventory_start_date", value="2026-09-24"))

        # 初始化默认品类映射规则
        rule_count = db.query(FeeMappingRule).count()
        if rule_count == 0:
            default_rules = [
                ("Computers", "Computers & Laptops", 100),
                ("Computer Components", "Computer Components", 100),
                ("TV & Audio", "TV & Audio", 100),
                ("Cameras", "Cameras", 100),
                ("Small Appliances", "Small Appliances", 90),
                ("Large Appliances", "Large Appliances", 90),
                ("Sport", "Sport", 90),
                ("Camping", "Camping & Outdoor", 90),
                ("Beauty", "Beauty", 90),
                ("Health", "Health", 90),
                ("Baby", "Baby", 90),
                ("Toys", "Toys", 90),
                ("Books", "Books", 90),
                ("Pets", "Pets", 90),
                ("Liquor", "Liquor", 90),
                ("Garden", "Garden, Pool & Patio", 90),
                ("Stationery", "Stationery", 90),
                ("Office", "Office", 90),
                ("Musical Instruments", "Musical Instruments", 90),
                ("Homeware", "Homeware", 80),
                ("Clothing", "Clothing & Footwear", 80),
                ("Fashion", "Clothing & Footwear", 80),
                ("Luggage", "Luggage & Travel", 80),
                ("DIY", "DIY & Automotive", 80),
                ("Automotive", "DIY & Automotive", 80),
                ("Electronic Accessories", "Electronic Accessories", 80),
                ("Music", "Music & DVD", 70),
                ("Games", "Games", 70),
                ("Smart Home", "Smart Home & Connected Living", 70),
                ("Drives & Storage", "Computer Components", 60),
                ("Data Storage", "Computer Components", 60),
            ]
            for pattern, category, priority in default_rules:
                db.add(FeeMappingRule(
                    takealot_category_pattern=pattern,
                    fee_category=category,
                    priority=priority,
                    active=True,
                ))

        db.commit()
    finally:
        db.close()


from pydantic import BaseModel
from typing import List, Dict, Any

class MigrationPayload(BaseModel):
    table: str
    rows: List[Dict[str, Any]]

import uuid

@app.post("/api/migrate")
def bulk_migrate(data: List[MigrationPayload], db=Depends(get_db), store_id: str = Depends(store_routes.get_store_id)):
    """将本地数据库数据批量迁移到 Railway"""
    import sqlalchemy as sa
    from database import engine as raw_engine
    results = {}
    with raw_engine.begin() as conn:
        # 删除阶段临时关闭外键约束，避免 procutement_records 引用 products 导致 DELETE 被阻塞
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        # 仅清空本次 payload 涉及的表，避免误删未同步的表（如只同步采购记录时不碰 products）
        payload_tables = {p.table for p in data}
        # 按外键依赖顺序清空：先删 scrape_logs、procurement_records（均 FK->products），再删 products
        ordered_deletes = ["scrape_logs", "procurement_records", "products", "fee_mapping_rules", "fee_categories", "system_settings"]
        for table_name in ordered_deletes:
            if table_name not in payload_tables:
                continue
            try:
                if table_name in {"products", "procurement_records"}:
                    conn.execute(sa.text(f"DELETE FROM {table_name} WHERE store_id = :store_id"), {"store_id": store_id})
                else:
                    conn.execute(sa.text(f"DELETE FROM {table_name}"))
            except Exception:
                pass  # 表可能不存在
        for payload in data:
            if not payload.rows:
                continue
            table_name = payload.table
            # 查询目标表实际存在的列
            existing_cols = set()
            col_rows = conn.execute(sa.text(f"PRAGMA table_info({table_name})")).fetchall()
            for r in col_rows:
                existing_cols.add(r[1])  # name 列在第二列
            count = 0
            for row in payload.rows:
                if table_name == "system_settings" and "id" not in row:
                    row["id"] = uuid.uuid4().hex
                if table_name in {"products", "procurement_records"}:
                    row["store_id"] = store_id
                # 仅保留目标表实际存在的列（过滤掉本地多出的字段如 unit_price_cny）
                filtered = {k: v for k, v in row.items() if k in existing_cols}
                columns = list(filtered.keys())
                placeholders = ", ".join([f":{c}" for c in columns])
                cols = ", ".join(columns)
                sql = f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})"
                conn.execute(sa.text(sql), filtered)
                count += 1
            results[table_name] = count
    return {"status": "ok", "imported": results}


@app.get("/debug/scraper")
async def debug_scraper():
    info = {"playwright_avail": False, "playwright_error": None,
            "curl_cffi_avail": False, "scraper_test": None}
    try:
        from playwright.async_api import async_playwright
        info["playwright_avail"] = True
    except ImportError as e:
        info["playwright_error"] = str(e)
    try:
        import curl_cffi
        info["curl_cffi_avail"] = True
    except ImportError as e:
        info["curl_cffi_error"] = str(e)
    from services.takealot_scraper import scrape_product
    result = await scrape_product("https://www.takealot.com/bmw-f30-m4-mirror-covers/PLID90630735")
    info["scraper_test"] = {k: result.get(k) for k in
        ["success", "actual_sale_price_zar", "competing_sellers_count", "review_count", "rating_value", "warnings"]}
    return info


@app.get("/")
def root(request: Request):
    # build 字段用于确认「线上到底跑的是哪个提交」。Railway 部署时会注入
    # RAILWAY_GIT_COMMIT_SHA；之前判断后端有没有重新部署只能靠新增的 OpenAPI 字段，
    # 或者让用户去控制台看，容易各说各话。这里直接把自己的提交号报出来。
    import os
    from pathlib import Path

    # 网页版也挂在这个域名下，和健康检查共用根路径：
    # 浏览器（Accept 带 text/html）拿页面，curl / 监控脚本继续拿 JSON。
    # 把健康检查挪到别的路径会打挂现有监控，所以按 Accept 分流。
    index = Path(os.environ.get('FRONTEND_DIST')
                 or (Path(__file__).resolve().parent / 'dist')) / 'index.html'
    if index.is_file() and 'text/html' in request.headers.get('accept', ''):
        from fastapi.responses import FileResponse
        return FileResponse(index, headers={'Cache-Control': 'no-cache, must-revalidate'})

    sha = (os.environ.get('RAILWAY_GIT_COMMIT_SHA') or os.environ.get('GIT_COMMIT_SHA') or '').strip()
    return {"message": "Takealot 选品与利润测算系统 API", "version": "1.0.0", "build": sha[:12] or "unknown"}


@app.on_event('startup')
def start_operations_sync():
    from services.seller_operations_sync import start_worker
    start_worker()
    from services.automatic_pricing import start_worker as start_pricing
    start_pricing()
    from services.buybox_monitor import start_worker as start_monitor
    start_monitor()
    # 参考价每天重读一次官网。用户电脑不常开，所以调度必须在服务端，
    # 不能指望本地脚本或 GitHub Actions（仓库里的定时 workflow 已被删除）。
    from services.market_price_sync import start_worker as start_market_price
    start_market_price()


@app.on_event('shutdown')
def stop_operations_sync():
    from services.seller_operations_sync import stop_worker
    stop_worker()
    from services.automatic_pricing import stop_worker as stop_pricing
    stop_pricing()
    from services.buybox_monitor import stop_worker as stop_monitor
    stop_monitor()
    from services.market_price_sync import stop_worker as stop_market_price
    stop_market_price()


@app.on_event('shutdown')
async def close_image_proxy_client():
    from api import image_proxy
    await image_proxy.aclose_service()


# ---- 网页版：同源托管前端构建产物 ----
# 为什么放在后端：GitHub Pages 在部分网络下不可达（系统代理对 *.github.io 直接
# 断链），而 Railway 这个域名本来就在用。把构建产物一起打进镜像，网页版与 API
# 同源，既不用再依赖第三方静态托管，也顺带省掉跨域和 API 地址配置。
# dist 不存在时（只跑后端 / 本地开发）整段跳过，行为与之前完全一致。
from pathlib import Path as _Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_DIST = _Path(os.environ.get('FRONTEND_DIST') or (_Path(__file__).resolve().parent / 'dist')).resolve()

if (_DIST / 'index.html').is_file():
    if (_DIST / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=str(_DIST / 'assets')), name='assets')

    @app.get('/{full_path:path}', include_in_schema=False)
    async def serve_spa(full_path: str):
        # /api 走上面的路由；能落到这里说明这个接口根本不存在，必须 404。
        # 若在这里回 index.html，前端会把 HTML 当 JSON 解析，
        # 报出来的错跟真正的原因完全对不上。
        if full_path == 'api' or full_path.startswith('api/'):
            raise HTTPException(status_code=404, detail='Not Found')
        if full_path:
            candidate = (_DIST / full_path).resolve()
            # 防目录穿越：必须仍在 dist 内，否则 ../ 能读到服务器上的任意文件
            if _DIST in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        # index.html 引用的是带 hash 的资源名，所以它自己不能被缓存住，
        # 否则发版后浏览器还在用旧 index 去拉已经删掉的旧 JS。
        return FileResponse(_DIST / 'index.html',
                            headers={'Cache-Control': 'no-cache, must-revalidate'})
