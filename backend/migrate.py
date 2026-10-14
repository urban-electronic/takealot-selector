"""
启动时将内置数据迁移到 Railway 数据库
"""
import sqlite3
import logging
import os

logger = logging.getLogger(__name__)

# 与 database.py 保持一致的数据库路径（支持 Railway 卷挂载）
import database as _database
DB_PATH = os.path.join(_database.DB_DIR, 'takealot_selector.db')


def _column_default(col: str, col_type: str):
    """dump 行缺失的 NOT NULL 列的兜底值。

    store_id 回填默认店铺（语义默认）；布尔/数值列回填 0；其余回填空串。
    旧版迁移数据早于多店铺与归档改造，行内不带这些列，而 ORM default
    不作用于裸 INSERT，曾导致全新数据库启动崩溃（2026-10-15 实测复现）。
    """
    if col == "store_id":
        from models import DEFAULT_STORE_ID
        return DEFAULT_STORE_ID
    t = (col_type or "").upper()
    if "BOOL" in t:
        return 0
    if "INT" in t or "REAL" in t or "FLOA" in t or "DOUB" in t or "NUM" in t:
        return 0
    return ""


def migrate_from_dump():
    """如果数据库是空的，从内嵌数据导入"""
    from migration_data import MIGRATION_DATA

    conn = sqlite3.connect(DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count > 0:
            logger.info(f"Database already has {count} products, skipping migration")
            return

        logger.info("Importing data from embedded dataset...")
        for table_name, rows in MIGRATION_DATA.items():
            if not rows:
                continue
            # 兼容旧版迁移数据：补齐 dump 行中缺失的 NOT NULL 列（见 _column_default）
            table_cols = {r[1]: r for r in conn.execute(f"PRAGMA table_info({table_name})")}
            for r in rows:
                for col, info in table_cols.items():
                    if info[3] and col not in r:  # NOT NULL 且 dump 行缺失
                        r[col] = _column_default(col, info[2])
            # 清空已建的空表
            conn.execute(f"DELETE FROM {table_name}")
            # 逐行插入
            cols = list(rows[0].keys())
            placeholders = ", ".join(["?" for _ in cols])
            col_str = ", ".join(cols)
            sql = f"INSERT INTO {table_name} ({col_str}) VALUES ({placeholders})"
            conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])

        conn.commit()
        new_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        logger.info(f"Migration complete: {new_count} products imported")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        conn.close()
