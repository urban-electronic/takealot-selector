# -*- coding: utf-8 -*-
"""标签接口回归：一个 SKU 一个文件，多变体产品按 SKU 拆开。

背景：多规格产品的 sku 字段是「空格拼起来的一整串」（如 "9902348621873 9902348623136"），
前端勾选时拿到的是**单个 SKU**。修复前后端用 `Product.sku.in_(wanted)` 精确匹配，
一整串永远匹配不上 → 点下载直接 404；即使查到了，整串拿去校验 EAN-13 也必然失败。
"""

import io
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.pdfmeta import page_count, page_sizes

from api.product_routes import LabelsRequest, download_labels
from database import Base
from models import DEFAULT_STORE_ID, Product

SKU_A = '9902348621873'
SKU_B = '9902348623136'
SKU_BAD = '9902348621874'  # 校验位错


@pytest.fixture()
def Session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/labels.db', connect_args={'timeout': 30})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(Session, sku, name='Test Product', pid='p-multi'):
    db = Session()
    db.add(Product(id=pid, store_id=DEFAULT_STORE_ID, sku=sku, product_name=name, is_archived=False))
    db.commit()
    db.close()
    return pid


def _count(resp):
    return int(resp.headers['X-Label-Count'])


def _page_mm(pdf_bytes):
    width, height = page_sizes(pdf_bytes)[0]
    return round(width / 72 * 25.4, 1), round(height / 72 * 25.4, 1), page_count(pdf_bytes)


def test_product_id_expands_every_sku(Session):
    """按产品 id 下载：2 个 SKU → 2 个文件，打包成 zip。"""
    pid = _seed(Session, f'{SKU_A} {SKU_B}')
    resp = download_labels(LabelsRequest(product_ids=[pid]), db=Session(), store_id=DEFAULT_STORE_ID)
    assert resp.media_type == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
        assert sorted(zf.namelist()) == [f'{SKU_A}.pdf', f'{SKU_B}.pdf']


def test_single_sku_returns_plain_pdf(Session):
    """只有一个 SKU 时直接给 PDF，不套一层 zip。"""
    pid = _seed(Session, SKU_A)
    resp = download_labels(LabelsRequest(product_ids=[pid]), db=Session(), store_id=DEFAULT_STORE_ID)
    assert resp.media_type == 'application/pdf'
    assert _page_mm(resp.body)[:2] == (40.0, 30.0)
    assert _page_mm(resp.body)[2] == 1


def test_single_variant_sku_from_list_page(Session):
    """列表页勾的是单个 SKU，也得能查到并只出那一个文件。"""
    _seed(Session, f'{SKU_A} {SKU_B}')
    resp = download_labels(LabelsRequest(skus=[SKU_B]), db=Session(), store_id=DEFAULT_STORE_ID)
    assert resp.media_type == 'application/pdf'
    assert _count(resp) == 1


def test_full_sku_string_still_works(Session):
    """老调用方传整串 sku 字段：拆开后每个 SKU 一个文件。"""
    _seed(Session, f'{SKU_A} {SKU_B}')
    resp = download_labels(
        LabelsRequest(skus=[f'{SKU_A} {SKU_B}']), db=Session(), store_id=DEFAULT_STORE_ID
    )
    assert resp.media_type == 'application/zip'
    assert _count(resp) == 2


def test_bad_sku_is_skipped_but_others_kept(Session):
    """一个 SKU 条码不合法，不影响其它 SKU 出标签。"""
    pid = _seed(Session, f'{SKU_A} {SKU_BAD}')
    resp = download_labels(LabelsRequest(product_ids=[pid]), db=Session(), store_id=DEFAULT_STORE_ID)
    assert _count(resp) == 1
    assert resp.headers['X-Label-Skipped'] == '1'
    assert resp.media_type == 'application/pdf'  # 只剩一个合法的


def test_copies_applies_per_sku(Session):
    """copies=3 时每个 SKU 各出 3 张，但仍是各自一个文件。"""
    pid = _seed(Session, f'{SKU_A} {SKU_B}')
    resp = download_labels(
        LabelsRequest(product_ids=[pid], copies=3), db=Session(), store_id=DEFAULT_STORE_ID
    )
    assert _count(resp) == 6
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
        for name in zf.namelist():
            assert _page_mm(zf.read(name))[2] == 3


def test_sheet_mode_is_still_a4(Session):
    """mode='sheet' 保留 A4 拼版，给想用普通打印机的人。"""
    pid = _seed(Session, f'{SKU_A} {SKU_B}')
    resp = download_labels(
        LabelsRequest(product_ids=[pid], mode='sheet'), db=Session(), store_id=DEFAULT_STORE_ID
    )
    assert resp.media_type == 'application/pdf'
    w, h, pages = _page_mm(resp.body)
    assert (round(w), round(h), pages) == (210, 297, 1)


def test_no_sku_product_is_rejected(Session):
    """没有 SKU 的产品直接报错，不返回空 PDF。"""
    pid = _seed(Session, '')
    with pytest.raises(Exception) as err:
        download_labels(LabelsRequest(product_ids=[pid]), db=Session(), store_id=DEFAULT_STORE_ID)
    assert '400' in str(err.value) or '没有' in str(err.value)


def test_other_store_cannot_download(Session):
    """跨店铺拿不到别人的产品。"""
    _seed(Session, SKU_A)
    with pytest.raises(Exception):
        download_labels(LabelsRequest(product_ids=['p-multi']), db=Session(), store_id='other-store')
