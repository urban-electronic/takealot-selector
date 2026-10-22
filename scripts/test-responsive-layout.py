"""Real Chromium layout smoke; isolated fixture API, never touches production."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

# 约翰内斯堡全年 UTC+2、无夏令时，所以这里不用 zoneinfo（Windows 上要额外装 tzdata）。
# 调价页的「今日改价 N/M」按平台所在时区切天，fixture 必须给出当天日期才会渲染这个计数。
JNB_TODAY = (datetime.now(timezone.utc) + timedelta(hours=2)).date().isoformat()

BASE = 'http://127.0.0.1:5173'
PRODUCT = dict(id='fixture-product', product_no=1, product_name='多用途收纳包 / Travel storage bag with a long product title', chinese_product_name='收纳包', sku='TEST-001', selection_status='数据待补充', product_image_url=None, product_image_path=None, takealot_url='https://catalog.example.invalid/product/this-is-a-deliberately-long-product-link-for-layout-validation', recorded_at='2026-10-15', purchase_quantity=1, fee_category='Home', fee_category_confirmed=False, is_archived=False)
# Optional numeric values used by the existing detail view; fixtures are never saved.
for key in ['actual_sale_price_zar','purchase_cost_cny','purchase_shipping_cny','length_mm','width_mm','height_mm','actual_weight_kg','packaging_cost_per_unit_cny','inbound_listing_fee_cny','outbound_operation_fee_cny','last_mile_delivery_fee_cny','other_fee_cny','fulfillment_fee_zar','success_fee_rate','total_cost_zar','profit_zar','profit_margin']:
    PRODUCT[key] = 0
# 多市场参考价：列表里是一格并列三个价格（210px），详情页是一张表。
# 不给数据时这一列永远只渲染一个「—」，等于从没被量过；这里按四位数价格 + 带小数的
# 新加坡价给最宽的一档（价格越高标签越宽，四位数是代运营包袋的真实量级）。
# 价格在列表里会折成兰特（R），四位数人民币折完是五位数（¥1289 → R31,500），
# 所以这一格比原来的本币写法更宽——列宽就是按这个最坏情况给的。
PRODUCT['market_prices'] = json.dumps({
    'CN': dict(price=1289, cur='CNY', src='官网', at='2026-10-21'),
    # SG 带 stale：它是唯一由服务端每天重读的市场，也只有它会过期。
    # 不给这一条时「已过期」那段文案在任何宽度、任何页面上都没被渲染过。
    'SG': dict(price=245.9, cur='SGD', src='官网自动', at='2026-10-21', miss=3, stale=True),
    'TB': dict(price=899, cur='CNY', src='淘宝均价', n=128, at='2026-10-21'),
}, ensure_ascii=False)
# 店名写「代理」：多市场参考价这套只对代理店显示（自营店没有官方售价，整块会收起来）
STORE = dict(id='default-store', name='代理CK店铺', platform='Takealot', status='active', owner_name='', sync_method='manual', sync_interval_minutes=60, external_store_ref='', allow_negative_inventory_shipments=False, last_synced_at=None)
PROCUREMENT = dict(id='fixture-record', product_id=PRODUCT['id'], product_no='1', product_name=PRODUCT['product_name'], recorded_at='2026-10-15', quantity=2, unit_price_cny=10, total_price_cny=20, status='in_transit', notes='', shipping_cost_cny=0)
PACK = dict(sku='TEST-001', name='收纳包', name_zh='收纳包', name_en='Travel storage bag', image_file='', default_count=1, material='织物', electric='否', magnetic='否', unit='个', weight='0.2', brand='', battery='')
RULE = dict(enabled=False, use_system_min=True, strategy='takealot_buybox', last_result='未启动',
            max_daily_changes=10, today_changes=4, change_day=JNB_TODAY,
            # 决策卡片和「今日改价」都只从 rule.plan 渲染。没有 plan 时这一整块不出现，
            # 审计照样报「0 处问题」——等于这一屏从没被量过。这里给一份真实形状的
            # 决策（最长的原因文案 + 紧张档 + 剔除异常报价 + 配额计数），把卡片撑开。
            plan=dict(regime='compete', current=269, floor=200, ceiling=320, cheapest=255, gap_pct=5.2,
                      live_count=3, dark_count=1, sole_seller=False, pressure='tight', cover_days=12.4,
                      outliers=2, confidence='high', risk='high',
                      reason='3 个竞品有现货，最低 R 255；本店不在持位，按一次最多降 15% 走一步：269 → 255，够不着就分几轮走。库存紧张（约 12 天）时不跟降。'))
PRICING = dict(product_id=PRODUCT['id'], offer_id='test-offer', sku='TEST-001', name=PRODUCT['product_name'], group='未分类', stock=10, price=269, suggested_min=200, rule=RULE, monitor=dict(status='error', enabled=False, interval_minutes=5, error_code='site_challenge', error='网站要求本人验证', last_success_at=None))
PRICING['monitor'].update(buybox_price=269,last_success_at='2026-10-15T14:00:00Z',price_ownership=dict(state='matched',own_price=269,own_price_source='official_live',own_price_asof='2026-10-15T14:00:01Z',identity_verified=False))
DATA = {
    '/api/stores':[STORE], '/api/inventory/stores-overview':[],
    '/api/products':[PRODUCT], '/api/products/'+PRODUCT['id']:PRODUCT,
    '/api/products/stats/dashboard':dict(total=1, qualified=0, not_recommended=0, pending=0, data_incomplete=1, avg_profit_margin=0),
    '/api/inventory':[dict(product_id=PRODUCT['id'], name='收纳包', name_en=PRODUCT['product_name'], sku='TEST-001', available=10, in_transit=2, purchased=10, adjusted=0, shipped=0)],
    '/api/inventory/shipments':[], '/api/inventory/adjustments':[], '/api/activity':[],
    '/api/procurement':[PROCUREMENT], '/api/fee-categories':[], '/api/fee-mapping-rules':[],
    '/api/settings':{'cny_per_zar':'0.41'}, '/api/integrations/takealot-seller/status':{'configured':False},
    # 参考价列表要把本币折成兰特，页面一挂载就会读这个接口。漏声明会让整个布局测试
    # 因为「未声明的接口」直接失败，而不是悄悄少测一列。
    '/api/market-prices/fx':{'base':'ZAR','rates':{'SGD':12.8345,'CNY':2.4453},'at':'2026-10-21','src':'open.er-api.com','fetched_at':'2026-10-21T00:02:32+00:00','stale':False,'fallback':False},
    # 「参考价」批量录入面板一打开就读同步状态。漏声明时它 404、面板里只挂一句
    # 「读取同步状态失败」，覆盖率与上次同步时间在审计里等于从没出现过。
    '/api/market-prices/status':dict(store_id='default-store', products=1, products_with_price=1,
        coverage={'CN':{'filled':1,'stale':0},'SG':{'filled':1,'stale':1},'TB':{'filled':1,'stale':0}},
        last_sync={'at':'2026-10-21T05:12:33+00:00','updated':1}, interval_hours=24, auto_disabled=False,
        due=False, auto_markets=['SG'],
        manual_only={'TB':'淘宝/天猫需登录，不做自动抓取','CN':'中国官网已停摆，需人工录入'}),
    # 设置页会读本店卖家名（调价判断 Buybox 持位的依据），漏声明就会 404 后静默降级。
    '/api/integrations/takealot-seller/identity':{'name':'','source':'','candidates':[]},
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
        # 需要模拟「这个接口读失败」时把路径放进来（见文件末尾的降级界面检查）。
        failing=set()
        def intercept(route):
            from urllib.parse import urlsplit
            url=urlsplit(route.request.url)
            if url.path.startswith('/api/'):
                if url.path in failing:
                    route.fulfill(status=500,json={'detail':'Fixture failure'});return
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
                if path=='/products' and (width,height) in ((1280,720),(390,480),(320,568)):
                    # 「多市场参考价」批量录入面板：一个 aria-modal 弹窗，四列表头 + 两列输入框 +
                    # 只读的新加坡价（带「已过期」标记）。它是唯一能一次填十几行的入口，
                    # 却从来没被任何宽度量过——320px 恰恰是它最容易挤爆的一档。
                    page.get_by_role('button',name='参考价',exact=True).click()
                    mp=page.get_by_role('dialog',name='多市场参考价',exact=True)
                    mp.wait_for()
                    if mp.locator('.seller-assist-body').evaluate('e=>e.scrollWidth>e.clientWidth+1'):failures.append(f'{width}: market price body overflows')
                    if width<=480 and not mp.get_by_role('region',name='参考价明细').evaluate('e=>e.scrollWidth>e.clientWidth'):failures.append(f'{width}: market price table lacks local scroll')
                    bounds=mp.bounding_box()
                    if bounds['x']<0 or bounds['y']<0 or bounds['x']+bounds['width']>width+1 or bounds['y']+bounds['height']>height+1:failures.append(f'{width}: market price dialog outside viewport')
                    for control in mp.get_by_role('button').all():
                        b=control.bounding_box()
                        if b['x']<0 or b['y']<0 or b['x']+b['width']>width+1 or b['y']+b['height']>height+1:failures.append(f'{width}: market price action clipped')
                    if page.evaluate('()=>document.documentElement.scrollWidth')>width+2: failures.append(f'{width}: market price dialog causes page overflow')
                    # 声明了 aria-modal 就得能用 Esc 关掉（和「核对本店资料」同一个口径）。
                    page.keyboard.press('Escape')
                    if mp.count():failures.append(f'{width}: market price Escape failed')
                if path=='/auto-pricing' and (width,height) in ((1280,720),(390,680),(320,568)):
                    # 8 个次要动作收在默认收起的「更多」面板里：不点开就等于没审计过。
                    # 这条折叠的原始动因就是 320px 会溢出，所以必须在这个宽度上真开一次。
                    page.get_by_role('button',name='更多',exact=True).click()
                    panel=page.locator('.actions-more-panel');panel.wait_for()
                    bounds=panel.bounding_box()
                    if bounds['x']<0 or bounds['x']+bounds['width']>width+1: failures.append(f'{width}: more panel clipped {bounds}')
                    if panel.evaluate('e=>e.scrollWidth>e.clientWidth+1'): failures.append(f'{width}: more panel content overflows')
                    for control in panel.get_by_role('button').all()+panel.locator('input,select').all():
                        b=control.bounding_box()
                        if b['x']<0 or b['x']+b['width']>width+1: failures.append(f'{width}: more panel control clipped {b}')
                    if page.evaluate('()=>document.documentElement.scrollWidth')>width+2: failures.append(f'{width}: more panel causes page overflow')
                    page.get_by_role('button',name='收起',exact=True).click()
                    if page.locator('.actions-more-panel').count(): failures.append(f'{width}: more panel did not collapse')
                    # 每行的「详情」默认收起，里面装着决策卡片、诊断、持位依据。
                    # 不点开就等于这些内容在任何宽度下都没被量过；而它恰恰是最长的一块
                    # （长原因文案 + 8 个 chip），窄屏最容易挤爆的就是这里。
                    row=page.locator('.row-details').first
                    row.locator('summary').click()
                    card=row.locator('.decision-card')
                    if not card.is_visible(): failures.append(f'{width}: decision card not rendered after opening details')
                    else:
                        box=card.bounding_box()
                        if box['height']<40: failures.append(f'{width}: decision card collapsed {box}')
                        if card.evaluate('e=>e.scrollWidth>e.clientWidth+1'): failures.append(f'{width}: decision card content overflows')
                        if row.evaluate('e=>e.scrollWidth>e.clientWidth+1'): failures.append(f'{width}: row details content overflows')
                        for chip in card.locator('span').all():
                            b=chip.bounding_box()
                            if b and (b['x']<box['x']-1 or b['x']+b['width']>box['x']+box['width']+1): failures.append(f'{width}: decision chip outside card {b}')
                    if page.evaluate('()=>document.documentElement.scrollWidth')>width+2: failures.append(f'{width}: opened details causes page overflow')
                    row.locator('summary').click()
                    if card.is_visible(): failures.append(f'{width}: details did not collapse')
                if args.screenshots and (path in ('/','/auto-pricing','/packing') and width in (1440,390)):
                    page.screenshot(path=str(Path(args.screenshots)/((path.strip('/').replace('/','-') or 'home')+f'-{width}.png')))
        # ---- 费率品类读失败时的降级界面 -------------------------------------
        # /api/fee-categories 在正常 fixture 里返回空列表（= 读取成功但没品类），
        # 所以「读取失败」这条分支在任何宽度下都没被渲染过。而它恰恰是新建产品页
        # 和详情页选品类的唯一入口：读失败时下拉是空的，如果提示再挤爆窄屏或跑出
        # 卡片，用户就只剩「没有品类可选」这一种解释（原来的行为正是如此）。
        # 这里单独把该接口打成 500，只量这一条提示是否可见、在卡片内、不撑破页面。
        failing.add('/api/fee-categories')
        try:
            for width,height in ((390,680),(320,568)):
                page.set_viewport_size({'width':width,'height':height})
                path='/products/'+PRODUCT['id']
                print(f'Checking {width} {path} (fee categories failing)',flush=True)
                page.goto(BASE+path)
                try: page.get_by_role('heading',name='多用途收纳包',exact=False).first.wait_for(timeout=8000)
                except Exception: failures.append(f'{width} {path}: missing heading (degraded)');continue
                page.locator('.loading').wait_for(state='hidden')
                alert=page.get_by_role('alert')
                if alert.count()!=1: failures.append(f'{width}: fee category failure shows no alert');continue
                if not alert.is_visible(): failures.append(f'{width}: fee category alert hidden')
                text=alert.inner_text()
                if '不是没有品类' not in text: failures.append(f'{width}: fee category alert text lost the point: {text!r}')
                box=alert.bounding_box(); card=page.locator('.card').filter(has=alert).first.bounding_box()
                if box['x']<0 or box['x']+box['width']>width+1: failures.append(f'{width}: fee category alert clipped {box}')
                if card and (box['x']<card['x']-1 or box['x']+box['width']>card['x']+card['width']+1): failures.append(f'{width}: fee category alert outside its card')
                retry=alert.get_by_role('button',name='重新读取')
                b=retry.bounding_box()
                if not b or b['x']<0 or b['x']+b['width']>width+1 or b['y']<0: failures.append(f'{width}: fee category retry unreachable {b}')
                if page.evaluate('()=>document.documentElement.scrollWidth')>width+2: failures.append(f'{width}: fee category alert causes page overflow')
                if args.screenshots: page.screenshot(path=str(Path(args.screenshots)/f'fee-error-{width}.png'))
                # 编辑态：品类表读不到时，下拉必须继续显示库里已有的品类。
                # 原来下拉只由品类表生成，读失败就只剩「-- 请选择 --」，
                # 屏幕上看起来像这件货没设过品类（实际有值，保存也不会丢）。
                page.goto(BASE+path+'?edit=1')
                try: page.get_by_role('heading',name='多用途收纳包',exact=False).first.wait_for(timeout=8000)
                except Exception: failures.append(f'{width} {path}: missing heading (degraded edit)');continue
                page.locator('.loading').wait_for(state='hidden')
                select=page.locator('select').filter(has=page.locator('option',has_text='请选择')).first
                if select.count()!=1: failures.append(f'{width}: fee category select missing in edit mode');continue
                if select.input_value()!=PRODUCT['fee_category']:
                    failures.append(f"{width}: fee category select lost the stored value ({select.input_value()!r})")
                if '当前值' not in select.locator('option:checked').inner_text():
                    failures.append(f'{width}: stored fee category not labelled as current value')
                if page.evaluate('()=>document.documentElement.scrollWidth')>width+2: failures.append(f'{width}: fee category edit select causes page overflow')
        finally:
            failing.clear()
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
