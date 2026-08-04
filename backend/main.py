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
from api import product_routes, scraper_routes, category_routes, settings_routes, image_proxy
from migrate import migrate_from_dump

app = FastAPI(title="Takealot 选品与利润测算系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(product_routes.router)
app.include_router(scraper_routes.router)
app.include_router(category_routes.router)
app.include_router(settings_routes.router)
app.include_router(image_proxy.router)


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
    migrate_from_dump()
    _init_default_data()


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

@app.post("/api/migrate")
def bulk_migrate(data: List[MigrationPayload], db=Depends(get_db)):
    """将本地数据库数据批量迁移到 Railway"""
    import sqlalchemy as sa
    from database import engine as raw_engine
    results = {}
    with raw_engine.begin() as conn:
        for payload in data:
            if not payload.rows:
                continue
            table_name = payload.table
            # 先清空
            conn.execute(sa.text(f"DELETE FROM {table_name}"))
            # 逐行插入
            count = 0
            for row in payload.rows:
                columns = list(row.keys())
                placeholders = ", ".join([f":{c}" for c in columns])
                cols = ", ".join(columns)
                sql = f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})"
                conn.execute(sa.text(sql), row)
                count += 1
            results[table_name] = count
    return {"status": "ok", "imported": results}


@app.get("/")
def root():
    return {"message": "Takealot 选品与利润测算系统 API", "version": "1.0.0"}
