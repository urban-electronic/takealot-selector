"""逐箱清单 → OMS 入仓单：真实浏览器断言（不是截图看看而已）。

背景：箱唛不是我们出的，是 OMS（鲲鹏 JF WMS）建完入库单自己打的，而且
**箱唛上本来就有「SKU / 单箱数量」那张表**。所以我们的活只有一件：
把装箱时录的逐箱清单，变成 OMS 建入库单要导入的那份 xlsx。

这条用例钉死「串起来」的那几处：

  1) 只填「箱数 × 每箱件数」，逐箱清单**自动**长出来（2 箱 × 10 件 + 3 箱 × 5 件 = 5 箱），
     不用点按钮，也不用把同一份数量再抄一遍；自动模式下逐箱清单是只读的；
  2) 填 OMS 入库单号后，箱号变成 OMS 的箱唛号（INH1501522-001），
     这是我们的记录和 OMS 打的箱唛唯一能对上的凭据；
  3) 混装：点「手动调整」接管清单，往第 1 箱多塞一个 SKU 后，
     「已选产品」那行自动变成「4 箱 · 20 件」，**不再弹「合计对不上」**；
  4) 一箱超过 5 个 SKU 要标出来 —— OMS 箱唛一张只放得下 5 行，
     第 6 行起会另起一页，而那一页没有箱号、没有条码，贴到箱上认不出是哪箱；
  5) 事后查箱：按日期查得到当天全部箱，按 SKU 反查只列装了它的箱，
     但「共 N 箱」仍是整票总数；
  6) 导出后提示条必须给出「导出 OMS 入仓模板」直达入口，并默认勾上刚导出的那一票；
  7) 逐箱清单的产品下拉永远列得出候选（草稿恢复后勾选丢了也不会变成空下拉）；
  8) 唛头跟着入库单号走 —— 模板里「客户唛头」和 A 列「箱唛」是同一套编号；
  9) 装箱单日期默认「做单那天」，不是点导出那天；老草稿里残留的旧日期不算数，
     只有**手改过**的日期才跟着草稿走。

窄屏 320px 不许横向溢出（逐箱面板有「箱号/备注/第几箱」一行）。
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_server import vite_server  # noqa: E402  （必须先插 sys.path）

BASE = 'http://127.0.0.1:5173'  # 默认值；__main__ 会换成现挑的空闲端口（见 _audit_server.py）
ROOT = Path(__file__).resolve().parents[1]

PACKS = [
    dict(sku='9902348623136', name='皮革黑色包', name_zh='皮革黑色包', name_en='Leather bag',
         image_file='', default_count=10, material='皮革', electric='否', magnetic='否',
         unit='个', weight='0.3', brand='CK', battery=''),
    dict(sku='9902348621873', name='宝马后视镜盖', name_zh='宝马后视镜盖', name_en='Mirror cover',
         image_file='', default_count=5, material='塑料', electric='否', magnetic='否',
         unit='个', weight='0.2', brand='BMW', battery=''),
]
# 再补 5 个只用来「往同一箱里塞到 6 个 SKU」的产品：OMS 箱唛 5 行是硬上限。
for _n in range(5):
    PACKS.append(dict(sku=f'99000000000{_n}', name=f'填充品 {_n}', name_zh=f'填充品 {_n}',
                      name_en=f'Filler {_n}', image_file='', default_count=1, material='',
                      electric='否', magnetic='否', unit='个', weight='0.1', brand='', battery=''))

STORE = dict(id='default-store', name='代理CK店铺', platform='Takealot', status='active',
             owner_name='', sync_method='manual', sync_interval_minutes=60,
             external_store_ref='', allow_negative_inventory_shipments=False, last_synced_at=None)

SHIPMENTS = [dict(id='ship-1', shipment_no='PK-20261022-001', shipment_date='2026-10-22',
                  mark='CK-01', shipping='海运', address='', status='exported',
                  created_at='2026-10-22T09:00:00', total_skus=2, total_quantity=35)]

DATA = {
    '/api/stores': [STORE],
    '/api/packing/products': PACKS,
    '/api/packing/sync-from-products': {'ok': True, 'total_products': len(PACKS), 'skipped_no_sku': 0, 'duplicate_sku': 0},
    '/api/inventory': [],
    '/api/inventory/shipments': SHIPMENTS,
    '/api/inventory/adjustments': [],
    '/api/settings': {'cny_per_zar': '0.41'},
    '/api/market-prices/fx': {'base': 'ZAR', 'rates': {'SGD': 12.8345, 'CNY': 2.4453},
                              'at': '2026-10-22', 'src': 'fixture',
                              'fetched_at': '2026-10-22T00:00:00+00:00', 'stale': False, 'fallback': False},
}

# 一票 5 箱：C01..C03 是包（各 10 件），C04..C05 是后视镜盖（各 5 件）。
# 按 SKU 查时只返回装了它的箱，但 total 仍是 5。
CARTON_PLAN = [
    ('C01', 1, '9902348623136', '皮革黑色包', 'Leather bag', 10),
    ('C02', 2, '9902348623136', '皮革黑色包', 'Leather bag', 10),
    ('C03', 3, '9902348623136', '皮革黑色包', 'Leather bag', 10),
    ('C04', 4, '9902348621873', '宝马后视镜盖', 'Mirror cover', 5),
    ('C05', 5, '9902348621873', '宝马后视镜盖', 'Mirror cover', 5),
]


def carton_records(sku=''):
    rows = []
    for box_no, seq, s, zh, en, qty in CARTON_PLAN:
        if sku and s != sku:
            continue
        rows.append(dict(
            shipment_id='ship-1', shipment_no='SHIP-20261022-01', shipment_date='2026-10-22',
            mark='CK-01', seq=seq, total=5, box_no=box_no, note='',
            items=[dict(sku=s, name_zh=zh, name_en=en, quantity=qty)],
        ))
    # total_cartons 忠实于后端：它是「本次返回多少箱」= len(rows)，按 SKU 筛时会变小。
    # 这里千万别写死 5 —— fixture 比后端宽容，就会把「共 N 箱」的文案矛盾遮住。
    return {'date': '2026-10-22', 'total_cartons': len(rows), 'cartons': rows}


def run():
    failures = []
    page_errors = []
    # 断言条数自己数，别再手写一个数字 —— 加了两条却忘了改，报告就成了假的。
    # 名字不能叫 checked：下面「入仓模板弹层」那段有个同名 Locator。
    assertions = 0

    def check(ok, message):
        nonlocal assertions
        assertions += 1
        if not ok:
            failures.append(message)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.add_init_script(f"localStorage.setItem('api_base_url', '{BASE}')")

        def intercept(route):
            from urllib.parse import urlsplit, parse_qs
            url = urlsplit(route.request.url)
            if url.path.startswith('/api/'):
                if route.request.method != 'GET':
                    route.fulfill(status=200, json={'ok': True})
                    return
                if url.path == '/api/packing/cartons':
                    qs = parse_qs(url.query)
                    route.fulfill(json=carton_records((qs.get('sku') or [''])[0]))
                    return
                if url.path in DATA:
                    route.fulfill(json=DATA[url.path])
                    return
                route.fulfill(status=404, json={'detail': 'fixture endpoint not declared'})
                return
            route.continue_()

        context.route('**/*', intercept)
        page = context.new_page()
        page.on('pageerror', lambda e: page_errors.append(str(e)))

        page.goto(BASE + '/packing', wait_until='networkidle')
        page.wait_for_timeout(600)

        # 勾两个产品：第一个 2 箱 × 10 件，第二个 3 箱 × 5 件
        boxes = page.locator('.packing-table-scroll input[type=checkbox]')
        boxes.nth(0).check()
        boxes.nth(1).check()
        page.wait_for_timeout(300)
        first = page.locator('.packing-selected-item').nth(0)
        first.locator('input[type=number]').nth(0).fill('2')
        first.locator('input[type=number]').nth(1).fill('10')
        second = page.locator('.packing-selected-item').nth(1)
        second.locator('input[type=number]').nth(0).fill('3')
        second.locator('input[type=number]').nth(1).fill('5')
        page.wait_for_timeout(200)

        # 1) 只填箱数 × 每箱件数：逐箱清单自动长出来，不用点按钮、不用再抄一遍
        cartons = page.locator('.packing-carton-item')
        check(cartons.count() == 5, f'填完箱数应自动生成 5 箱（2+3），实得 {cartons.count()}')
        check(page.locator('.packing-carton-warn').count() == 0, '刚填完不该出现警告')
        check(page.locator('.packing-carton-total').inner_text().startswith('5 箱'),
              f'标题应显示 5 箱，实得 {page.locator(".packing-carton-total").inner_text()}')
        box_nos = [cartons.nth(i).locator('input').nth(0).input_value() for i in range(cartons.count())]
        check(box_nos == ['C01', 'C02', 'C03', 'C04', 'C05'], f'箱号应自动编号 C01..C05，实得 {box_nos}')
        seq_text = cartons.nth(2).locator('.packing-carton-seq').inner_text()
        check('第 3 箱' in seq_text and '共 5 箱' in seq_text, f'序号应显示「第 3 箱 / 共 5 箱」，实得 {seq_text}')
        # 自动模式下逐箱清单只读 —— 同一份数量只留一个录入处，两边都能改就必然打架
        check(cartons.nth(0).locator('input').nth(0).is_disabled(), '自动模式下箱号应是只读的')
        check(cartons.nth(0).locator('.packing-carton-line input').first.is_disabled(), '自动模式下件数应是只读的')

        # 2) 填 OMS 入库单号：箱号必须自动变成 OMS 的箱唛号（INH1501522-001），
        #    这是我们的记录和 OMS 打的箱唛唯一能对上的凭据
        page.locator('.packing-carton-inbound input').fill('INH1501522')
        page.wait_for_timeout(400)
        box_nos2 = [cartons.nth(i).locator('input').nth(0).input_value() for i in range(cartons.count())]
        want2 = [f'INH1501522-{i:03d}' for i in range(1, 6)]
        check(box_nos2 == want2, f'填入库单号后箱号应变成 {want2}，实得 {box_nos2}')
        hint = page.locator('.packing-carton-inbound-hint').inner_text()
        check('INH1501522-001' in hint, f'提示要显示箱唛号范围，实得 {hint!r}')
        # 唛头必须跟着入库单号走：模板里「客户唛头」和 A 列「箱唛」是同一套编号，
        # 只填一次入库单号，两处就该自动一致。
        mark_value = page.locator('.packing-export-grid input[type=text]').nth(0).input_value()
        check(mark_value == 'INH1501522', f'唛头应自动同入库单号，实得 {mark_value!r}')

        # 3) 混装：先「手动调整」接管清单，再往第 1 箱多塞一个 SKU ×5。
        #    这时不该再弹「合计对不上」—— 数量以逐箱清单为准，「已选产品」那行跟着汇总。
        page.get_by_role('button', name='手动调整（混装 / 拆箱）').click()
        page.wait_for_timeout(300)
        cartons.nth(0).get_by_role('button', name='这箱再加一个产品').click()
        page.wait_for_timeout(300)
        line = cartons.nth(0).locator('.packing-carton-line').nth(1)
        # 空行必须列得出候选：下拉只列「已勾选产品」时，草稿恢复后勾选一丢
        # 就是个空下拉，点开什么都没有，混装根本加不进来。
        check(line.locator('select option').count() > 1,
              f'逐箱清单的产品下拉不能是空的，实得 {line.locator("select option").count()} 项')
        line.locator('select').select_option('9902348621873')
        line.locator('input[type=number]').fill('5')
        page.wait_for_timeout(400)
        check(page.locator('.packing-carton-warn').count() == 0, '混装后不该再出现「合计对不上」的警告')
        second_item = page.locator('.packing-selected-item').nth(1)
        check('4 箱' in second_item.inner_text() and '20 件' in second_item.inner_text(),
              f'第二个产品应汇总成「4 箱 · 20 件」（C03/C04/C05 各 5 + 第 1 箱 5），实得 {second_item.inner_text()!r}')

        # 4) 一箱塞到 6 个 SKU：必须出现「超出箱唛 5 行」的提示。
        #    OMS 箱唛第 6 行起另起一页，那页没有箱号也没有条码，贴到箱上认不出是哪箱。
        for n in range(2, 7):
            page.locator('.packing-table-scroll input[type=checkbox]').nth(n).check()
        page.wait_for_timeout(400)
        for n in range(4):
            cartons.nth(0).get_by_role('button', name='这箱再加一个产品').click()
            page.wait_for_timeout(120)
            row = cartons.nth(0).locator('.packing-carton-line').nth(n + 2)
            row.locator('select').select_option(f'99000000000{n}')
            row.locator('input[type=number]').fill('1')
        page.wait_for_timeout(400)
        overflow = page.locator('.packing-carton-overflow')
        check(overflow.count() == 1, f'第 1 箱塞到 6 个 SKU 后应出现超限提示，实得 {overflow.count()} 个')
        if overflow.count():
            text = overflow.first.inner_text()
            check('6 个 SKU' in text and '5 行' in text, f'超限提示要写清「6 个 SKU / 箱唛 5 行」，实得 {text!r}')

        # 5) 事后查箱：按日期查当天全部 5 箱
        page.get_by_role('button', name='查箱子').click()
        page.wait_for_timeout(600)
        qrows = page.locator('.carton-query-row')
        check(qrows.count() == 5, f'按日期查应得 5 箱，实得 {qrows.count()}')
        summary = page.locator('.carton-query-summary').inner_text()
        check('共 5 箱' in summary, f'查询摘要应写「共 5 箱」，实得 {summary!r}')
        row3 = qrows.nth(2).inner_text()
        check('第 3 箱' in row3 and '共 5 箱' in row3, f'第 3 行应是「第 3 箱 / 共 5 箱」，实得 {row3!r}')
        check('皮革黑色包' in row3, '查询结果要直接看出箱里装的是什么，不能只给箱号')

        # 6) 按 SKU 反查：只列装了它的箱，但「共 N 箱」仍是整票总数
        page.locator('.carton-query-filters input').nth(1).fill('9902348621873')
        page.get_by_role('button', name='查询', exact=True).click()
        page.wait_for_timeout(500)
        check(page.locator('.carton-query-row').count() == 2, f'按 SKU 查应只剩 2 箱，实得 {page.locator(".carton-query-row").count()}')
        sku_summary = page.locator('.carton-query-summary').inner_text()
        # 按 SKU 筛时后端 total_cartons 只是命中数，文案不能说「共 N 箱」否则自相矛盾
        check('找到 2 箱' in sku_summary and '整票总数' in sku_summary,
              f'按 SKU 查应说「找到 2 箱…整票总数」，实得 {sku_summary!r}')
        check('共 2 箱' not in sku_summary, f'按 SKU 查不能说「共 2 箱」（与「共 5 箱」矛盾），实得 {sku_summary!r}')
        sku_row = page.locator('.carton-query-row').nth(0).inner_text()
        check('第 4 箱' in sku_row and '共 5 箱' in sku_row,
              f'按 SKU 查到的第一箱应是「第 4 箱 / 共 5 箱」，实得 {sku_row!r}')
        page.locator('.carton-query-head').get_by_role('button', name='关闭').click()
        page.wait_for_timeout(200)

        # 7) 窄屏不许横向溢出
        page.set_viewport_size({'width': 320, 'height': 900})
        page.wait_for_timeout(500)
        doc_overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(doc_overflow == 0, f'320px 下整页横向溢出 {doc_overflow}px（头部按钮挤爆了？）')

        # 8) 没展开就导出：后端会自动展开箱，前端草稿已清空 ——
        #    提示条必须给「导出 OMS 入仓模板」直达入口，并默认勾上刚导出的那一票
        page.set_viewport_size({'width': 1440, 'height': 1000})
        # 清掉草稿重来，模拟「新开一票、填完箱数直接导出」
        page.evaluate("() => localStorage.clear()")
        page.reload(wait_until='networkidle')
        page.wait_for_timeout(700)
        boxes = page.locator('.packing-table-scroll input[type=checkbox]')
        boxes.nth(0).check()
        boxes.nth(1).check()
        page.wait_for_timeout(300)
        first = page.locator('.packing-selected-item').nth(0)
        first.locator('input[type=number]').nth(0).fill('2')
        first.locator('input[type=number]').nth(1).fill('10')
        second = page.locator('.packing-selected-item').nth(1)
        second.locator('input[type=number]').nth(0).fill('3')
        second.locator('input[type=number]').nth(1).fill('5')
        page.wait_for_timeout(200)
        # 运输方式现在是必选项（模板 G2 是「头程（空/海）」），不选不许导出 ——
        # 头程印错一行，货代就按错的渠道走。
        page.locator('.packing-export-grid select').select_option('海运')
        page.locator('.packing-export-btn').first.click()
        page.wait_for_timeout(1500)
        success = page.locator('.alert-success')
        check(success.count() >= 1, '导出后应有成功提示')
        if success.count():
            txt = success.first.inner_text()
            check('自动生成 5 箱' in txt, f'提示要说明逐箱清单已自动生成 5 箱，实得 {txt!r}')
            btn = success.first.get_by_role('button', name='导出 OMS 入仓模板')
            check(btn.count() == 1, '导出后要给「导出 OMS 入仓模板」直达按钮')
            if btn.count():
                btn.click()
                page.wait_for_timeout(800)
                # 弹层里最新那一票要默认勾上，省得再翻一遍列表
                checked = page.locator('.packing-modal input[type=checkbox]:checked')
                check(checked.count() == 1, f'入仓模板弹层应默认勾上刚导出的那一票，实得 {checked.count()} 个勾选')
                check(page.get_by_text('PK-20261022-001').count() >= 1, '弹层里应能看到刚导出的装箱单号')

        # 9) 装箱单日期 = **做单那天**，不是点导出那天。
        #    模拟「10-01 做的单、今天才导出」：草稿里只有 createdOn，没有 exportForm.date。
        #    同一个草稿里只填了入库单号，唛头也要跟着它 —— 模板里两处是同一套编号。
        page.evaluate("""() => {
          // 草稿键是 `packingDraftV1:<店铺ID>`，必须改到同一把键上；
          // 写到裸键上会被「已存在的店铺键」盖过去，等于没写。
          const key = Object.keys(localStorage).find(k => k.includes('packingDraftV1')) || 'packingDraftV1';
          localStorage.setItem(key, JSON.stringify({
            selected: [], lines: {}, exportForm: {}, draftKey: 'draft-old',
            cartons: [], cartonsManual: false, inboundNo: 'INH1501522', createdOn: '2026-10-01',
          }));
        }""")
        page.reload(wait_until='networkidle')
        page.wait_for_timeout(700)
        date_value = page.locator('.packing-export-grid input[type=date]').input_value()
        check(date_value == '2026-10-01', f'日期应默认做单那天 2026-10-01，实得 {date_value!r}')
        mark_value = page.locator('.packing-export-grid input[type=text]').nth(0).input_value()
        check(mark_value == 'INH1501522', f'草稿只有入库单号时唛头应补成它，实得 {mark_value!r}')

        # 9b) 老草稿里躺着的 exportForm.date 是「上一轮点导出那天」的残留，不能沿用。
        #     2026-10-23 线上实测：草稿里是 2026-10-15，而做单那天是 10-23，
        #     照搬就会把今天做的单印成上个月的日子。没手改过（dateTouched 缺失）就一律回到做单那天。
        page.evaluate("""() => {
          const key = Object.keys(localStorage).find(k => k.includes('packingDraftV1')) || 'packingDraftV1';
          localStorage.setItem(key, JSON.stringify({
            selected: [], lines: {}, exportForm: { date: '2026-09-30' }, draftKey: 'draft-old',
            cartons: [], cartonsManual: false, inboundNo: '', createdOn: '2026-10-01',
          }));
        }""")
        page.reload(wait_until='networkidle')
        page.wait_for_timeout(700)
        stale = page.locator('.packing-export-grid input[type=date]').input_value()
        check(stale == '2026-10-01',
              f'没手改过日期时不能用草稿里的旧日期，应回落到做单那天 2026-10-01，实得 {stale!r}')

        # 9c) 手改过就要认 —— 否则「今天做的单但要补成昨天的日期」永远改不动。
        page.locator('.packing-export-grid input[type=date]').fill('2026-10-02')
        page.wait_for_timeout(500)
        page.reload(wait_until='networkidle')
        page.wait_for_timeout(700)
        kept = page.locator('.packing-export-grid input[type=date]').input_value()
        check(kept == '2026-10-02', f'手改过的日期刷新后要保持 2026-10-02，实得 {kept!r}')

        browser.close()

    report = {'checked': assertions, 'failures': failures, 'page_errors': page_errors}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return failures, page_errors


if __name__ == '__main__':
    # 端口现挑：本机可能同时有另一份工作副本在跑 selfcheck，写死 5173 会互相探到/杀掉
    # 对方的服务（表现为随机 ERR_CONNECTION_REFUSED 或「按钮找不到」）。
    with vite_server(ROOT) as base:
        globals()['BASE'] = base
        failures, errors = run()
    assert not failures and not errors, {'failures': failures, 'page_errors': errors}
