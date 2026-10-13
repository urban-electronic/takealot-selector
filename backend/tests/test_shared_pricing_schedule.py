import json
from datetime import datetime, timezone, timedelta
from services.automatic_pricing import shared_snapshot
from models import SystemSettings, DEFAULT_STORE_ID
from tests.test_pricing_routes import database


def test_pricing_uses_shared_quote_without_starting_collection():
    with database() as db:
        row = SystemSettings(key=f'_pricing_monitor:{DEFAULT_STORE_ID}:one:one',value=json.dumps({'sku':'a','enabled':False,'status':'success','last_success_at':datetime.now(timezone.utc).isoformat(),'snapshot':{'buyboxPrice':100,'offers':[]}}))
        db.add(row); db.commit()
        assert shared_snapshot(db,'one','one','a')['buyboxPrice']==100
        assert not json.loads(row.value)['enabled']
        assert shared_snapshot(db,'one','one','other').get('error')
        data=json.loads(row.value);data['status']='error';row.value=json.dumps(data);db.commit()
        assert shared_snapshot(db,'one','one','a').get('error')
        data['status']='success';data['last_success_at']=(datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat();row.value=json.dumps(data);db.commit()
        assert shared_snapshot(db,'one','one','a').get('error')
