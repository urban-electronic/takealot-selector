"""Fresh-database boot regression: embedded dump must import cleanly.

2026-10-15 local repro: on a brand-new database the backend aborted during
startup because migrate_from_dump() inserted products rows whose dump
predates later NOT NULL columns (store_id from multi-store support,
is_archived from archiving; ORM defaults do not apply to the raw sqlite3
INSERT). Any fresh deployment, new volume or first local install hit
this. The migration now backfills missing NOT NULL columns for dump rows.
"""
import sqlite3

from sqlalchemy import create_engine

from database import Base
from models import DEFAULT_STORE_ID


def _fresh_schema(path):
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    engine.dispose()


def test_migrate_from_dump_imports_products_with_store_id(tmp_path, monkeypatch):
    import migrate

    db_file = tmp_path / 'fresh.db'
    _fresh_schema(db_file)
    monkeypatch.setattr(migrate, 'DB_PATH', str(db_file))

    migrate.migrate_from_dump()  # must not raise on a fresh database

    conn = sqlite3.connect(str(db_file))
    total, missing = conn.execute(
        "SELECT COUNT(*), SUM(store_id IS NULL) FROM products").fetchone()
    wrong = conn.execute(
        "SELECT COUNT(*) FROM products WHERE store_id != ?", (DEFAULT_STORE_ID,)).fetchone()[0]
    archived = conn.execute(
        "SELECT COUNT(*) FROM products WHERE is_archived IS NULL").fetchone()[0]
    conn.close()
    assert total > 0, "embedded dump should import products on a fresh database"
    assert missing == 0 and wrong == 0, "every imported product must carry the default store id"
    assert archived == 0, "is_archived must be backfilled, not left NULL"


def test_migrate_from_dump_is_idempotent(tmp_path, monkeypatch):
    import migrate

    db_file = tmp_path / 'fresh.db'
    _fresh_schema(db_file)
    monkeypatch.setattr(migrate, 'DB_PATH', str(db_file))

    migrate.migrate_from_dump()
    migrate.migrate_from_dump()  # second run must skip, not duplicate

    conn = sqlite3.connect(str(db_file))
    total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    conn.close()
    assert total > 0
