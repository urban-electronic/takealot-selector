import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

spec=importlib.util.spec_from_file_location('native_runner',Path(__file__).resolve().parents[2]/'tools/native-collector/collector.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

def test_native_runner_reuses_retained_pricepilot_core():
    core=runner.parser_module()
    assert Path(core.__file__).name=='pricepilot_collector.py'
    assert core._norm_label(' ✓ Blue ')=='blue'
    assert not runner.valid_product_url('https://example.com/PLID123')
    with pytest.raises(ValueError): runner.Api('https://example.com','test')

def test_native_report_is_per_item_and_no_write_endpoints(monkeypatch):
    calls=[]
    item={'productId':'lease','sku':'SKU1','url':'https://www.takealot.com/x/PLID1'}
    class Api:
        def call(self,path,body=None):
            calls.append((path,body))
            if path.endswith('/products'): return {'items':[item]}
            return {'processed':1}
    async def scrape(*args): return {**item,'buyboxPrice':100,'collectedAt':'2026-10-14T00:00:00+00:00'}
    monkeypatch.setattr(runner,'scrape',scrape)
    assert asyncio.run(runner.round_once(None,Api(),None))==1
    assert [p for p,b in calls]==['/api/collector/products','/api/collector/offers']
    assert len(calls[1][1]['items'])==1

def test_failed_report_releases_unfinished_lease(monkeypatch):
    calls=[]
    class Api:
        def call(self,path,body=None):
            calls.append((path,body))
            if path.endswith('/products'): return {'items':[{'productId':'lease','sku':'SKU1'}]}
            if path.endswith('/offers'): raise RuntimeError('network unavailable')
            return {'released':1}
    async def scrape(*args): return {'productId':'lease','sku':'SKU1','error':'price_not_found'}
    monkeypatch.setattr(runner,'scrape',scrape)
    with pytest.raises(RuntimeError): asyncio.run(runner.round_once(None,Api(),None))
    assert calls[-1]==('/api/collector/release',{'product_ids':['lease']})

def test_native_wrapper_preserves_quotes_but_never_verifies_identity():
    closed=[]
    class Page:
        async def close(self): closed.append(True)
    class Context:
        async def new_page(self): return Page()
    async def parse(page,item): return {'productId':'lease','sku':'SKU1','buyboxPrice':100,
        'holdingBuybox':True,'holdingBuyboxVerified':True,'offers':[{'price':100,'isMine':True}]}
    result=asyncio.run(runner.scrape(Context(),{'productId':'lease','sku':'SKU1','url':'https://www.takealot.com/x/PLID1'},SimpleNamespace(scrape_product=parse)))
    assert result['buyboxPrice']==100 and result['collectedAt']
    assert not result['holdingBuybox'] and 'holdingBuyboxVerified' not in result
    assert not result['offers'][0]['isMine'] and closed==[True]
