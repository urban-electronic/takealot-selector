#!/usr/bin/env python3
"""前端视觉层审计（浏览器实测，产出可复核的逐节点证据）。

为什么需要它
------------
本脚本独立采纳自 WorkBuddy PR120/afd72fc，并修复请求隔离与验收失败退出。
仅运行本机前端与隔离fixture API，不读取Key、不连接生产。它测量默认渲染态：
用真实 Chromium 打开每条路由，逐个元素取**计算样式**，按 WCAG 2.1 公式算对比度，
把每个不达标节点（类名 / 字号 / 前景 / 背景 / 实测比值 / 文案）都记录下来。

用法
----
    # 1. 先起 dev server（任选其一）
    npx vite --port 5173 --strictPort

    # 2. 跑隔离审计，无需API key
    python scripts/ui_contrast_audit.py

依赖：playwright（Python 包）+ 已下载的 Chromium。
     pip install playwright && python -m playwright install chromium

输出
----
    ui_audit_out/audit.json   全量逐节点数据（每条路由一个键，如 "/products|w1366"）
     stdout                   每个路由的汇总行，便于直接贴进评审

判定口径
--------
    正文文字：< 4.5:1 记不达标；>=18.66px 且加粗、或 >=24px 的文字按 3:1（WCAG 2.1 AA）。
    有效对比度：祖先链上的 opacity 相乘后与背景混合再算比值。`opacity: .6` 弱化的
        12.5px 正文实际只有 3.65:1，按计算样式的 color 直算是 11.84:1——只看 color
        等于把这处不达标判成通过。
    停用控件：`disabled` / `aria-disabled="true"` 内的文字按 WCAG 1.4.3 例外单列计数，
        不计失败；但仍出现在汇总的「停用控件文字」列里，不从分母消失。
    控件：高度 < 28px 记为触达目标过小（WCAG 2.5.8 建议 24px，本项目取更严的 28px）。
    背景：祖先背景近似，不覆盖所有透明混合、hover、焦点、弹窗状态。
    未成功加载标题与关键fixture行、未知请求、低对比均退出非0。
"""
import asyncio
import json
import os
import runpy
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

BASE = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:5173")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "ui_audit_out")

ROUTES = [
    "/", "/products", "/inventory", "/auto-pricing", "/replenishment", "/packing",
    "/procurement", "/stores", "/settings", "/create", "/products/fixture-product",
]
VIEWPORTS = [("w1366", 1366, 900), ("w768", 768, 1000), ("w390", 390, 844)]

FIXTURES = runpy.run_path(os.path.join(HERE, 'test-responsive-layout.py'))
DATA = FIXTURES['DATA']
TITLES = dict(FIXTURES['PAGES'])
MOCK_POSTS = {'/api/buybox/page-open', '/api/packing/sync-from-products'}

def local_origin(base):
    value = urlsplit(base)
    if value.scheme != 'http' or value.hostname not in ('127.0.0.1', 'localhost', '::1') or value.username or value.password:
        raise ValueError('审计仅允许本机HTTP测试服务')
    return value.scheme, value.netloc

INIT = """
try {
  localStorage.setItem('api_base_url', %r);
  localStorage.setItem('active_store_id', 'default-store');
  localStorage.removeItem('api_key');
} catch (e) {}
""" % BASE

AUDIT = r"""
() => {
  const px = v => parseFloat(v) || 0;
  const lum = ([r,g,b]) => { const f=c=>{c/=255;return c<=0.03928?c/12.92:Math.pow((c+0.055)/1.055,2.4)}; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b); };
  const rgb = s => { const m=(s||'').match(/[\d.]+/g); if(!m) return null; return [ +m[0],+m[1],+m[2], m[3]!==undefined? +m[3]:1 ]; };
  const effBg = el => {
    let n = el;
    while (n && n !== document.documentElement) {
      const c = rgb(getComputedStyle(n).backgroundColor);
      if (c && c[3] > 0.95) return c;
      n = n.parentElement;
    }
    return [255,255,255,1];
  };
  const ratio = (fg,bg) => { const l1=lum(fg), l2=lum(bg); const a=Math.max(l1,l2), b=Math.min(l1,l2); return (a+0.05)/(b+0.05); };
  const cls = el => el.tagName.toLowerCase() + (typeof el.className==='string' && el.className ? '.'+el.className.trim().split(/\s+/).slice(0,2).join('.') : '');
  // 祖先链上 opacity 是相乘的：`opacity: .6` 弱化的文字，计算样式里的 color 一点没变，
  // 真正画到屏幕上的颜色是「前景 × α + 背景 × (1-α)」。原来只看自身 opacity 是否等于 0，
  // 于是 0.6 透明度的过期参考价按 11.84:1 判过，实际只有 3.65:1。
  const alphaChain = el => {
    let a = 1, n = el;
    while (n && n !== document.documentElement) { a *= +getComputedStyle(n).opacity; n = n.parentElement; }
    return a;
  };
  const blend = (fg, bg, a) => [ fg[0]*a + bg[0]*(1-a), fg[1]*a + bg[1]*(1-a), fg[2]*a + bg[2]*(1-a) ];
  // 非活动控件按 WCAG 2.1 1.4.3 例外单列：它不计失败，但必须计数出现在报告里，
  // 不能像以前那样「量了但按未弱化算」，也不能悄悄从分母里消失。
  const inactive = el => !!el.closest('[disabled],[aria-disabled="true"]');

  const lowContrast = [];
  const inactiveLowContrast = [];
  const fontSizes = {};
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const alpha = alphaChain(el);
    if (alpha < 0.01) return;
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
    if (!own) return;
    const fs = px(cs.fontSize);
    fontSizes[fs] = (fontSizes[fs] || 0) + 1;
    const fg = rgb(cs.color); const bg = effBg(el);
    if (fg && bg) {
      const cr = ratio(alpha >= 0.999 ? fg : blend(fg, bg, alpha), bg);
      const need = (fs >= 24 || (fs >= 18.66 && +cs.fontWeight >= 700)) ? 3 : 4.5;
      if (cr < need) {
        const node = { cls: cls(el), fs, ratio: Math.round(cr*100)/100, need, alpha: Math.round(alpha*100)/100,
          text: own.slice(0,28), color: cs.color, bg: `rgb(${bg[0]},${bg[1]},${bg[2]})` };
        (inactive(el) ? inactiveLowContrast : lowContrast).push(node);
      }
    }
  });

  const controls = { tooSmall: [], heights: {} };
  document.querySelectorAll('button, a, select, input:not([type=checkbox]):not([type=radio])').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const h = Math.round(r.height);
    controls.heights[h] = (controls.heights[h] || 0) + 1;
    if (h < 28 && r.width > 2) controls.tooSmall.push({ cls: cls(el), h, w: Math.round(r.width), text: (el.textContent||'').trim().slice(0,18) });
  });

  const ths = [];
  document.querySelectorAll('table thead th').forEach(th => { const cs = getComputedStyle(th); ths.push({ bg: cs.backgroundColor, fs: cs.fontSize, fw: cs.fontWeight, pad: cs.paddingTop }); });

  const header = document.querySelector('.app-header');
  const hh = header ? Math.round(header.getBoundingClientRect().height) : 0;
  return {
    vw: document.documentElement.clientWidth, vh: document.documentElement.clientHeight,
    headerH: hh, fontSizes,
    lowContrast, lowContrastCount: lowContrast.length,
    inactiveLowContrast, inactiveLowContrastCount: inactiveLowContrast.length,
    controls: { heights: controls.heights, tooSmall: controls.tooSmall, tooSmallCount: controls.tooSmall.length },
    ths,
  };
}
"""


async def intercept(route, errors):
    request = route.request
    url = urlsplit(request.url)
    if (url.scheme, url.netloc) != local_origin(BASE):
        errors.append('external request blocked')
        await route.abort()
        return
    if url.path.startswith('/api/'):
        allowed = request.method in ('GET', 'HEAD') or request.method == 'POST' and url.path in MOCK_POSTS
        if not allowed or url.path not in DATA:
            errors.append(request.method + ' ' + url.path)
            await route.abort()
            return
        await route.fulfill(status=200, content_type='application/json', body=json.dumps(DATA[url.path]))
        return
    if request.method not in ('GET', 'HEAD'):
        errors.append('non-read request blocked')
        await route.abort()
        return
    await route.continue_()


async def main():
    local_origin(BASE)
    os.makedirs(OUT, exist_ok=True)
    report = {}
    request_errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        for label, w, h in VIEWPORTS:
            ctx = await browser.new_context(viewport={"width": w, "height": h})
            await ctx.route('**/*', lambda route: intercept(route, request_errors))
            await ctx.add_init_script(INIT)
            for route in ROUTES:
                page = await ctx.new_page()
                try:
                    await page.goto(BASE + route, wait_until="domcontentloaded", timeout=30000)
                    await page.get_by_role('heading', name=TITLES[route], exact=False).first.wait_for(timeout=8000)
                    await page.locator('.loading').wait_for(state='hidden')
                    await page.wait_for_load_state("networkidle", timeout=20000)
                    if route == '/procurement':
                        # Procurement renders a product number, not the product SKU.
                        await page.locator('.procurement-table-scroll tbody').get_by_role(
                            'cell', name=str(FIXTURES['PROCUREMENT']['product_no']), exact=True
                        ).first.wait_for(timeout=8000)
                    elif route in ('/products', '/inventory', '/auto-pricing', '/packing'):
                        await page.get_by_text('TEST-001', exact=False).first.wait_for(timeout=8000)
                    if route == '/auto-pricing':
                        # 「更多」面板与每行「详情」都默认收起。不点开，里面那 8 个次要
                        # 动作和决策卡片（最长的一段文案 + 8 个 chip）就从来没被量过对比度，
                        # 而报告照样写「0 处低对比」。
                        await page.get_by_role('button', name='更多', exact=True).click()
                        await page.locator('.actions-more-panel').wait_for(timeout=8000)
                        await page.locator('.row-details').first.locator('summary').click()
                        await page.locator('.decision-card').first.wait_for(timeout=8000)
                    result = await page.evaluate(AUDIT)
                    if not isinstance(result, dict) or not isinstance(result.get('lowContrastCount'), int) or not isinstance(result.get('controls', {}).get('tooSmallCount'), int):
                        raise ValueError('页面未产生有效测量')
                    report[f"{route}|{label}"] = result
                except Exception as exc:  # noqa: BLE001 - 审计要把失败本身记进报告
                    report[f"{route}|{label}"] = {"error": str(exc)[:150]}
                await page.close()
            await ctx.close()
        await browser.close()

    path = os.path.join(OUT, "audit.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # 汇总：一行一个路由，直接可贴进评审
    total_low = total_small = total_inactive = 0
    print(f"{'route|width':28s} {'低对比文字':>10s} {'停用控件文字':>12s} {'<28px控件':>10s} {'顶栏高':>6s}")
    for key in sorted(report):
        v = report[key]
        if "error" in v:
            print(f"{key:28s} {'ERROR':>10s}")
            continue
        total_low += v["lowContrastCount"]
        total_inactive += v.get("inactiveLowContrastCount", 0)
        total_small += v["controls"]["tooSmallCount"]
        print(f"{key:28s} {v['lowContrastCount']:>10d} {v.get('inactiveLowContrastCount', 0):>12d} "
              f"{v['controls']['tooSmallCount']:>10d} {v.get('headerH', 0):>6d}")
    print(f"{'TOTAL':28s} {total_low:>10d} {total_inactive:>12d} {total_small:>10d}")
    print("saved", os.path.abspath(path))
    measured = sum('error' not in value for value in report.values())
    expected = len(ROUTES) * len(VIEWPORTS)
    print(json.dumps({'environment':'isolated fixtures', 'expected':expected, 'measured':measured,
                     'errors':expected-measured, 'blocked_requests':len(request_errors),
                     'inactive_low_contrast':total_inactive}, ensure_ascii=False))
    if measured != expected or request_errors or total_low:
        raise RuntimeError('视觉审计未通过；错误/未测量页面不得视为零问题')


if __name__ == "__main__":
    asyncio.run(main())
