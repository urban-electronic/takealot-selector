"""Real Chromium layout smoke; isolated fixture API, never touches production."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5173'
PRODUCT = dict(id='fixture-product', product_no=1, product_name='多用途收纳包 / Travel storage bag with a long product title', chinese_product_name='收纳包', sku='TEST-001', selection_status='数据待补充', product_image_url=None, product_image_path=None, takealot_url='https://catalog.example.invalid/product/this-is-a-deliberately-long-product-link-for-layout-validation', recorded_at='2026-10-15', purchase_quantity=1, fee_category_confirmed=False, is_archived=False)
# Optional numeric values used by the existing detail view; fixtures are never saved.
for key in ['actual_sale_price_zar','purchase_cost_cny','purchase_shipping_cny','length_mm','width_mm','height_mm','actual_weight_kg','packaging_cost_per_unit_cny','inbound_listing_fee_cny','outbound_operation_fee_cny','last_mile_delivery_fee_cny','other_fee_cny','fulfillment_fee_zar','success_fee_rate','total_cost_zar','profit_zar','profit_margin']:
    PRODUCT[key] = 0
STORE = dict(id='default-store', name='自营店铺', platform='Takealot', status='active', owner_name='', sync_method='manual', sync_interval_minutes=60, external_store_ref='', allow_negative_inventory_shipments=False, last_synced_at=None)
PROCUREMENT = dict(id='fixture-record', product_id=PRODUCT['id'], product_no='1', product_name=PRODUCT['product_name'], recorded_at='2026-10-15', quantity=2, unit_price_cny=10, total_price_cny=20, status='in_transit', notes='', shipping_cost_cny=0)
PACK = dict(sku='TEST-001', name='收纳包', name_zh='收纳包', name_en='Travel storage bag', image_file='', default_count=1, material='织物', electric='否', magnetic='否', unit='个', weight='0.2', brand='', battery='')
RULE = dict(enabled=False, use_system_min=True, strategy='takealot_buybox', last_result='未启动')
PRICING = dict(product_id=PRODUCT['id'], offer_id='test-offer', sku='TEST-001', name=PRODUCT['product_name'], group='未分类', stock=10, price=269, suggested_min=200, rule=RULE, monitor=dict(status='error', enabled=False, interval_minutes=5, error_code='site_challenge', error='网站要求本人验证', last_success_at=None))
DATA = {
    '/api/stores':[STORE], '/api/inventory/stores-overview':[],
    '/api/products':[PRODUCT], '/api/products/'+PRODUCT['id']:PRODUCT,
    '/api/products/stats/dashboard':dict(total=1, qualified=0, not_recommended=0, pending=0, data_incomplete=1, avg_profit_margin=0),
    '/api/inventory':[dict(product_id=PRODUCT['id'], name='收纳包', name_en=PRODUCT['product_name'], sku='TEST-001', available=10, in_transit=2, purchased=10, adjusted=0, shipped=0)],
    '/api/inventory/shipments':[], '/api/inventory/adjustments':[], '/api/activity':[],
    '/api/procurement':[PROCUREMENT], '/api/fee-categories':[], '/api/fee-mapping-rules':[],
    '/api/settings':{'cny_per_zar':'0.41'}, '/api/integrations/takealot-seller/status':{'configured':False},
    '/api/packing/products':[PACK], '/api/packing/sync-from-products':{'ok':True,'total_products':1,'skipped_no_sku':0,'duplicate_sku':0},
    '/api/operations/alerts':[], '/api/operations/overview':{'rows':[dict(id=PRODUCT['id'], name='收纳包', sku='TEST-001', plan={'level':'healthy','velocity':1,'stock':10,'schedule':[]})]}, '/api/operations/'+PRODUCT['id']:{'profile':None,'purchases':[],'plan':None},
    '/api/automatic-pricing':{'configured':False,'rows':[PRICING]},
    '/api/buybox/page-open':{'queued':0,'skipped':0,'price_changed':False},
    '/api/products/seller-assist/preview':{'fillable':1,'items':[dict(id=PRODUCT['id'],product_no=1,name=PRODUCT['product_name'],token='fixture-only',changes=[dict(field='product_name',label='标题',value=PRODUCT['product_name'])],differences=[],warnings=['请核对实际规格与商品身份'],manual=['重量'])]},
}
PAGES = [('/', '运营工作台'),('/products','产品列表'),('/inventory','库存管理'),('/auto-pricing','Buybox与自动调价'),('/replenishment','补货计划'),('/packing','装箱单'),('/create','新建产品'),('/procurement','采购记录'),('/stores','店铺管理'),('/settings','系统设置'),('/products/fixture-product','多用途收纳包')]

def main():
    cli=argparse.ArgumentParser();cli.add_argument('--observe',action='store_true');cli.add_argument('--screenshots');args=cli.parse_args()
    if args.screenshots: Path(args.screenshots).mkdir(parents=True,exist_ok=True)
    failures=[];unknown=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context()
        context.add_init_script("localStorage.setItem('api_base_url', 'http://127.0.0.1:5173')")
        def intercept(route):
            from urllib.parse import urlsplit
            url=urlsplit(route.request.url)
            if url.path.startswith('/api/'):
                allowed_write = route.request.method == 'POST' and url.path in ('/api/packing/sync-from-products', '/api/buybox/page-open', '/api/products/seller-assist/preview')
                if route.request.method != 'GET' and not allowed_write:
                    unknown.append(route.request.method+' '+url.path);route.abort();return
                if url.path in DATA:
                    route.fulfill(json=DATA[url.path]);return
                unknown.append(route.request.method+' '+url.path);route.fulfill(status=404,json={'detail':'Fixture endpoint not declared'});return
            if url.netloc=='127.0.0.1:5173': route.continue_()
            else: route.abort()
        context.route('**/*',intercept)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        checked=0
        for width,height in [(1920,900),(1440,900),(1280,720),(1024,640),(768,700),(390,680),(1024,480),(390,480),(320,568)]:
            page.set_viewport_size({'width':width,'height':height})
            for path,title in PAGES:
                print(f'Checking {width} {path}',flush=True)
                page.goto(BASE+path)
                try: page.get_by_role('heading',name=title,exact=False).first.wait_for(timeout=8000)
                except Exception: failures.append(f'{width} {path}: missing heading');continue
                page.locator('.loading').wait_for(state='hidden')
                details=page.evaluate('''() => ({width:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth,
                    header:document.querySelector('.app-header').getBoundingClientRect().right,
                    offenders:[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0 && r.right>innerWidth+2 && !e.closest('table') && !e.closest('.primary-nav')}).slice(0,5).map(e=>e.className||e.tagName)})''')
                checked+=1
                if page.get_by_role('main').count()!=1: failures.append(f'{width}: missing main landmark')
                nav=page.locator('.primary-nav')
                links=nav.locator('a')
                if links.count()!=6:failures.append('missing primary navigation entry')
                for link in links.all():
                    if link.evaluate("e=>getComputedStyle(e).whiteSpace")!='nowrap':failures.append(f'{width}: navigation label wraps')
                    b=link.bounding_box()
                    if b['height']>44:failures.append(f'{width}: vertical navigation label')
                    if width<=700 and (b['x']<0 or b['x']+b['width']>width+1):failures.append(f'{width}: hidden primary navigation entry')
                if details['scroll']>width+2: failures.append(f'{width} {path}: overflow {details}')
                if path=='/products' and (width,height) in ((1280,720),(390,480),(320,568)):
                    page.get_by_role('button',name='核对本店资料',exact=True).click()
                    preview=page.locator('.seller-assist-dialog');preview.wait_for()
                    if preview.locator('.seller-assist-body').evaluate('e=>e.scrollWidth>e.clientWidth+1'):failures.append(f'{width}: preview body overflows')
                    if width<=480 and not preview.get_by_role('region',name='资料核对明细').evaluate('e=>e.scrollWidth>e.clientWidth'):failures.append(f'{width}: preview table lacks local scroll')
                    bounds=preview.bounding_box()
                    if bounds['x']<0 or bounds['y']<0 or bounds['x']+bounds['width']>width+1 or bounds['y']+bounds['height']>height+1:failures.append(f'{width}: seller preview outside viewport')
                    for control in preview.get_by_role('button').all():
                        b=control.bounding_box()
                        if b['x']<0 or b['y']<0 or b['x']+b['width']>width+1 or b['y']+b['height']>height+1:failures.append(f'{width}: preview action clipped')
                    if args.screenshots: page.screenshot(path=str(Path(args.screenshots)/f'seller-preview-{width}.png'))
                    page.keyboard.press('Escape')
                    if preview.count():failures.append('seller preview Escape failed')
                if path=='/' and width in (1440,1024,390):
                    page.get_by_role('button',name='管理工具',exact=True).click()
                    menu=page.locator('.management-menu-panel').bounding_box()
                    if menu['x']<0 or menu['x']+menu['width']>width+1: failures.append(f'{width}: management menu clipped')
                    page.keyboard.press('Escape')
                    if page.locator('.management-menu-panel').count(): failures.append('management Escape failed')
                if path=='/procurement' and (width,height) in ((1280,720),(390,680),(390,480),(1024,480),(320,568)):
                    page.get_by_role('button',name='新建记录',exact=False).click()
                    if not page.locator('form').is_visible(): failures.append('procurement form missing')
                    page.get_by_role('button',name='取消',exact=True).click()
                    page.get_by_role('button',name='删除',exact=True).click()
                    box=page.get_by_role('heading',name='确认删除',exact=True).locator('..').bounding_box()
                    if box['x']<0 or box['x']+box['width']>width+1:failures.append(f'{width}: delete dialog outside viewport {box}')
                    page.get_by_role('button',name='取消',exact=True).click()
                if path=='/packing' and (width,height) in ((1280,720),(390,680),(390,480),(1024,480),(320,568)):
                    page.get_by_role('button',name='编辑',exact=True).first.click()
                    modal=page.get_by_role('dialog',name='编辑装箱产品',exact=True);bounds=modal.bounding_box()
                    columns=modal.locator('.dialog-form-grid').evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length")
                    if columns!=(1 if width<=480 else 2):failures.append(f'{width}: packing form columns {columns}')
                    if bounds['x']<0 or bounds['y']<0 or bounds['x']+bounds['width']>width+1 or bounds['y']+bounds['height']>height+1:failures.append(f'{width}: packing dialog outside viewport')
                    modal.get_by_role('button',name='保存',exact=True).scroll_into_view_if_needed()
                    if modal.evaluate('e=>e.scrollWidth>e.clientWidth+1'):failures.append(f'{width}: packing form content overflows')
                    if args.screenshots and width in (390,1280):page.screenshot(path=str(Path(args.screenshots)/f'packing-edit-{width}.png'))
                    page.get_by_role('button',name='取消',exact=True).click()
                    if (width,height)==(390,680):
                        page.locator('.packing-table-scroll input[type=checkbox]').first.check()
                        page.locator('.primary-nav').get_by_role('link',name='库存',exact=True).click()
                        page.get_by_role('heading',name='库存管理',exact=True).wait_for()
                        page.locator('.primary-nav').get_by_role('link',name='装箱单',exact=True).click()
                        page.get_by_role('heading',name='装箱单工作台',exact=True).wait_for()
                        if page.locator('.packing-selected-item').count()!=1:failures.append('packing selection lost on navigation')
                if path=='/replenishment' and (width,height) in ((1280,720),(390,480)):
                    page.get_by_role('button',name='设置',exact=True).first.click()
                    dialog=page.get_by_role('dialog',name='补货计划设置',exact=True)
                    dialog.wait_for()
                    close=dialog.get_by_role('button',name='关闭',exact=True)
                    close.scroll_into_view_if_needed()
                    bounds=close.bounding_box()
                    if bounds['y']<0 or bounds['y']+bounds['height']>height: failures.append('replenishment close button unreachable')
                    page.keyboard.press('Escape')
                    if dialog.count():failures.append('replenishment Escape failed')
                if args.screenshots and (path in ('/','/auto-pricing','/packing') and width in (1440,390)):
                    page.screenshot(path=str(Path(args.screenshots)/((path.strip('/').replace('/','-') or 'home')+f'-{width}.png')))
        browser.close()
    report=dict(checked=checked,failures=failures,page_errors=errors,undeclared_api=sorted(set(unknown)))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not args.observe: assert not failures and not errors and not unknown,report
if __name__=='__main__':
    import subprocess, time
    from urllib.request import build_opener, ProxyHandler
    server=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for attempt in range(80):
            try:
                build_opener(ProxyHandler({})).open(BASE,timeout=1).close();break
            except OSError:
                if server.poll() is not None: raise RuntimeError('Vite test server failed to start')
                time.sleep(.1)
        else: raise RuntimeError('Vite test server did not become ready')
        main()
    finally:
        server.terminate();server.wait(timeout=10)
