"""Read the overseas shipment ledger into planning, without changing stock snapshots."""
import copy
import datetime as dt
import re
from models import Shipment, ShipmentLine, ShipmentAllocation


def with_shipments(db, store, product, profile, purchases):
    p = copy.deepcopy(profile)
    purchases = list(purchases)
    skus = [s for s in re.split(r'[\s,，;；]+', product.sku or '') if s]
    lines = (db.query(Shipment, ShipmentLine).join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id)
             .join(ShipmentAllocation, ShipmentAllocation.line_id == ShipmentLine.id)
             .filter(Shipment.store_id == store, Shipment.status == 'transit', ShipmentLine.sku.in_(skus)).all())
    for shipment, line in lines:
        mode = 'air' if shipment.shipping == '空运' else 'sea'
        identifier = 'shipment:' + line.id
        days = p.get('air_days',17) if mode == 'air' else p.get('sea_days',58)
        eta = (dt.date.fromisoformat(shipment.shipment_date) + dt.timedelta(days=days)).isoformat()
        purchases.append(dict(id=identifier, quantity=line.total_quantity, status='in_transit', recorded_at=shipment.shipment_date, notes='已发货海外在途'))
        p.setdefault('allocations', []).append(dict(procurement_id=identifier, mode=mode, quantity=line.total_quantity, eta=eta, included_in_stock=False))
    return p, purchases
