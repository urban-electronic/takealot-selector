"""Retrospective manifests: replace explicit snapshots and allocate real domestic stock.

No procurement, on-hand overseas stock, or platform transaction is created.
"""
import datetime as dt
import hashlib
import json
import uuid
from collections import defaultdict

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from models import Shipment, ShipmentLine, ShipmentAllocation, Store, SystemSettings, PackingProduct
from services.operation_log import record_operation


class ManifestLine(BaseModel):
    date: str
    shipping: str
    box: str = Field(min_length=1, max_length=80)
    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(strict=True, ge=1, le=1000000)


class ReconcileIn(BaseModel):
    lines: list[ManifestLine] = Field(min_length=1, max_length=1000)
    replace_ids: list[str] = Field(default_factory=list, max_length=500)
    token: str = ''


def _canonical(data):
    lines = sorted([x.model_dump() for x in data.lines], key=lambda x: (x['date'], x['shipping'], x['box'], x['sku']))
    seen = set()
    for x in lines:
        try:
            if dt.date.fromisoformat(x['date']).isoformat() != x['date']:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, '发货日期必须为 YYYY-MM-DD')
        if x['date'] > dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat():
            raise HTTPException(400, '已发货补登不能使用未来日期')
        if x['shipping'] not in ('空运', '海运'):
            raise HTTPException(400, '运输方式必须为空运或海运')
        key = (x['date'], x['shipping'], x['box'], x['sku'])
        if key in seen:
            raise HTTPException(400, '同一箱的 SKU 不能重复，请合并数量')
        seen.add(key)
    return lines


def preview(db, store_id, data):
    from api.inventory_routes import inventory_balance_for_sku, _start_date
    lines = _canonical(data)
    if any(x['date'] < _start_date(db) for x in lines):
        raise HTTPException(400, '补登日期早于本轮库存起算日期')
    ids = sorted(set(data.replace_ids))
    replaced = db.query(Shipment).filter(Shipment.store_id == store_id, Shipment.id.in_(ids)).all() if ids else []
    if len(replaced) != len(ids) or any(x.status not in ('confirmed', 'exported', 'clearance', 'transit') for x in replaced):
        raise HTTPException(409, '选中的发货单不存在或已变更，请刷新核对')
    restored = defaultdict(int)
    for s in replaced:
        for line in db.query(ShipmentLine).filter(ShipmentLine.shipment_id == s.id):
            if s.status == 'confirmed':
                restored[line.sku] += line.total_quantity
            elif s.status == 'transit':
                allocation = db.get(ShipmentAllocation, line.id)
                restored[line.sku] += allocation.domestic_quantity if allocation else 0
    skus = sorted({x['sku'] for x in lines})
    for sku in skus:
        from api.inventory_routes import _find_product_for_sku
        if not _find_product_for_sku(db, sku, store_id)[0]:
            raise HTTPException(400, f'SKU {sku} 不在当前店铺有效产品库中')
        if not db.query(PackingProduct).filter(
            PackingProduct.store_id == store_id,
            PackingProduct.sku == sku,
        ).first():
            raise HTTPException(400, f'SKU {sku} 不在装箱产品库中')
    balances = {sku: inventory_balance_for_sku(db, sku, store_id) + restored[sku] for sku in skus}
    remaining = {sku: max(0, v) for sku, v in balances.items()}
    result = []
    for x in lines:
        domestic = min(remaining[x['sku']], x['quantity'])
        remaining[x['sku']] -= domestic
        result.append(dict(x, domestic_quantity=domestic, legacy_quantity=x['quantity']-domestic))
    manifest = dict(lines=lines, replace_ids=ids)
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    snapshot = dict(manifest=manifest, balances=balances, replaced=[(s.id, s.status, s.version) for s in sorted(replaced, key=lambda s:s.id)])
    token = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return dict(lines=result, token=token, fingerprint=fingerprint, total_quantity=sum(x['quantity'] for x in result),
                domestic_quantity=sum(x['domestic_quantity'] for x in result), legacy_quantity=sum(x['legacy_quantity'] for x in result),
                boxes=len({(x['date'], x['shipping'], x['box']) for x in result}),
                replaced=[dict(id=s.id, shipment_no=s.shipment_no, quantity=sum(l.total_quantity for l in db.query(ShipmentLine).filter(ShipmentLine.shipment_id == s.id))) for s in replaced])


def apply(db, store_id, data):
    # Serialize reconciliations per store; a changed preview is rejected before any mutation.
    db.query(Store).filter(Store.id == store_id).with_for_update().first()
    canonical = _canonical(data)
    fingerprint = hashlib.sha256(json.dumps(dict(lines=canonical, replace_ids=sorted(set(data.replace_ids))), sort_keys=True).encode()).hexdigest()
    key = f'shipment-reconciliation:{store_id}:{fingerprint}'
    saved = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    if saved:
        previous = json.loads(saved.value)
        active = db.query(Shipment).filter(Shipment.store_id == store_id, Shipment.shipment_no.in_(previous['shipment_numbers']), Shipment.status == 'transit').count()
        if active != len(previous['shipment_numbers']):
            raise HTTPException(409, '该补登批次后续已撤回，请核对历史记录')
        return dict(previous, replayed=True)
    plan = preview(db, store_id, data)
    if not data.token or plan['token'] != data.token:
        raise HTTPException(409, '库存或发货记录已变化，请重新预览后提交')
    try:
        stamp = uuid.uuid4().hex
        for old in plan['replaced']:
            s = db.get(Shipment, old['id'])
            s.status = 'void'
            s.void_reason = '按已发货原装箱单更正，替代批次 ' + stamp
            s.voided_at = dt.datetime.utcnow()
            s.version += 1
        groups = defaultdict(list)
        for line in plan['lines']:
            groups[(line['date'], line['shipping'], line['box'])].append(line)
        numbers = []
        for index, ((date, shipping, box), lines) in enumerate(groups.items(), 1):
            shipment = Shipment(store_id=store_id, draft_key=f'{store_id}:reconcile:{stamp}:{index}',
                                shipment_no=f'RC-{date.replace("-", "")}-{stamp[:8]}-{index:02d}',
                                shipment_date=date, shipping=shipping, mark=box, status='transit')
            db.add(shipment); db.flush(); numbers.append(shipment.shipment_no)
            for x in lines:
                product = db.query(PackingProduct).filter(
                    PackingProduct.store_id == store_id,
                    PackingProduct.sku == x['sku'],
                ).first()
                line = ShipmentLine(shipment_id=shipment.id, sku=x['sku'], name_zh=product.name_zh,
                                    name_en=product.name_en, cartons=1, count_per_carton=x['quantity'], total_quantity=x['quantity'])
                db.add(line); db.flush()
                db.add(ShipmentAllocation(line_id=line.id, domestic_quantity=x['domestic_quantity'], legacy_quantity=x['legacy_quantity']))
        result = {k:plan[k] for k in ('total_quantity', 'domestic_quantity', 'legacy_quantity', 'boxes')}
        result.update(shipment_numbers=numbers, batch=stamp)
        db.add(SystemSettings(key=key, value=json.dumps(result)))
        record_operation(db, store_id, 'shipment', stamp, 'reconcile',
                         f'更正已发货 {plan["total_quantity"]} 件：国内库存 {plan["domestic_quantity"]} 件，老货 {plan["legacy_quantity"]} 件，全部海外在途',
                         dict(plan=plan, shipment_numbers=numbers))
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '记录已变更，请刷新后核对，勿重复提交')
    except Exception:
        db.rollback()
        raise
