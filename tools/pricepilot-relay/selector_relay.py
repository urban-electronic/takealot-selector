"""Forward an existing collector round. No browser, database or pricing writes.

Call forward_round(raw_report_items, round_started_epoch) from the existing daemon.
SELECTOR_COLLECTOR_KEY is the selector's existing collector credential, not the
PricePilot credential. No credentials or response bodies are logged.
"""
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from urllib import request
from urllib.parse import urlsplit

BASE = 'https://takealot-selector-production.up.railway.app'


def identity(item):
    p = urlsplit(item.get('url') or '')
    match = re.search(r'PLID\d+', p.path, re.I)
    sku = item.get('sku')
    if p.scheme != 'https' or p.hostname not in ('www.takealot.com', 'takealot.com') or not match or not isinstance(sku,str) or not sku:
        return None
    return sku, match.group().upper()


def make_reports(jobs, raw_items, round_started, now=None):
    now = time.time() if now is None else now
    if isinstance(round_started,bool) or not isinstance(round_started,(int,float)) or not math.isfinite(round_started) or not 0 <= now-round_started <= 600:
        return []
    indexed = {}
    for item in raw_items:
        if isinstance(item,dict) and identity(item):
            indexed.setdefault(identity(item),[]).append(item)
    reports = []
    for job in jobs:
        matches = indexed.get(identity(job),[])
        # Multiple shops/variants sharing an identity are ambiguous, never choose first.
        if len(matches) != 1:
            continue
        item = matches[0]
        price = item.get('buyboxPrice')
        if item.get('error') or item.get('variantSwitchFailed') or isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price <= 0:
            # A known failed round invalidates shared pricing, even if the previous
            # successful quote is still younger than ten minutes.
            reports.append({'productId':job['productId'],'sku':job['sku'],
                'error':'source_collection_failed','offers':[],
                'collectedAt':datetime.fromtimestamp(round_started,timezone.utc).isoformat()})
            continue
        offers=[]
        for offer in item.get('offers') or []:
            if not isinstance(offer,dict): continue
            # Original isMine/holdingBuybox may be based on price equality. Preserve
            # the ambiguity by excluding those offers from competitor calculations.
            offers.append({k:offer[k] for k in ('seller','price','status','offerType','isMine','deliveryLabel','leadtimeDays') if k in offer})
        reports.append({'productId':job['productId'],'sku':job['sku'],
            'buyboxPrice':price,'buyboxSeller':item.get('buyboxSeller'),
            'buyboxType':item.get('buyboxType'),'offers':offers,
            'collectedAt':datetime.fromtimestamp(round_started,timezone.utc).isoformat()})
    return reports


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def forward_round(raw_items, round_started, call=None):
    """Single bounded sync; failures never interrupt the original collector."""
    key=os.environ.get('SELECTOR_COLLECTOR_KEY','').strip()
    if call is None and not key: return {'status':'disabled','processed':0}
    if call is None:
        opener=request.build_opener(NoRedirect)
        def call(path,body=None):
            if path not in ('/api/collector/products','/api/collector/offers','/api/collector/release'):
                raise ValueError('unsupported path')
            req=request.Request(BASE+path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'X-Collector-Key':key,'X-Collector-Client':'pricepilot_relay',
                         'X-Store-ID':'default-store','Content-Type':'application/json'})
            with opener.open(req,timeout=20) as response:
                raw=response.read(2_000_001)
                if len(raw)>2_000_000: raise ValueError('response too large')
                return json.loads(raw)
    leases=[]
    try:
        # Refuse expired rounds before touching the remote queue.
        if isinstance(round_started,bool) or not isinstance(round_started,(int,float)) or not math.isfinite(round_started) or not 0 <= time.time()-round_started <= 600:
            return {'status':'expired','processed':0}
        jobs=call('/api/collector/products')['items']
        leases=[job['productId'] for job in jobs]
        reports=make_reports(jobs,raw_items,round_started)
        result=call('/api/collector/offers',{'items':reports}) if reports else {'processed':0}
        return {'status':'synced','processed':result.get('processed',0),'matched':len(reports)}
    except Exception:
        return {'status':'unavailable','processed':0}
    finally:
        if leases:
            try: call('/api/collector/release',{'product_ids':leases})
            except Exception: pass
