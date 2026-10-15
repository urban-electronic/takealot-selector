"""PricePilot's price-based estimate; never seller identity or a price-write signal."""
import json
import math
import threading
from datetime import datetime, timezone
import httpx

_checks = threading.BoundedSemaphore(2)


def amount(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def estimate(previous, buybox, own, source, asof):
    """Equal prices infer holding; losing an inferred hold needs two valid rounds."""
    previous = previous or {}
    if not amount(buybox) or not amount(own):
        return pending(previous, failed=True)
    matched = abs(buybox - own) < .01
    losses = 0 if matched else previous.get('losing_rounds', 0) + 1
    retained = not matched and previous.get('holding') is True and losses < 2
    return dict(holding=matched or retained, state='matched' if matched else 'mismatch_pending' if retained else 'mismatch',
                losing_rounds=losses, own_price=own, own_price_source=source, own_price_asof=asof,
                checked_at=datetime.now(timezone.utc).isoformat(), identity_verified=False)


def pending(previous, failed=False):
    return {**(previous or {}), 'state':'verification_failed' if failed else 'checking', 'identity_verified':False}


def verify_batch(jobs):
    """Post-response read-only official checks; CAS protects pause and newer quotes.

    At most two batches run. Saturated checks remain pending until the next round.
    No credentials come from collectors; no price PUT or quote snapshot writes.
    """
    if not _checks.acquire(blocking=False): return
    try:
        from database import SessionLocal
        from models import SystemSettings
        from services.takealot_seller_api import configured_key
        from services.automatic_pricing import official_offer
        for row_key, expected in jobs[:20]:
            with SessionLocal() as db:
                row=db.query(SystemSettings).filter_by(key=row_key).first()
                if not row or row.value != expected: continue
                config=json.loads(expected)
                if config.get('status') != 'success': continue
                key=configured_key(db)
            try:
                if not key: raise ValueError('not configured')
                with httpx.Client(timeout=5, follow_redirects=False) as client:
                    own=official_offer(client,key,config['offer_id'])
                if own.get('sku') != config.get('sku'): raise ValueError('SKU changed')
                result=estimate(config.get('price_ownership'),config.get('buybox_price'),own['selling_price'],
                                'official_live',datetime.now(timezone.utc).isoformat())
            except Exception:
                result=pending(config.get('price_ownership'),failed=True)
            with SessionLocal() as db:
                config['price_ownership']=result
                db.query(SystemSettings).filter_by(key=row_key,value=expected).update(
                    {'value':json.dumps(config,ensure_ascii=False)})
                db.commit()
    finally:
        _checks.release()
