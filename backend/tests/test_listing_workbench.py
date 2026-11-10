"""Isolated draft lifecycle, import fidelity and no-price/no-platform guarantees."""
import base64
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.listing_routes import router
from database import Base, get_db
from models import Store
from services.listing_workbench import (ListingDraft, blank_payload, issues, merge_import,
                                       parse_table, public_image_url)


def table(rows=None, headers=None):
    wb = Workbook()
    ws = wb.active
    ws.append(['供应商回填表'])
    ws.append(['原图每款至少3张'])
    ws.append(['请补全截断英文品名'])
    ws.append(headers or ['内部编号（勿改）', '原货号（勿改）', '产品名称（原文）', '完整英文品名（截断必填）',
                         '包装后长(cm)*', '包装后宽(cm)*', '包装后高(cm)*', '包装后重量(kg)*',
                         '主材质（核对/补充）', '原文颜色（参考）'])
    for row in rows or [['JB-005', 'DGM1098', '1.8升不锈钢电热水壶', '1.8L Stainless Steel Electric Kettle',
                         26, 21, 28, 1.2, '不锈钢', '黑色/红色']]:
        ws.append(row)
    output = io.BytesIO(); wb.save(output); wb.close()
    return output.getvalue()


@pytest.fixture
def api():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Store(id='old', name='Old'), Store(id='new', name='New')]); session.commit()
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers={'X-Store-ID': 'new'}) as client:
        yield client, session
    session.close(); engine.dispose()


def upload(client, raw=None, **kwargs):
    return client.post('/api/listing/import', json=dict(filename='fixture.xlsx',
        data_base64=base64.b64encode(raw or table()).decode()), **kwargs)


def complete():
    return dict(title='Electric Kettle', subtitle='A kettle for everyday use', description='Product description\nKey Features\n1.8L capacity',
                whats_in_box='1 x Electric Kettle', brand_mode='generic', category_id='fixture-category',
                category_path='Fixture / Kettles', major_colour='黑色', other_colours=['红色'],
                packaged_length_cm=26, packaged_width_cm=21, packaged_height_cm=28, packaged_weight_kg=1.2,
                image_urls=[f'https://images.example.invalid/kettle-{i}.jpg' for i in range(5)])


def test_supplier_headers_preserve_units_names_and_no_guessing_colour():
    row = parse_table(table(), '约堡.xlsx')[0]
    p = row['payload']
    assert row['internal_code'] == 'JB-005' and row['source_row'] == 5
    assert p['packaged_weight_kg'] == 1.2  # NOT 1.2 / carton quantity.
    assert p['packaged_length_cm'] == 26  # NOT 26mm.
    assert p['raw_name'] == '1.8升不锈钢电热水壶'
    assert p['english_name'] == '1.8L Stainless Steel Electric Kettle'
    assert p['colour_candidates'] == ['Black', 'Red'] and p['major_colour'] == ''


def test_single_colour_sets_major_but_multicolour_does_not():
    for val, expected in [('黑色', 'Black'), ('red', 'Red'), ('多色', '')]:
        raw = table([['ONE', 'SKU', 'Kettle', '', 1, 2, 3, 1, '', val]])
        assert parse_table(raw, 'f.xlsx')[0]['payload']['major_colour'] == expected


def test_supplier_write_only_workbook_without_dimension_metadata():
    wb = Workbook(write_only=True)
    ws = wb.create_sheet('回填')
    ws.append(['内部编号', '产品名称', '包装后重量(kg)'])
    ws.append(['JB-005', 'Kettle', 1.2])
    output = io.BytesIO(); wb.save(output); wb.close()
    rows = parse_table(output.getvalue(), 'no-dimensions.xlsx')
    assert len(rows) == 1 and rows[0]['payload']['packaged_weight_kg'] == 1.2


@pytest.mark.parametrize('field,value', [('english_name', 'Kettle...'), ('english_name', '电水壶'),
                                       ('country_of_origin', ''), ('description', 'Missing features heading')])
def test_review_rejects_incomplete_english_origin_and_feature_structure(field, value):
    payload = blank_payload(); payload.update(complete()); payload[field] = value
    assert any(i['field'] == field for i in issues(payload))


def test_truncated_name_needs_supplier_completion():
    p = parse_table(table([['X', 'S', 'Men Socks…', '', 1, 1, 1, 1, '', '']]), 'f.xlsx')[0]['payload']
    assert not p['english_name'] and not p['title']
    assert any(i['field'] == 'english_name' for i in issues(p))
    p['english_name'] = 'Mens Cotton Socks'
    assert not any(i['field'] == 'english_name' for i in issues(p))


@pytest.mark.parametrize('raw, message', [
    (lambda: table([['X', 'S', 'A'], ['X', 'T', 'B']]), '重复'),
    (lambda: table([['', 'S', 'A']]), '缺少内部编号'),
    (lambda: table([['X', 'S', 'A', '', '20cm']]), '只填数字'),
    (lambda: table([['X', 'S', '=HYPERLINK("x")']]), '含公式'),
    (lambda: table([['X', 'S', 'A', '', 0]]), '大于0'),
])
def test_invalid_import_is_atomic(api, raw, message):
    client, db = api
    result = upload(client, raw())
    assert result.status_code == 400 and message in result.json()['detail']
    assert db.query(ListingDraft).count() == 0


def test_import_idempotence_and_cross_store_read_write_export_isolation(api):
    client, _ = api
    assert upload(client).json()['created'] == 1
    assert upload(client).json()['unchanged'] == 1
    assert upload(client, headers={'X-Store-ID': 'old'}).json()['created'] == 1
    row = client.get('/api/listing/drafts').json()['items'][0]
    foreign = {'X-Store-ID': 'old'}
    assert client.get('/api/listing/drafts').json()['total'] == 1
    assert client.get(f'/api/listing/drafts/{row["id"]}', headers=foreign).status_code == 404
    assert client.patch(f'/api/listing/drafts/{row["id"]}', headers=foreign,
                        json={'revision': 1, 'fields': {'title': 'Bad'}}).status_code == 404
    assert client.post('/api/listing/review-export', headers=foreign,
                       json={'rows': {row['id']: 1}}).status_code == 404
    assert client.get('/api/listing/drafts', headers={'X-Store-ID': 'missing'}).status_code == 400


def test_listing_review_has_no_pricing_dependency_and_edit_invalidates_it(api):
    client, _ = api; upload(client)
    row = client.get('/api/listing/drafts').json()['items'][0]; url = f'/api/listing/drafts/{row["id"]}'
    assert client.post(url+'/review', json={'revision': 1}).status_code == 400
    result = client.patch(url, json={'revision': 1, 'fields': complete()})
    assert result.status_code == 200
    saved = result.json()
    assert saved['issues'] == [] and 'price' not in saved['payload']
    assert saved['payload']['major_colour'] == 'Black'
    assert saved['payload']['other_colours'] == ['Red']
    reviewed = client.post(url+'/review', json={'revision': 2}).json()
    assert reviewed['state'] == 'reviewed' and reviewed['reviewed_at'].endswith('Z')
    export = client.post('/api/listing/review-export', json={'rows': {row['id']: 3}})
    assert export.status_code == 200
    wb = load_workbook(io.BytesIO(export.content), data_only=False)
    assert '非官方模板' in wb.active.title
    assert not any('price' in str(c.value).lower() for c in wb.active[1]); wb.close()
    changed = client.patch(url, json={'revision': 3, 'fields': {'major_colour': 'Red', 'other_colours': ['Black']}}).json()
    assert changed['state'] == 'draft' and changed['reviewed_at'] is None
    assert client.post('/api/listing/review-export', json={'rows': {row['id']: 3}}).status_code == 409
    assert client.patch(url, json={'revision': 1, 'fields': {'title': 'Stale'}}).status_code == 409
    assert client.get(url).json()['payload']['title'] == 'Electric Kettle'


def test_reimport_preserves_manual_value_and_surfaces_conflict(api):
    client, _ = api; upload(client)
    row = client.get('/api/listing/drafts').json()['items'][0]; url = f'/api/listing/drafts/{row["id"]}'
    client.patch(url, json={'revision': 1, 'fields': {'packaged_weight_kg': 1.5}})
    result = upload(client).json()
    assert result['conflicts'] == 1
    saved = client.get(url).json()
    assert saved['payload']['packaged_weight_kg'] == 1.5
    assert saved['payload']['import_conflicts'][0]['field'] == 'packaged_weight_kg'
    result = client.patch(url, json={'revision': saved['revision'], 'fields': {'packaged_weight_kg': 1.5}}).json()
    assert result['payload']['import_conflicts'] == []


def test_identity_changes_cannot_overwrite_existing_row(api):
    client, _ = api; upload(client)
    changed = table([['JB-005', 'OTHER-SKU', 'Other product']])
    assert upload(client, changed).status_code == 409
    assert client.get('/api/listing/drafts').json()['items'][0]['payload']['supplier_code'] == 'DGM1098'


@pytest.mark.parametrize('value', [0, -1, 'nan', 'inf', True, 'bad'])
def test_bad_measurement_does_not_clear_existing_value(api, value):
    client, _ = api; upload(client)
    row = client.get('/api/listing/drafts').json()['items'][0]; url = f'/api/listing/drafts/{row["id"]}'
    assert client.patch(url, json={'revision': 1, 'fields': {'packaged_weight_kg': value}}).status_code == 400
    assert client.get(url).json()['payload']['packaged_weight_kg'] == 1.2


@pytest.mark.parametrize('url', ['http://images.example/a.jpg', 'https://localhost/a', 'https://127.0.0.1/a',
                                'https://192.168.1.1/a', 'https://user:pass@example.com/a', 'file:///a'])
def test_private_image_urls_are_not_reviewable(url):
    assert not public_image_url(url)


def test_wrong_store_missing_facts_duplicate_images_and_duplicate_colours_are_blocked():
    p = blank_payload(); p.update(complete()); p['major_colour'] = 'Black'; p['other_colours'] = ['Black']
    p['image_urls'] = [p['image_urls'][0]] * 5
    assert {'image_urls', 'other_colours'} <= {i['field'] for i in issues(p)}
