from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import DEFAULT_STORE_ID, Product, PackingProduct
from api import packing_routes as routes


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
