// 真实浏览器窗口布局检查（320 / 768 / 1366）——只读，不触发任何写操作。
// 用法：
//   1) npx vite --port 5177 --strictPort      # 另开一个终端
//   2) npm i -D playwright && npx playwright install chromium   # 浏览器需已缓存
//   3) WIN_CHECK_BASE=http://localhost:5177 node scripts/check-window-layout.cjs
// 输出：window-check/*.png 截图 + window-check/report.json，控制台打印摘要。
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

const BASE = process.env.WIN_CHECK_BASE || 'http://localhost:5177';
const ROUTES = ['/', '/inventory', '/auto-pricing', '/products', '/procurement',
  '/packing', '/replenishment', '/create', '/stores', '/settings', '/products/1'];
const WIDTHS = [320, 768, 1366];
const OUT = path.resolve(__dirname, '..', 'window-check');
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const report = { browser: browser.version(), base: BASE, results: [] };
  for (const width of WIDTHS) {
    const ctx = await browser.newContext({ viewport: { width, height: 800 } });
    const page = await ctx.newPage();
    const errs = [];
    page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 120)); });
    for (const route of ROUTES) {
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForTimeout(800);
      const layout = await page.evaluate(() => {
        const de = document.documentElement;
        const nav = [...document.querySelectorAll('nav a, header a')];
        const inView = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.left >= -1 && r.right <= window.innerWidth + 1; };
        return {
          pageOverflow: de.scrollWidth > window.innerWidth + 2,
          scrollWidth: de.scrollWidth, innerWidth: window.innerWidth,
          navCount: nav.length, navVisible: nav.filter(inView).length,
          buttons: document.querySelectorAll('button').length
        };
      });
      let table = null;
      const wrap = await page.$('.inventory-table-wrap');
      if (wrap) {
        table = await page.evaluate(() => {
          const w = document.querySelector('.inventory-table-wrap');
          w.scrollLeft = w.scrollWidth;
          const last = [...document.querySelectorAll('th')].pop();
          const r = last ? last.getBoundingClientRect() : null;
          return {
            clientWidth: w.clientWidth, scrollWidth: w.scrollWidth,
            horizontallyScrollable: w.scrollWidth > w.clientWidth + 1,
            lastColumn: last ? (last.textContent || '').trim() : null,
            lastColumnReachableAfterScroll: !!r && r.left >= -2 && r.right <= window.innerWidth + 2
          };
        });
        await page.screenshot({ path: path.join(OUT, `scrolled_${width}_inventory.png`) });
      }
      await page.screenshot({ path: path.join(OUT, `${width}${route.replace(/\//g, '_') || '_root'}.png`) });
      report.results.push({ width, route, ...layout, table });
    }
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));
  for (const width of WIDTHS) {
    const rows = report.results.filter(r => r.width === width);
    const bad = rows.filter(r => r.pageOverflow || r.navVisible < r.navCount);
    const t = rows.find(r => r.table)?.table;
    console.log(`width=${width} pages=${rows.length} 整体溢出/导航遮挡=${bad.length}` +
      (t ? ` | 库存表 ${t.clientWidth}->${t.scrollWidth} 可横滚=${t.horizontallyScrollable} 末列"${t.lastColumn}"滚动后可达=${t.lastColumnReachableAfterScroll}` : ''));
  }
  console.log('browser', report.browser, '| 产物 ->', OUT);
})().catch(e => { console.error(e); process.exitCode = 1; });
