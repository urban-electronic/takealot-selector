"""Read-only official sales/shipment snapshots. No platform or inventory writes."""
import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.exc import IntegrityError

from database import Base
from models import DEFAULT_STORE_ID, Store
from services.takealot_seller_api import configured_key

SOURCE = 'https://marketplace-api.takealot.com/v1'


class LedgerError(ValueError):
    pass


class SellerLedgerBinding(Base):
    __tablename__ = 'seller_ledger_bindings'
    store_id = Column(String, ForeignKey('stores.id'), primary_key=True)
    seller_id = Column(String, nullable=False)


class SellerLedgerSnapshot(Base):
    __tablename__ = 'seller_ledger_snapshots'
    __table_args__ = (UniqueConstraint('store_id', 'resource', name='ux_seller_ledger_store_resource'),)
    id = Column(Integer, primary_key=True)
    store_id = Column(String, ForeignKey('stores.id'), nullable=False)
    resource = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=0)
    seller_id = Column(String, nullable=False)
    synced_at = Column(String, nullable=False)
    period_from = Column(String, nullable=False, default='')
    period_to = Column(String, nullable=False, default='')
    payload = Column(Text, nullable=False)


def integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LedgerError(f'{label}缺失或无效，保留上次完整数据')
    return value


def identity(value, label):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'\d+', str(value)) or int(value) <= 0:
        raise LedgerError(f'{label}缺失或无效，保留上次完整数据')
    return str(value)


def number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise LedgerError('金额无效，保留上次完整数据')
    return value


def normalize(resource, row):
    if not isinstance(row, dict):
        raise LedgerError('明细不是对象')
    if resource == 'sales':
        raw = row.get('order_date')
        if not isinstance(raw, str):
            raise LedgerError('订单日期缺失')
        stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise LedgerError('订单日期缺少时区')
        sku = row.get('sku')
        status = row.get('sale_status')
        if not isinstance(sku, str) or not sku.strip() or not isinstance(status, str) or not status.strip():
            raise LedgerError('订单SKU或状态缺失')
        return dict(order_item_id=identity(row.get('order_item_id'), '订单行编号'),
                    order_id=identity(row.get('order_id'), '订单编号'),
                    order_date=stamp.isoformat(), business_date=stamp.astimezone(ZoneInfo('Africa/Johannesburg')).date().isoformat(),
                    sku=sku.strip(), offer_id=identity(row.get('offer_id'), '报价编号'),
                    quantity=integer(row.get('quantity'), '订单数量'), sale_status=status,
                    selling_price=number(row.get('selling_price')), total_fees=number(row.get('total_fees')))
    items = row.get('shipment_items')
    if not isinstance(items, list):
        raise LedgerError('货件明细未完整展开')
    normalized = []
    seen = set()
    for item in items:
        item_id = identity(item.get('shipment_item_id'), '货件行编号')
        if item_id in seen:
            raise LedgerError('货件行重复')
        seen.add(item_id)
        normalized.append(dict(shipment_item_id=item_id, offer_id=identity(item.get('offer_id'), '报价编号'),
                               quantity_sending=integer(item.get('quantity_sending'), '发运数量'),
                               quantity_received=integer(item.get('purchase_order_quantity_received'), '实收数量'),
                               quantity_damaged=integer(item.get('purchase_order_quantity_damaged'), '破损数量')))
    for flag in ('shipped', 'cancelled'):
        if not isinstance(row.get(flag), bool):
            raise LedgerError('货件状态缺失')
    return dict(shipment_id=identity(row.get('shipment_id'), '货件编号'),
                reference=str(row.get('reference') or ''), shipped=row['shipped'], cancelled=row['cancelled'],
                due_date=str(row.get('due_date') or ''), date_unloaded=row.get('date_unloaded'),
                destination_region=str(row.get('destination_region') or ''), items=normalized)


def read_complete(client, key, resource, params, deadline):
    """Continuation tokens are opaque query values, never URLs. No partial publish."""
    rows, identities, tokens = [], set(), set()
    expected = None
    current = {**params, 'limit': 100, 'include_count': 'true'}
    for _ in range(100):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LedgerError('同步超过75秒预算，保留上次完整数据；请缩小日期范围')
        response = client.get(f'{SOURCE}/{resource}', params=current,
                              headers={'X-API-Key': key}, timeout=min(10, remaining))
        if response.status_code != 200:
            raise LedgerError(f'卖家接口HTTP {response.status_code}，未保存不完整数据')
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise LedgerError('分页格式异常，未保存不完整数据')
        if expected is None and 'count' in payload:
            expected = integer(payload['count'], '总条数')
        for raw in payload['items']:
            row = normalize(resource, raw)
            rid = row['order_item_id' if resource == 'sales' else 'shipment_id']
            if rid in identities:
                raise LedgerError('分页出现重复身份，未保存不完整数据')
            identities.add(rid)
            rows.append(row)
        token = payload.get('continuation_token')
        if token is None or token == '':
            if expected is not None and expected != len(rows):
                raise LedgerError('分页总数不一致，未保存不完整数据')
            return rows
        if not isinstance(token, str) or token in tokens or not payload['items']:
            raise LedgerError('分页游标重复或无进展')
        tokens.add(token)
        current = {'continuation_token': token}
    raise LedgerError('超过100页，未保存不完整数据；请缩小日期范围')


def sync(db, store_id, resource, start=None, end=None):
    if store_id != DEFAULT_STORE_ID:
        raise HTTPException(409, '当前店铺尚未绑定独立卖家接口，不能使用其他店铺数据')
    now = datetime.now(ZoneInfo('Africa/Johannesburg')).date()
    start, end = start or now - timedelta(days=29), end or now
    if resource == 'sales' and (start > end or end > now or (end - start).days > 30):
        raise HTTPException(400, '请选择不超过31天且不晚于今天的南非订单日期范围')
    saved = db.query(SellerLedgerSnapshot).filter_by(store_id=store_id, resource=resource).first()
    old_version = saved.version if saved else None
    binding = db.get(SellerLedgerBinding, store_id)
    key = configured_key(db)
    if not key:
        raise HTTPException(409, '当前店铺未配置卖家API')
    store = db.get(Store, store_id)
    try:
        deadline = time.monotonic() + 75
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get(f'{SOURCE}/seller', headers={'X-API-Key': key}, timeout=10)
            if response.status_code != 200:
                raise LedgerError(f'卖家身份接口HTTP {response.status_code}')
            seller_id = identity(response.json().get('seller_id'), '卖家身份')
            if binding and binding.seller_id != seller_id or saved and saved.seller_id != seller_id:
                raise LedgerError('卖家身份已变化，拒绝覆盖原店铺数据')
            if store and store.external_store_ref and store.external_store_ref != seller_id:
                raise LedgerError('卖家身份与店铺绑定不一致')
            params = ({'order_date__gte': start.isoformat(), 'order_date__lte': end.isoformat()}
                      if resource == 'sales' else {'expands': 'shipment_items'})
            rows = read_complete(client, key, resource, params, deadline)
        if resource == 'sales' and any(not start.isoformat() <= r['business_date'] <= end.isoformat() for r in rows):
            raise LedgerError('上游返回范围外订单，拒绝标记完整')
        values = dict(seller_id=seller_id, version=(old_version or 0) + 1,
                      synced_at=datetime.now(timezone.utc).isoformat(),
                      period_from=start.isoformat() if resource == 'sales' else '',
                      period_to=end.isoformat() if resource == 'sales' else '',
                      payload=json.dumps(rows, ensure_ascii=False))
        if not binding:
            db.add(SellerLedgerBinding(store_id=store_id, seller_id=seller_id))
        if saved:
            changed = db.query(SellerLedgerSnapshot).filter_by(id=saved.id, version=old_version).update(values)
            if changed != 1:
                raise HTTPException(409, '另一同步已完成，请刷新后再试')
        else:
            db.add(SellerLedgerSnapshot(store_id=store_id, resource=resource, **values))
        db.commit()
        return {'ok': True, 'count': len(rows), 'source': SOURCE, **{k: v for k, v in values.items() if k != 'payload'}}
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '另一同步已完成，请刷新后再试') from None
    except (ValueError, httpx.HTTPError, TypeError, AttributeError) as exc:
        db.rollback()
        # Only our fixed validation diagnostics may leave the boundary.
        message = str(exc) if isinstance(exc, LedgerError) else '卖家接口读取失败，保留上次完整数据'
        raise HTTPException(502, message) from None
