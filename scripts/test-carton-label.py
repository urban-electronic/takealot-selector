"""逐箱清单 + 箱唛：真实浏览器断言（不是截图看看而已）。

为什么需要这条用例：逐箱清单的价值全在「箱唛印出来和箱子里的东西一致」。
这件事只在浏览器里成立——中文排版、分页、打印时哪些元素被隐藏，都是 CSS 行为，
后端 pytest 测不到。这里钉死四件事：

  1) 「按 SKU 展开」把「2 箱 × 10 件 + 3 箱 × 5 件」展开成 5 箱，每箱数量对；
  2) 混装多塞一个 SKU 后，合计对不上要当场警告（箱子里写 20、装箱单 15）；
  3) 打印时 #root 必须 display:none —— 用 visibility 隐藏会保留布局高度，
     5 张箱唛会印出 14 页；
  4) 每张箱唛 100×70mm、一页一张、张数等于箱数；
  5) 事后查箱：按日期查得到当天全部箱，按 SKU 反查只列装了它的箱，
     但「共 N 箱」仍是整票总数；
  6) 重印不能重排 —— 从记录里挑第 3、4 箱重印，箱唛上必须还是
     「第 3 箱 / 共 5 箱」「第 4 箱 / 共 5 箱」，不能变成「第 1 箱 / 共 2 箱」，
     否则同一箱会印出两个箱号；
  7) 没展开就导出时，箱是后端自动展开的、草稿已清空、「打印箱唛」是灰的，
     提示条必须给出「去印箱唛」直达入口，否则用户以为印不了。

窄屏 320px 不许横向溢出（逐箱面板有「箱号/备注/第几箱」一行）。
"""

import json
import subprocess
import time
from pathlib import Path
from urllib.request import build_opener, ProxyHandler

from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5173'
ROOT = Path(__file__).resolve().parents[1]

PACKS = [
    dict(sku='9902348623136', name='皮革黑色包', name_zh='皮革黑色包', name_en='Leather bag',
         image_file='', default_count=10, material='皮革', electric='否', magnetic='否',
         unit='个', weight='0.3', brand='CK', battery=''),
    dict(sku='9902348621873', name='宝马后视镜盖', name_zh='宝马后视镜盖', name_en='Mirror cover',
         image_file='', default_count=5, material='塑料', electric='否', magnetic='否',
         unit='个', weight='0.2', brand='BMW', battery=''),
]
STORE = dict(id='default-store', name='代理CK店铺', platform='Takealot', status='active',
             owner_name='', sync_method='manual', sync_interval_minutes=60,
             external_store_ref='', allow_negative_inventory_shipments=False, last_synced_at=None)
DATA = {
    '/api/stores': [STORE],
    '/api/packing/products': PACKS,
    '/api/packing/sync-from-products': {'ok': True, 'total_products': 2, 'skipped_no_sku': 0, 'duplicate_sku': 0},
    '/api/inventory': [],
    '/api/inventory/shipments': [],
    '/api/inventory/adjustments': [],
    '/api/settings': {'cny_per_zar': '0.41'},
    '/api/market-prices/fx': {'base': 'ZAR', 'rates': {'SGD': 12.8345, 'CNY': 2.4453},
                              'at': '2026-10-22', 'src': 'fixture',
                              'fetched_at': '2026-10-22T00:00:00+00:00', 'stale': False, 'fallback': False},
}

# 一票 5 箱：C01..C03 是包（各 10 件），C04..C05 是后视镜盖（各 5 件）。
# 按 SKU 查时只返回装了它的箱，但 total 仍是 5 —— 箱唛要印「第 4 箱 / 共 5 箱」。
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

    def check(ok, message):
        if not ok:
            failures.append(message)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.add_init_script("localStorage.setItem('api_base_url', 'http://127.0.0.1:5173')")

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

        # 1) 按 SKU 展开
        page.get_by_role('button', name='按 SKU 展开').click()
        page.wait_for_timeout(400)
        cartons = page.locator('.packing-carton-item')
        check(cartons.count() == 5, f'按 SKU 展开应得 5 箱（2+3），实得 {cartons.count()}')
        check(page.locator('.packing-carton-warn').count() == 0, '刚展开时合计应该是对的，不该出现警告')
        check(page.locator('.packing-carton-total').inner_text().startswith('5 箱'),
              f'标题应显示 5 箱，实得 {page.locator(".packing-carton-total").inner_text()}')
        box_nos = [cartons.nth(i).locator('input').nth(0).input_value() for i in range(cartons.count())]
        check(box_nos == ['C01', 'C02', 'C03', 'C04', 'C05'], f'箱号应自动编号 C01..C05，实得 {box_nos}')
        seq_text = cartons.nth(2).locator('.packing-carton-seq').inner_text()
        check('第 3 箱' in seq_text and '共 5 箱' in seq_text, f'序号应显示「第 3 箱 / 共 5 箱」，实得 {seq_text}')

        # 2) 混装：第 1 箱再塞一个 SKU ×5 → 该 SKU 箱内 20 / 装箱单 15
        cartons.nth(0).get_by_role('button', name='这箱再加一个产品').click()
        page.wait_for_timeout(300)
        line = cartons.nth(0).locator('.packing-carton-line').nth(1)
        line.locator('select').select_option('9902348621873')
        line.locator('input[type=number]').fill('5')
        page.wait_for_timeout(400)
        warn = page.locator('.packing-carton-warn')
        check(warn.count() == 1, '混装多塞之后必须出现合计警告')
        if warn.count():
            text = warn.inner_text()
            check('20' in text and '15' in text, f'警告要写出「箱内 20 / 装箱单 15」，实得 {text}')

        # 3) 箱唛：张数、打印时隐藏 #root
        page.get_by_role('button', name='打印箱唛').click()
        page.wait_for_timeout(500)
        labels = page.locator('.carton-label')
        check(labels.count() == 5, f'箱唛张数应等于箱数 5，实得 {labels.count()}')
        first_label = labels.first
        check('C01' in first_label.inner_text(), '第一张箱唛要印箱号 C01')
        check('第 1 箱' in first_label.inner_text() and '共 5 箱' in first_label.inner_text(),
              '第一张箱唛要印「第 1 箱 / 共 5 箱」')
        check('皮革黑色包' in first_label.inner_text(), '箱唛必须能印中文品名（HTML 打印的意义就在这）')

        page.emulate_media(media='print')
        page.wait_for_timeout(300)
        root_display = page.evaluate("() => getComputedStyle(document.getElementById('root')).display")
        check(root_display == 'none', f'打印时 #root 必须 display:none，实得 {root_display}')
        label_visible = page.evaluate(
            "() => { const el = document.querySelector('.carton-label'); const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 }")
        check(label_visible, '打印时箱唛必须可见')

        pdf_path = ROOT / 'probe_carton' / 'test-carton-labels.pdf'
        pdf_path.parent.mkdir(exist_ok=True)
        page.pdf(path=str(pdf_path), width='100mm', height='70mm', print_background=True)

        page.emulate_media(media='screen')
        page.get_by_role('button', name='关闭', exact=True).click()
        page.wait_for_timeout(200)

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

        # 6) 重印不重排：挑第 3、4 箱，箱唛必须还是 3/5、4/5
        qrows.nth(2).locator('input[type=checkbox]').check()
        qrows.nth(3).locator('input[type=checkbox]').check()
        page.wait_for_timeout(200)
        page.get_by_role('button', name='重印选中箱唛').click()
        page.wait_for_timeout(500)
        rlabels = page.locator('.carton-label')
        check(rlabels.count() == 2, f'重印应出 2 张，实得 {rlabels.count()}')
        t1 = rlabels.nth(0).inner_text()
        t2 = rlabels.nth(1).inner_text()
        check('第 3 箱' in t1 and '共 5 箱' in t1, f'重印第 1 张应保持「第 3 箱 / 共 5 箱」，实得 {t1!r}')
        check('第 4 箱' in t2 and '共 5 箱' in t2, f'重印第 2 张应保持「第 4 箱 / 共 5 箱」，实得 {t2!r}')
        check('第 1 箱' not in t1, '重印不能把箱号重排成「第 1 箱」——同一箱会出现两个箱号')
        check('C03' in t1 and 'C04' in t2, f'重印箱号应为 C03 / C04，实得 {t1!r} / {t2!r}')

        page.emulate_media(media='print')
        page.wait_for_timeout(300)
        root_display = page.evaluate("() => getComputedStyle(document.getElementById('root')).display")
        check(root_display == 'none', '重印打印时 #root 也要 display:none（查询弹层在 #root 里，会被一起藏掉）')
        reprint_pdf = ROOT / 'probe_carton' / 'test-carton-reprint.pdf'
        page.pdf(path=str(reprint_pdf), width='100mm', height='70mm', print_background=True)
        page.emulate_media(media='screen')
        page.wait_for_timeout(200)

        # 7) 按 SKU 反查：只列装了它的箱，但「共 N 箱」仍是整票总数
        page.locator('.carton-print-toolbar').get_by_role('button', name='关闭').click()
        page.wait_for_timeout(300)
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

        # 4) 窄屏不许横向溢出
        page.set_viewport_size({'width': 320, 'height': 900})
        page.wait_for_timeout(500)
        overflow = page.evaluate(
            "() => { const el = document.querySelector('.packing-carton-card'); return el ? el.scrollWidth - el.clientWidth : -1 }")
        check(overflow == 0, f'320px 下逐箱面板横向溢出 {overflow}px')

        # 8) 没展开就导出：后端会自动展开箱，前端草稿已清空，
        #    「打印箱唛」是灰的 —— 必须给一个「去印箱唛」的直达入口，否则用户以为印不了
        page.set_viewport_size({'width': 1440, 'height': 1000})
        # 前面第 2 步故意留了「箱内 20 / 装箱单 15」的不一致，导出会被闸拦住；
        # 这里清掉草稿重来，模拟「新开一票、什么都没展开就直接导出」
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
        # 刻意不点「按 SKU 展开」，直接导出
        page.locator('.packing-export-btn').first.click()
        page.wait_for_timeout(1500)
        success = page.locator('.alert-success')
        check(success.count() >= 1, '导出后应有成功提示')
        if success.count():
            txt = success.first.inner_text()
            check('自动展开 5 箱' in txt, f'提示要说明已自动展开 5 箱，实得 {txt!r}')
            btn = success.first.get_by_role('button', name='去印箱唛')
            check(btn.count() == 1, '导出后要给「去印箱唛」直达按钮')
            if btn.count():
                btn.click()
                page.wait_for_timeout(800)
                check(page.locator('.carton-query-row').count() == 5,
                      f'点「去印箱唛」应打开查箱子并列出 5 箱，实得 {page.locator(".carton-query-row").count()}')

        browser.close()

    # PDF 页数与尺寸（Chromium 会把 100mm 舍入成 100.2mm，容差 1mm）
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        check(doc.page_count == 5, f'箱唛 PDF 应 5 页（5 箱），实得 {doc.page_count} 页')
        for i, page_obj in enumerate(doc):
            w = page_obj.rect.width / 72 * 25.4
            h = page_obj.rect.height / 72 * 25.4
            check(abs(w - 100) < 1 and abs(h - 70) < 1, f'第 {i + 1} 页尺寸应为 100×70mm，实得 {w:.1f}×{h:.1f}mm')
        doc.close()

        # 重印是另一个 overlay：打印时同样只能剩下箱唛，张数等于勾选数
        if reprint_pdf.exists():
            doc = pymupdf.open(str(reprint_pdf))
            check(doc.page_count == 2, f'重印 PDF 应 2 页（勾了 2 箱），实得 {doc.page_count} 页')
            doc.close()
        else:
            check(False, '重印没有生成 PDF')
    except ImportError:
        pass

    report = {'checked': 8, 'failures': failures, 'page_errors': page_errors}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return failures, page_errors


if __name__ == '__main__':
    server = subprocess.Popen(
        ['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
    try:
        for _ in range(80):
            try:
                build_opener(ProxyHandler({})).open(BASE, timeout=1).close()
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Vite test server failed to start')
                time.sleep(0.1)
        else:
            raise RuntimeError('Vite test server did not become ready')
        failures, errors = run()
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert not failures and not errors, {'failures': failures, 'page_errors': errors}
