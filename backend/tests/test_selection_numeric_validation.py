"""Selection PATCH/bulk validation must prevent negative-cost calculations."""
import pytest
from pydantic import ValidationError
from api.selection_routes import ItemPatch, BulkPatch

POSITIVE = ('length_mm', 'width_mm', 'height_mm', 'actual_weight_kg')
NONNEGATIVE = ('actual_sale_price_zar', 'purchase_cost_cny', 'purchase_shipping_cny',
               'unit_price_cny', 'packaging_cost_per_unit_cny')

@pytest.mark.parametrize('field', POSITIVE + NONNEGATIVE + ('purchase_quantity',))
def test_negative_inputs_rejected_by_single_and_bulk_contract(field):
    for model, payload in ((ItemPatch, {field: -0.5}),
                           (BulkPatch, {'ids': ['isolated-item'], 'patch': {field: -0.5}})):
        with pytest.raises(ValidationError):
            model.model_validate(payload)

@pytest.mark.parametrize('field', POSITIVE + ('purchase_quantity',))
def test_zero_physical_measurements_and_quantity_rejected(field):
    with pytest.raises(ValidationError):
        ItemPatch.model_validate({field: 0})

@pytest.mark.parametrize('field', POSITIVE + NONNEGATIVE)
@pytest.mark.parametrize('value', [float('inf'), float('-inf'), float('nan')])
def test_nonfinite_values_rejected(field, value):
    with pytest.raises(ValidationError):
        ItemPatch.model_validate({field: value})

@pytest.mark.parametrize('field', NONNEGATIVE)
def test_legitimate_zero_cost_preserved(field):
    assert ItemPatch.model_validate({field: 0}).model_dump(exclude_unset=True) == {field: 0}

@pytest.mark.parametrize('field', POSITIVE + NONNEGATIVE + ('purchase_quantity',))
def test_explicit_clear_still_supported(field):
    assert ItemPatch.model_validate({field: None}).model_dump(exclude_unset=True) == {field: None}

def test_fractional_purchase_quantity_rejected():
    with pytest.raises(ValidationError):
        ItemPatch.model_validate({'purchase_quantity': 1.5})

@pytest.mark.parametrize('bulk', [False, True])
def test_http_rejects_negative_weight_without_changing_saved_row(bulk):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from database import Base, get_db
    from api.store_routes import get_store_id
    from api.selection_routes import router
    from models import Store, SelectionItem, DEFAULT_STORE_ID
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(Store(id=DEFAULT_STORE_ID, name='isolated', status='active'))
        db.add(SelectionItem(id='isolated-item', store_id=DEFAULT_STORE_ID,
                             batch_id='isolated', product_name='Fixture', actual_weight_kg=0.35))
        db.commit()
    def database():
        with sessions() as db:
            yield db
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_store_id] = lambda: DEFAULT_STORE_ID
    client = TestClient(app)
    if bulk:
        response = client.post('/api/selection/items/bulk', json={'ids': ['isolated-item'], 'patch': {'actual_weight_kg': -0.5}})
    else:
        response = client.patch('/api/selection/items/isolated-item', json={'actual_weight_kg': -0.5})
    assert response.status_code == 422
    with sessions() as db:
        assert db.get(SelectionItem, 'isolated-item').actual_weight_kg == 0.35
    engine.dispose()
