#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长英文标题布局 + 焦点圈 + 截图 补充审计（Codex 任务 6232009314 / 6233701206）。

背景
----
用户截图反馈：Buybox（/auto-pricing）页 SKU9902348624058 / 9902348621330 的
英文标题跨列重叠。#125 已修复（单元格限宽 + 英文两行 clamp + 长串换行）。
本脚本在**隔离 fixture** 环境复测该修复，使用两条真实标题（脱敏仅取长度与形态）：

  9902348621330: "4K Thump action camera - professional package for motor,
                  biking, skydiving | Shop Today. Get It Tomorrow! | takealot.com"
  9902348624058: "6PCS Vacuum Cleaner Bags compatible with RV 20/30 Max Plus"

安全模型（与 main #128 版 scripts/ui_contrast_audit.py 完全一致）
---------------------------------------------------------------
- 导入 main 版审计模块，**只把其 DATA 换成本脚本的长标题变体**；
  intercept 的放行/拒绝逻辑原样沿用：非 GET/HEAD、未声明的 /api 路径一律 abort，
  仅两张声明的写 mock（page-open / packing sync）回 200，不达网络；
- 不读取 Key、不连接生产；BASE 只允许本机 HTTP（local_origin 校验）。

检查项（每路由 × 1280/1366/1440）
---------------------------------
1. 结构校验：标题出现、fixture 行（TEST-001/002）出现；
2. 文字互相重叠：任意两个非嵌套的文字元素矩形相交（面积 >9px² 且两轴 >1px）即失败
   —— 这正是用户截图「英文标题跨列重叠」的形态；
3. 表格单元格 containment：td/th 内所有后代元素不得越出单元格左右边界 1px；
4. 页面级横向溢出：scrollWidth <= clientWidth + 2；
5. 长标题元素形态：英文名元素的折行/clamp 状态与单元格剩余宽度；
6. 焦点圈：Tab 至多 60 次，逐个取 document.activeElement 的 outline/box-shadow
   颜色与所在背景，按 WCAG 2.1 公式算对比度，<3:1（WCAG 2.2 非文字）即失败；
7. 截图：每路由每宽度默认态一张 + 焦点态一张，存 ui_audit_out/longtitle/。

任一失败 → 退出码 1；未成功测量的页面不得视为零问题。
"""
import asyncio
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "ui_audit_out" / "longtitle"

# ---- 导入 main（#128）版审计模块：复用其安全拦截，不改其一行代码 ----
def load_main_audit():
    spec = importlib.util.spec_from_file_location("main_audit", HERE / "ui_contrast_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MAIN = load_main_audit()

# ---- 长标题 fixture 变体（深拷贝 main 的 DATA，仅改商品名/中文名/第二条商品）----
LONG_EN_CAMERA = ("4K Thump action camera - professional package for motor, biking, "
                  "skydiving | Shop Today. Get It Tomorrow! | takealot.com")
LONG_EN_BAGS = "6PCS Vacuum Cleaner Bags compatible with RV 20/30 Max Plus"

DATA = copy.deepcopy(MAIN.DATA)
p1 = DATA["/api/products"][0]
p1["product_name"] = LONG_EN_CAMERA
p1["chinese_product_name"] = "4K运动相机"
p2 = copy.deepcopy(p1)
p2["id"] = "fixture-product-2"
p2["product_no"] = 2
p2["sku"] = "TEST-002"
p2["product_name"] = LONG_EN_BAGS
p2["chinese_product_name"] = "吸尘袋套装"
DATA["/api/products"] = [p1, p2]
DATA["/api/products/" + p1["id"]] = p1
DATA["/api/products/" + p2["id"]] = p2

rows = DATA["/api/automatic-pricing"]["rows"]
rows[0]["name"] = LONG_EN_CAMERA
rows[0]["chinese_name"] = p1["chinese_product_name"]
rows[0]["product_id"] = p1["id"]
rows[0]["url"] = "https://catalog.example.invalid/product/this-is-a-deliberately-long-product-link-for-layout-validation"
r2 = copy.deepcopy(rows[0])
r2["sku"] = "TEST-002"
r2["name"] = LONG_EN_BAGS
r2["chinese_name"] = p2["chinese_product_name"]
r2["product_id"] = p2["id"]
rows.append(r2)

DATA["/api/inventory"].append(dict(DATA["/api/inventory"][0], product_id=p2["id"],
                                   name=p2["chinese_product_name"], name_en=LONG_EN_BAGS, sku="TEST-002"))
DATA["/api/packing/products"].append(dict(DATA["/api/packing/products"][0], sku="TEST-002",
                                          name=p2["chinese_product_name"], name_en=LONG_EN_BAGS))
DATA["/api/products/stats/dashboard"] = dict(total=2, qualified=0, not_recommended=0,
                                             pending=0, data_incomplete=2, avg_profit_margin=0)

# 路由 → 期望标题子串（详情页 h2 是英文标题本身）
ROUTES = [
    ("/products", "产品列表", ["TEST-001", "TEST-002"]),
    ("/auto-pricing", "Buybox与自动调价", ["TEST-001", "TEST-002"]),
    ("/products/fixture-product", "4K Thump action camera", []),
    ("/products/fixture-product-2", "6PCS Vacuum Cleaner Bags", []),
]
VIEWPORTS = [("w1280", 1280, 800), ("w1366", 1366, 900), ("w1440", 1440, 900)]
TAB_LIMIT = 60

LAYOUT_JS = r"""
() => {
  const px = v => parseFloat(v) || 0;
  const own = el => Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width >= 1 && r.height >= 1 && cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity !== 0; };
  // 闭合 <details> 的内容不绘制；其布局矩形仍在，必须排除，否则是误报
  const inClosedDetails = el => { const d = el.closest('details'); return !!d && !d.open; };
  // 实际绘制矩形：与祖先裁剪区（overflow!=visible / content-visibility:hidden）求交。
  // overflow:hidden 的子孙布局矩形可以远超可见区（如 maxWidth+ellipsis 的内层 span），
  // 但用户看得见的只有裁剪后的部分——跨列重叠必须按绘制矩形判定，否则全是假阳性。
  const painted = el => {
    let r = el.getBoundingClientRect();
    let n = el.parentElement;
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      if (cs.contentVisibility === 'hidden') return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 };
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const c = n.getBoundingClientRect();
        r = { left: Math.max(r.left, c.left), right: Math.min(r.right, c.right),
              top: Math.max(r.top, c.top), bottom: Math.min(r.bottom, c.bottom),
              width: 0, height: 0 };
        r.width = Math.max(0, r.right - r.left); r.height = Math.max(0, r.bottom - r.top);
        if (r.width < 0.5 || r.height < 0.5) return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 };
      }
      n = n.parentElement;
    }
    return r;
  };

  // 1) 文字互相重叠（按**绘制矩形**判定：非嵌套、两轴相交 >1px、面积 >9px²）
  const texts = [...document.querySelectorAll('body *')].filter(el => vis(el) && !inClosedDetails(el) && own(el).length > 0);
  const overlaps = [];
  for (let i = 0; i < texts.length; i++) {
    const a = texts[i], ra = painted(a);
    if (ra.width < 0.5 || ra.height < 0.5) continue;
    for (let j = i + 1; j < texts.length; j++) {
      const b = texts[j];
      if (a.contains(b) || b.contains(a)) continue;
      const rb = painted(b);
      if (rb.width < 0.5 || rb.height < 0.5) continue;
      const ix = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const iy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (ix > 1 && iy > 1 && ix * iy > 9) {
        overlaps.push({ a: (a.className||a.tagName).toString().slice(0,50), aText: own(a).slice(0,30),
                        b: (b.className||b.tagName).toString().slice(0,50), bText: own(b).slice(0,30),
                        ix: Math.round(ix), iy: Math.round(iy) });
        if (overlaps.length >= 30) break;
      }
    }
    if (overlaps.length >= 30) break;
  }

  // 2) 表格单元格 containment（按**绘制矩形**判左右边界 ±1px；闭合 details 内容跳过）
  const cellBreaks = [];
  document.querySelectorAll('table td, table th').forEach(td => {
    const r = td.getBoundingClientRect();
    if (r.width < 1) return;
    td.querySelectorAll('*').forEach(el => {
      if (!vis(el) || inClosedDetails(el)) return;
      const c = painted(el);
      if (c.width < 0.5 || c.height < 0.5) return;
      if (c.left < r.left - 1 || c.right > r.right + 1) {
        cellBreaks.push({ cell: (td.className||'').toString().slice(0,40),
                          el: (el.className||el.tagName).toString().slice(0,50),
                          over: Math.round(Math.max(r.left - c.left, c.right - r.right)),
                          text: own(el).slice(0,30) || (el.textContent||'').trim().slice(0,30) });
      }
    });
  });

  // 3) 页面级横向溢出
  const de = document.documentElement;
  const pageOverflow = de.scrollWidth - de.clientWidth;

  // 4) 长标题元素形态（按绘制矩形：clamp/ellipsis 是合法形态，但不得越过单元格）
  const longNames = [...document.querySelectorAll('.product-english-name, h2, td span[title]')].filter(el => vis(el) && !inClosedDetails(el)).map(el => {
    const td = el.closest('td');
    const fs = px(getComputedStyle(el).fontSize);
    const lh = px(getComputedStyle(el).lineHeight) || fs * 1.4;
    const r = painted(el);
    const full = el.getBoundingClientRect();
    return { cls: (el.className||el.tagName).toString().slice(0,40),
             text: (el.textContent||'').trim().slice(0,42),
             paintedW: Math.round(r.width), layoutW: Math.round(full.width),
             lines: full.height >= 1 ? Math.round(full.height / lh * 10) / 10 : 0,
             clamped: el.scrollHeight > el.clientHeight + 1,
             cellSlackRight: td ? Math.round(td.getBoundingClientRect().right - r.right) : null,
             cellSlackLeft: td ? Math.round(r.left - td.getBoundingClientRect().left) : null };
  });

  return { overlaps, cellBreaks, pageOverflow, longNames,
           textNodeCount: texts.length, vw: de.clientWidth };
}
"""

FOCUS_JS = r"""
() => {
  const px = v => parseFloat(v) || 0;
  const lum = ([r,g,b]) => { const f=c=>{c/=255;return c<=0.03928?c/12.92:Math.pow((c+0.055)/1.055,2.4)}; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b); };
  const rgb = s => { const m=(s||'').match(/[\d.]+/g); if(!m) return null; return [ +m[0],+m[1],+m[2], m[3]!==undefined? +m[3]:1 ]; };
  const effBg = el => { let n=el; while (n && n!==document.documentElement) { const c=rgb(getComputedStyle(n).backgroundColor); if (c && c[3]>0.95) return c; n=n.parentElement; } return [255,255,255,1]; };
  const ratio = (fg,bg) => { const l1=lum(fg), l2=lum(bg); const a=Math.max(l1,l2), b=Math.min(l1,l2); return (a+0.05)/(b+0.05); };
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cs = getComputedStyle(el);
  let ring = null, source = 'none';
  if (cs.outlineStyle && cs.outlineStyle !== 'none') { ring = rgb(cs.outlineColor); source = 'outline:' + cs.outlineStyle; }
  if (!ring && cs.boxShadow && cs.boxShadow !== 'none') { const m = cs.boxShadow.match(/rgba?\([^)]*\)/); if (m) { ring = rgb(m[0]); source = 'box-shadow'; } }
  const bgEl = el.parentElement || el;
  const bg = effBg(bgEl);
  const out = { tag: el.tagName.toLowerCase(), cls: (typeof el.className==='string'?el.className:'').slice(0,50),
                ringSource: source, ringColor: ring ? `rgb(${ring[0]},${ring[1]},${ring[2]})` : null,
                bg: `rgb(${bg[0]},${bg[1]},${bg[2]})`,
                text: (el.textContent||'').trim().slice(0,24) };
  out.ringRatio = ring ? Math.round(ratio(ring, bg) * 100) / 100 : null;
  const fg = rgb(cs.color);
  out.textRatio = fg ? Math.round(ratio(fg, effBg(el)) * 100) / 100 : null;
  return out;
}
"""


async def intercept(route, request_errors):
    """与 main #128 版语义一致：仅放行声明的只读 fixture 与两张写 mock。"""
    request = route.request
    url = urlsplit(request.url)
    if (url.scheme, url.netloc) != MAIN.local_origin(MAIN.BASE):
        request_errors.append("external request blocked: " + request.url)
        await route.abort()
        return
    if url.path.startswith("/api/"):
        allowed = request.method in ("GET", "HEAD") or request.method == "POST" and url.path in MAIN.MOCK_POSTS
        if not allowed or url.path not in DATA:
            request_errors.append(request.method + " " + url.path)
            await route.abort()
            return
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(DATA[url.path]))
        return
    if request.method not in ("GET", "HEAD"):
        request_errors.append("non-read request blocked: " + request.method + " " + url.path)
        await route.abort()
        return
    await route.continue_()


async def run():
    MAIN.local_origin(MAIN.BASE)  # 校验 BASE 只允许本机
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    request_errors = []
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        for label, w, h in VIEWPORTS:
            ctx = await browser.new_context(viewport={"width": w, "height": h})
            await ctx.route("**/*", lambda route: intercept(route, request_errors))
            await ctx.add_init_script(MAIN.INIT)
            for route_path, heading, markers in ROUTES:
                page = await ctx.new_page()
                key = f"{route_path}|{label}"
                try:
                    await page.goto(MAIN.BASE + route_path, wait_until="domcontentloaded", timeout=30000)
                    await page.get_by_role("heading", name=heading, exact=False).first.wait_for(timeout=8000)
                    await page.locator(".loading").wait_for(state="hidden")
                    await page.wait_for_load_state("networkidle", timeout=20000)
                    for marker in markers:
                        await page.get_by_text(marker, exact=False).first.wait_for(timeout=8000)
                    layout = await page.evaluate(LAYOUT_JS)
                    if not isinstance(layout, dict) or not isinstance(layout.get("overlaps"), list):
                        raise ValueError("页面未产生有效测量")

                    focus_records = []
                    for _ in range(TAB_LIMIT):
                        await page.keyboard.press("Tab")
                        fr = await page.evaluate(FOCUS_JS)
                        if fr:
                            focus_records.append(fr)

                    shot = OUT / f"{route_path.strip('/').replace('/', '_') or 'home'}_{label}.png"
                    await page.screenshot(path=str(shot))
                    if focus_records:
                        await page.keyboard.press("Tab")
                        fr = await page.evaluate(FOCUS_JS)
                        if fr and fr.get("ringSource") != "none":
                            tag = (fr.get("cls") or fr.get("tag") or "focus").split(" ")[0]
                            await page.screenshot(path=str(OUT / f"{route_path.strip('/').replace('/', '_') or 'home'}_{label}_focus.png"))

                    report[key] = {"layout": layout, "focus": focus_records}
                except Exception as exc:  # noqa: BLE001
                    report[key] = {"error": str(exc)[:200]}
                await page.close()
            await ctx.close()
        await browser.close()

    # ---- 汇总与判定 ----
    total_overlap = total_break = total_focus_low = 0
    for key, value in sorted(report.items()):
        if "error" in value:
            failures.append(f"{key}: ERROR {value['error']}")
            print(f"{key:44s} ERROR {value['error'][:80]}")
            continue
        lay = value["layout"]
        focus = value["focus"]
        focus_low = [f for f in focus if f.get("ringRatio") is not None and f["ringRatio"] < 3]
        total_overlap += len(lay["overlaps"])
        total_break += len(lay["cellBreaks"])
        total_focus_low += len(focus_low)
        print(f"{key:44s} 重叠={len(lay['overlaps']):2d} 越界单元格={len(lay['cellBreaks']):2d} "
              f"页面横溢={lay['pageOverflow']:3d}px 焦点<3:1={len(focus_low):2d} 焦点采样={len(focus):2d}")
        for o in lay["overlaps"][:5]:
            failures.append(f"{key}: 文字重叠 {o['a']}({o['aText']}) x {o['b']}({o['bText']}) {o['ix']}x{o['iy']}px")
        for c in lay["cellBreaks"][:5]:
            failures.append(f"{key}: 单元格越界 {c['cell']} 内 {c['el']} 超出 {c['over']}px ({c['text']})")
        if lay["pageOverflow"] > 2:
            failures.append(f"{key}: 页面横向溢出 {lay['pageOverflow']}px")
        for f in focus_low[:5]:
            failures.append(f"{key}: 焦点圈 {f['ringRatio']}:1 {f['ringColor']} on {f['bg']} ({f['cls']})")
        # 长标题形态（仅记录，不判定：clamp/折行都是合法形态）
        for ln in lay["longNames"]:
            if "camera" in ln["text"].lower() or "Vacuum" in ln["text"]:
                print(f"    长标题[{ln['cls']}] {ln['text'][:38]}… 行数={ln['lines']} clamp={ln['clamped']} "
                      f"单元格右余量={ln['cellSlackRight']}px")

    measured = sum(1 for v in report.values() if "error" not in v)
    expected = len(ROUTES) * len(VIEWPORTS)
    print(f"{'TOTAL':44s} 重叠={total_overlap} 越界={total_break} 焦点<3:1={total_focus_low}")
    print(json.dumps({"environment": "isolated fixtures (long-title variant)",
                      "base": "main c96edcb823 (#128) + PR130 visual layer",
                      "titles": [LONG_EN_CAMERA, LONG_EN_BAGS],
                      "expected": expected, "measured": measured, "errors": expected - measured,
                      "blocked_requests": len(request_errors)}, ensure_ascii=False))
    with open(OUT.parent / "longtitle_audit.json", "w", encoding="utf-8") as f:
        json.dump({"report": report, "failures": failures, "request_errors": request_errors[:50]},
                  f, ensure_ascii=False, indent=1)
    print("saved", (OUT.parent / "longtitle_audit.json").resolve())
    if failures or measured != expected or request_errors:
        print("FAILURES:")
        for f_ in failures[:40]:
            print(" -", f_)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
