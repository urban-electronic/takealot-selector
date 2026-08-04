"""
启动时从 dump.sql 迁移数据到 Railway 数据库
"""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'takealot_selector.db')
DUMP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dump.sql')


def migrate_from_dump():
    """如果数据库是空的，从 dump.sql 导入数据"""
    if not os.path.exists(DUMP_PATH):
        logger.info("dump.sql not found, skipping migration")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        # 检查是否已有数据（排除系统初始化的默认数据）
        count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count > 0:
            logger.info(f"Database already has {count} products, skipping migration")
            return

        # 执行 dump
        logger.info("Importing data from dump.sql...")
        with open(DUMP_PATH, 'r', encoding='utf-8') as f:
            sql = f.read()
        conn.executescript(sql)
        conn.commit()

        new_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        logger.info(f"Migration complete: {new_count} products imported")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        conn.close()
