import importlib.util
from pathlib import Path
import time
from datetime import datetime,timezone
import json
import pytest
from tests.test_collector_bridge import setup
from tests.test_pricing_routes import database
from api import collector_routes as bridge
from models import SystemSettings,DEFAULT_STORE_ID
from services.buybox_monitor import PREFIX

spec=importlib.util.spec_from_file_location('selector_relay',Path(__file__).resolve().parents[2]/'tools/pricepilot-relay/selector_relay.py')
relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)

def test_identity_failure_and_duplicate_results_are_not_relabelled():
    job={'productId':'lease','sku':'sku','url':'https://www.takealot.com/p/PLID123'}
    raw={**job,'productId':'original-id','buyboxPrice':269,'holdingBuybox':True}
    report=relay.make_reports([job],[raw],1000,1100)[0]
    assert report['productId']=='lease' and 'holdingBuybox' not in report
    assert not relay.make_reports([job],[raw,raw],1000,1100)
    for change in ({'sku':'other'},{'url':'https://www.takealot.com/p/PLID124'}):
        assert not relay.make_reports([job],[{**raw,**change}],1000,1100)
    for change in ({'error':'site_challenge'},{'variantSwitchFailed':True},{'buyboxPrice':True}):
        failure=relay.make_reports([job],[{**raw,**change}],1000,1100)[0]
        assert failure['error']=='source_collection_failed' and 'buyboxPrice' not in failure
    assert not relay.make_reports([job],[raw],1000,1601)

@pytest.mark.parametrize('age',[120,601,-20,None])
def test_original_timestamp_preserved_or_rejected(monkeypatch,age):
    with database() as db:
        job=setup(db,monkeypatch)
        item={**job,'buyboxPrice':269}
        if age is not None:item['collectedAt']=datetime.fromtimestamp(time.time()-age,timezone.utc).isoformat()
        bridge.offers(bridge.Report(items=[item]),db,DEFAULT_STORE_ID,'pricepilot_relay')
        value=json.loads(db.query(SystemSettings).filter_by(key=PREFIX+DEFAULT_STORE_ID+':one:one').first().value)
        if age==120:
            assert value['status']=='success' and value['last_success_at']==item['collectedAt']
            assert value['holding_buybox'] is None
        else:
            assert value['status']=='error' and value['error_code']=='stale_source_quote'
            assert not value.get('last_success_at')
        assert not db.query(SystemSettings).filter(SystemSettings.key.startswith('_pricing_rule:')).count()

def test_network_failure_releases_lease_without_escaping():
    calls=[]
    job={'productId':'lease','sku':'sku','url':'https://www.takealot.com/p/PLID123'}
    def call(path,body=None):
        calls.append(path)
        if path.endswith('/products'):return {'items':[job]}
        if path.endswith('/offers'):raise TimeoutError()
        return {'released':1}
    assert relay.forward_round([{**job,'buyboxPrice':269}],time.time(),call)['status']=='unavailable'
    assert calls[-1]=='/api/collector/release'

def test_unconfigured_and_expired_round_make_no_request(monkeypatch):
    monkeypatch.delenv('SELECTOR_COLLECTOR_KEY',raising=False)
    assert relay.forward_round([],time.time())['status']=='disabled'
    assert relay.forward_round([],time.time()-601,lambda *a:pytest.fail('no request'))['status']=='expired'
