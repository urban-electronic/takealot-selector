from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import DEFAULT_STORE_ID, Product, PackingProduct
from api import packing_routes as routes


def test_sync_repairs_missing_files_without_replacing_present_manual_images(tmp_path, monkeypatch):
    monkeypatch.setattr(routes.px, 'IMAGE_DIR', tmp_path / 'images')
    routes.px.IMAGE_DIR.mkdir()
    (routes.px.IMAGE_DIR / 'manual.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    engine = create_engine(f'sqlite:///{tmp_path}/repair.db')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        for sku, filename in [('MISSING', 'lost.png'), ('MANUAL', 'manual.png'), ('EMPTY', '')]:
            db.add(Product(id=sku, store_id=DEFAULT_STORE_ID, sku=sku,
                           product_name=sku, product_image_url=f'https://example.invalid/{sku}.png', is_archived=False))
            db.add(PackingProduct(sku=sku, image_file=filename))
        db.commit()
        fetched = []
        def fetch(url):
            fetched.append(url)
            return url.rsplit('/', 1)[-1]
        monkeypatch.setattr(routes, '_fetch_takealot_image', fetch)
        result = routes.sync_images_from_products(routes.SyncImagesIn(), db=db, store_id=DEFAULT_STORE_ID)
        assert result['updated'] == 2
        assert result['total_pending'] == 2
        assert fetched == ['https://example.invalid/EMPTY.png', 'https://example.invalid/MISSING.png']
        assert db.query(PackingProduct).filter_by(sku='MANUAL').one().image_file == 'manual.png'
        assert db.query(PackingProduct).filter_by(sku='MISSING').one().image_file == 'MISSING.png'


def test_export_hydrates_a_stale_image_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(routes.px, 'IMAGE_DIR', tmp_path)
    source = SimpleNamespace(actual_weight_kg=None, sku='ONLY', product_image_url='https://example.invalid/only.png')
    monkeypatch.setattr(routes, '_catalog_product_for_sku', lambda *args: source)
    monkeypatch.setattr(routes, '_fetch_takealot_image', lambda url: 'restored.png')
    product = SimpleNamespace(sku='ONLY', image_file='missing.png')
    routes._hydrate_export_product(None, product, DEFAULT_STORE_ID)
    assert product.image_file == 'restored.png'


def test_group_cover_does_not_claim_branch_colour():
    group = SimpleNamespace(sku='BLACK WHITE', product_image_url='https://example/black.jpg')
    white = SimpleNamespace(sku='WHITE', product_image_url='https://example/white.jpg')
    assert routes._catalog_images_by_sku([group]) == {'BLACK': group.product_image_url}
    for rows in ([white, group], [group, white]):
        assert routes._catalog_images_by_sku(rows)['WHITE'] == white.product_image_url


def test_list_and_sync_do_not_copy_cover_to_branch(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/images.db')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Product(id='colours', store_id=DEFAULT_STORE_ID, sku='BLACK WHITE',
                       product_name='Socket', product_image_url='https://example/black.jpg', is_archived=False))
        db.add_all([PackingProduct(sku='BLACK', image_file=''), PackingProduct(sku='WHITE', image_file='')])
        db.commit()
        rows = {r['sku']: r for r in routes.list_products(db=db, store_id=DEFAULT_STORE_ID)}
        assert rows['BLACK']['source_image_url'].endswith('/black.jpg')
        assert rows['WHITE']['source_image_url'] == ''
        fetched = []
        def fetch(url):
            fetched.append(url)
            return 'black.png'
        monkeypatch.setattr(routes, '_fetch_takealot_image', fetch)
        result = routes.sync_images_from_products(routes.SyncImagesIn(), db=db, store_id=DEFAULT_STORE_ID)
        assert result['updated'] == 1
        assert fetched == ['https://example/black.jpg']
        assert db.query(PackingProduct).filter_by(sku='WHITE').one().image_file == ''
        branch = db.query(PackingProduct).filter_by(sku='WHITE').one()
        routes._hydrate_export_product(db, branch, DEFAULT_STORE_ID)
        assert branch.image_file == ''
        assert fetched == ['https://example/black.jpg']
