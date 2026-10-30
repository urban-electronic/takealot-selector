"""Real Chromium + real selection routes against a fresh in-memory DB.
All browser external requests and undeclared API paths are blocked; public data
is a local fixture, never a production fetch. No application main/production DB.
"""
import sys,io,json,runpy
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts')]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from database import Base,get_db
from models import Store,FeeCategory,Product,SelectionItem,DEFAULT_STORE_ID
from api.store_routes import get_store_id
from api import selection_routes as sr
from services import takealot_seller_api as seller
from playwright.sync_api import sync_playwright,expect
from _audit_server import vite_server
eng=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
Base.metadata.create_all(eng);Session=sessionmaker(bind=eng)
with Session() as d:
 d.add(Store(id=DEFAULT_STORE_ID,name='隔离验收店铺',status='active'));d.add(FeeCategory(name='Homeware',success_fee_rate=.15,active=True));d.commit()
def db():
 with Session() as d:yield d
app=FastAPI();app.include_router(sr.router);app.dependency_overrides[get_db]=db;app.dependency_overrides[get_store_id]=lambda:DEFAULT_STORE_ID
client=TestClient(app)
seller.configured_key=lambda _:None
sr.pub.fetch_many=lambda ids,workers=4:iter([{'plid':p,'success':True,'product_name':'Fixture storage cup','product_image_url':'https://fixture.invalid/cup.svg','actual_sale_price_zar':499,'takealot_category_path':'Homeware'} for p in ids])
rows=[['产品链接','产品名称','售价'],['https://www.takealot.com/fixture/PLID999000001','Fixture cup',499]]
xlsx=runpy.run_path(str(ROOT/'backend/tests/test_selection_routes.py'))['make_xlsx'](rows)
errors=[];unknown=[];writes=[]
with vite_server(ROOT) as base,sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True);ctx=browser.new_context(viewport={'width':1440,'height':1000})
 ctx.add_init_script('localStorage.setItem("api_base_url",'+json.dumps(base)+')')
 def route(r):
  req=r.request;url=urlsplit(req.url)
  if url.path.startswith('/api/selection/'):
   if req.method!='GET':writes.append((req.method,url.path))
   result=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers={'Content-Type':req.headers.get('content-type','application/json')})
   
   if result.status_code>=400: print('API FAILURE',url.path,result.status_code,result.text[:300],flush=True)
   r.fulfill(status=result.status_code,body=result.content,headers={'Content-Type':'application/json'});return
  if url.path.startswith('/api/'):
   if url.path=='/api/image-proxy':
    r.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>');return
   if url.path=='/api/buybox/page-open':r.fulfill(json={'ok':True});return
   fixtures={'/api/stores':[{'id':DEFAULT_STORE_ID,'name':'隔离验收店铺','status':'active'}],'/api/fee-categories':[{'id':1,'name':'Homeware','success_fee_rate':.15,'active':True}],'/api/settings':{}}
   if url.path in fixtures:r.fulfill(json=fixtures[url.path]);return
   unknown.append(req.method+' '+url.path);r.fulfill(status=404,json={'detail':'undeclared fixture'});return
  if url.netloc==urlsplit(base).netloc:r.continue_()
  elif url.netloc=='fixture.invalid':r.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>')
  else:r.abort()
 ctx.route('**/*',route);page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(base+'/selection');page.locator('input[type=file]').set_input_files({'name':'isolated-selection.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':xlsx})
 expect(page.locator('.selection-card')).to_have_count(1)
 page.get_by_role('button',name='抓取主图与类目',exact=True).click()
 
 try: expect(page.locator('.selection-card img')).to_have_count(1)
 except Exception:
  print('DEBUG',page.locator('.selection-notice').all_text_contents(),unknown,writes,flush=True);raise
 def fill(label,value):
  el=page.get_by_label(label,exact=True);el.fill(str(value));el.press('Tab');expect(page.locator('.selection-busy')).to_have_count(0)
 fill('中文品名','隔离验收收纳杯');fill('1688 / 货源链接','https://detail.1688.com/offer/123.html');fill('选品备注','蓝色2件装，隔离测试')
 # Numeric labels include unit text, so locate by wrapping label's first span.
 def number(prefix,value):
  el=page.locator('label.selection-field').filter(has=page.locator('span',has_text=prefix)).locator('input[type=number]')
  el=el.first;el.fill(str(value));el.press('Tab');expect(page.locator('.selection-busy')).to_have_count(0)
 for label,val in [('长',100),('宽',100),('高',100),('重量',.2),('供货单价',10),('采购数量',2),('国内运费',0)]:number(label,val)
 page.locator('.selection-card label').filter(has=page.locator('span',has_text='物流方式')).locator('select').select_option(label='海运普货');expect(page.locator('.selection-busy')).to_have_count(0)
 page.locator('.selection-card label').filter(has=page.locator('span',has_text='类目')).locator('select').select_option(label='Homeware（15%）');expect(page.locator('.selection-busy')).to_have_count(0)
 page.reload();expect(page.get_by_label('中文品名',exact=True)).to_have_value('隔离验收收纳杯');expect(page.get_by_label('1688 / 货源链接',exact=True)).to_have_value('https://detail.1688.com/offer/123.html')
 with Session() as d:
  item=d.query(SelectionItem).one();assert item.purchase_cost_cny==20;assert item.profit_margin is not None;assert item.dimensions_source=='manual';saved_margin=item.profit_margin
 page.get_by_role('button',name='按 15% 勾选达标商品').click();expect(page.locator('.selection-card input[type=checkbox]')).to_be_checked()
 page.on('dialog',lambda dialog:dialog.accept());page.get_by_role('button',name='批量加入产品列表',exact=False).click();expect(page.get_by_role('link',name='查看产品')).to_have_count(1)
 with Session() as d:
  product=d.query(Product).one();assert product.chinese_product_name=='隔离验收收纳杯';assert product.purchase_url=='https://detail.1688.com/offer/123.html';assert product.note=='蓝色2件装，隔离测试';assert product.purchase_cost_cny==20;assert abs(product.profit_margin-saved_margin)<1e-8
 assert not errors,errors;assert not unknown,unknown
 page.screenshot(path='/tmp/selection-journey-desktop.png',full_page=True)
 page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile overflow'
 page.screenshot(path='/tmp/selection-journey-mobile.png',full_page=True)
 print(json.dumps({'result':'PASS','path':'xlsx -> enrich fixture -> source/dimensions/cost save -> reload -> qualify -> promote -> product readback','write_count':len(writes),'browser_errors':errors,'undeclared_api':unknown,'margin_match':saved_margin},ensure_ascii=False))
 browser.close()
