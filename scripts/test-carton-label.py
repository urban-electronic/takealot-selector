"""逐箱清单 + 箱唛：真实浏览器断言（不是截图看看而已）。

为什么需要这条用例：逐箱清单的价值全在「箱唛印出来和箱子里的东西一致」。
这件事只在浏览器里成立——中文排版、分页、打印时哪些元素被隐藏，都是 CSS 行为，
后端 pytest 测不到。这里钉死四件事：

  1) 「按 SKU 展开」把「2 箱 × 10 件 + 3 箱 × 5 件」展开成 5 箱，每箱数量对；
  2) 混装多塞一个 SKU 后，合计对不上要当场警告（箱子里写 20、装箱单 15）；
  3) 打印时 #root 必须 display:none —— 用 visibility 隐藏会保留布局高度，
     5 张箱唛会印出 14 页；
  4) 每张箱唛 100×70mm、一页一张、张数等于箱数。

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
            from urllib.parse import urlsplit
            url = urlsplit(route.request.url)
            if url.path.startswith('/api/'):
                if route.request.method != 'GET':
                    route.fulfill(status=200, json={'ok': True})
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

        # 4) 窄屏不许横向溢出
        page.set_viewport_size({'width': 320, 'height': 900})
        page.wait_for_timeout(500)
        overflow = page.evaluate(
            "() => { const el = document.querySelector('.packing-carton-card'); return el ? el.scrollWidth - el.clientWidth : -1 }")
        check(overflow == 0, f'320px 下逐箱面板横向溢出 {overflow}px')

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
    except ImportError:
        pass

    report = {'checked': 4, 'failures': failures, 'page_errors': page_errors}
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
