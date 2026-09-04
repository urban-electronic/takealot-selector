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
