"""
Takealot 选品系统 - 主入口
"""

import os
import sys

# 确保 backend 目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base, SessionLocal, get_db
from models import FeeCategory, FeeMappingRule, SystemSettings
from api import product_routes, scraper_routes, category_routes, settings_routes, image_proxy, procurement_routes, packing_routes
from migrate import migrate_from_dump

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


@app.middleware("http")
async def api_key_check(request: Request, call_next):
    # CORS 预检（OPTIONS）不带 X-API-Key，必须放行交给 CORSMiddleware 处理，
    # 否则浏览器跨域请求 preflight 401 导致 "Failed to fetch"
    if request.method == "OPTIONS":
        return await call_next(request)
    if _API_KEY and request.url.path.startswith("/api"):
        key = request.headers.get("X-API-Key", "")
        if key != _API_KEY:
            return JSONResponse(
                status_code=401,
                content={"detail": "无效或缺失 API Key，请在设置页填写"},
            )
    return await call_next(request)

# 注册路由
app.include_router(product_routes.router)
app.include_router(scraper_routes.router)
app.include_router(category_routes.router)
app.include_router(settings_routes.router)
app.include_router(image_proxy.router)
app.include_router(procurement_routes.router)
app.include_router(packing_routes.router)
app.include_router(packing_routes.ui_router)


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
    migrate_from_dump()
    _init_default_data()


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
    except Exception as e:
        print(f"[startup] _ensure_columns 迁移失败: {e}", flush=True)


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
def bulk_migrate(data: List[MigrationPayload], db=Depends(get_db)):
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
def root():
    return {"message": "Takealot 选品与利润测算系统 API", "version": "1.0.0"}
