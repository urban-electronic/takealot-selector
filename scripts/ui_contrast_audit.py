#!/usr/bin/env python3
"""前端视觉层审计（浏览器实测，产出可复核的逐节点证据）。

为什么需要它
------------
`scripts/check-visual-consistency.cjs` 是**静态**护栏：它按 CSS 源码里的声明算对比度，
不需要浏览器，适合进 CI。但静态分析看不到"这条规则实际落在哪个元素上、被谁覆盖"，
也看不到运行期才出现的节点（图片降载占位、异步渲染的徽标等）。本脚本补上这一层：
用真实 Chromium 打开每条路由，逐个元素取**计算样式**，按 WCAG 2.1 公式算对比度，
把每个不达标节点（类名 / 字号 / 前景 / 背景 / 实测比值 / 文案）都记录下来。

用法
----
    # 1. 先起 dev server（任选其一）
    npx vite --port 5173 --strictPort

    # 2. 跑审计（API key 只用于让页面渲染出真实数据，绝不外传）
    SELECTOR_API_KEY=<key> python scripts/ui_contrast_audit.py

依赖：playwright（Python 包）+ 已下载的 Chromium。
     pip install playwright && python -m playwright install chromium

输出
----
    ui_audit_out/audit.json   全量逐节点数据（每条路由一个键，如 "/products|w1366"）
     stdout                   每个路由的汇总行，便于直接贴进评审

判定口径
--------
    正文文字：< 4.5:1 记不达标；>=18.66px 且加粗、或 >=24px 的文字按 3:1（WCAG 2.1 AA）。
    控件：高度 < 28px 记为触达目标过小（WCAG 2.5.8 建议 24px，本项目取更严的 28px）。
    背景：从元素自身向上找第一个不透明背景色，避免把透底误判成白底。
"""
import asyncio
import json
import os

from playwright.async_api import async_playwright

BASE = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:5173")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "ui_audit_out")
API_KEY = os.environ.get("SELECTOR_API_KEY", "")

ROUTES = [
    "/", "/products", "/inventory", "/auto-pricing", "/replenishment", "/packing",
    "/procurement", "/stores", "/settings", "/create",
]
VIEWPORTS = [("w1366", 1366, 900), ("w768", 768, 1000), ("w390", 390, 844)]

# 只拦"打开页面即触发一次真实采集"的入口：审计要看的是界面，不是采集结果。
# 这是唯一的写操作面，必须挡掉，否则跑一次审计就等于真采一轮。
BLOCKED = ("/api/buybox/page-open",)

INIT = """
try {
  localStorage.setItem('api_base_url', %r);
  localStorage.setItem('active_store_id', 'default-store');
  localStorage.setItem('api_key', %r);
} catch (e) {}
""" % (os.environ.get("SELECTOR_API_BASE", "https://takealot-selector-production.up.railway.app"), API_KEY)

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

  const lowContrast = [];
  const fontSizes = {};
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return;
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
    if (!own) return;
    const fs = px(cs.fontSize);
    fontSizes[fs] = (fontSizes[fs] || 0) + 1;
    const fg = rgb(cs.color); const bg = effBg(el);
    if (fg && bg) {
      const cr = ratio(fg, bg);
      const need = (fs >= 24 || (fs >= 18.66 && +cs.fontWeight >= 700)) ? 3 : 4.5;
      if (cr < need) lowContrast.push({ cls: cls(el), fs, ratio: Math.round(cr*100)/100, need,
        text: own.slice(0,28), color: cs.color, bg: `rgb(${bg[0]},${bg[1]},${bg[2]})` });
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
    controls: { heights: controls.heights, tooSmall: controls.tooSmall, tooSmallCount: controls.tooSmall.length },
    ths,
  };
}
"""


async def _block(route, _request):
    await route.fulfill(status=200, content_type="application/json",
                        body='{"queued":0,"skipped":0,"price_changed":false}')


async def main():
    os.makedirs(OUT, exist_ok=True)
    report = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        for label, w, h in VIEWPORTS:
            ctx = await browser.new_context(viewport={"width": w, "height": h})
            await ctx.add_init_script(INIT)
            for route in ROUTES:
                page = await ctx.new_page()
                await page.route("**" + BLOCKED[0], _block)
                try:
                    await page.goto(BASE + route, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(4000)
                    await page.wait_for_load_state("networkidle", timeout=20000)
                    report[f"{route}|{label}"] = await page.evaluate(AUDIT)
                except Exception as exc:  # noqa: BLE001 - 审计要把失败本身记进报告
                    report[f"{route}|{label}"] = {"error": str(exc)[:150]}
                await page.close()
            await ctx.close()
        await browser.close()

    path = os.path.join(OUT, "audit.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # 汇总：一行一个路由，直接可贴进评审
    total_low = total_small = 0
    print(f"{'route|width':28s} {'低对比文字':>10s} {'<28px控件':>10s} {'顶栏高':>6s}")
    for key in sorted(report):
        v = report[key]
        if "error" in v:
            print(f"{key:28s} {'ERROR':>10s}")
            continue
        total_low += v["lowContrastCount"]
        total_small += v["controls"]["tooSmallCount"]
        print(f"{key:28s} {v['lowContrastCount']:>10d} {v['controls']['tooSmallCount']:>10d} {v.get('headerH', 0):>6d}")
    print(f"{'TOTAL':28s} {total_low:>10d} {total_small:>10d}")
    print("saved", os.path.abspath(path))


if __name__ == "__main__":
    asyncio.run(main())
