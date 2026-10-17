#!/usr/bin/env python3
"""前端视觉层审计（浏览器实测，产出可复核的逐节点证据）。

为什么需要它
------------
`scripts/check-visual-consistency.cjs` 是**静态**护栏：按 CSS 源码声明算对比度，
不需要浏览器，适合进 CI。但静态分析看不到"这条规则实际落在哪个元素上、被谁覆盖"，
也看不到运行期才出现的节点（图片降载占位、异步渲染的徽标等）。本脚本补上这一层：
用真实 Chromium 打开每条路由，逐个元素取**计算样式**，按 WCAG 2.1 公式算对比度，
记录每个不达标节点（类名 / 字号 / 前景 / 背景 / 实测比值 / 文案）。

安全模型（结构性保证，不依赖使用者自觉）
--------------------------------------
1. **默认 fixture 隔离模式**：不连接任何真实后端、不需要 API key、不读环境里的 key。
   脚本自带静态 SPA 服务器（只服务 dist/ 构建产物，对任何路径只回 200/404，
   **永不发 3xx 重定向**——从构造上消灭"重定向链漏放"），所有 `/api/**` 请求
   由本脚本内的 fixture 注册表应答，api_base_url 指向该本地服务器。
2. **context 级路由默认拒绝**：在 browser context 上注册**唯一**路由（不是每页一次），
   - POST/PUT/PATCH/DELETE：除两张声明的 mock（buybox/page-open、packing/sync-from-products）
     外一律 abort。这两张 mock 返回 200 假响应，**请求永远不会到达网络**，
     因此脚本在任何模式下都不可能写入生产（packing sync 会真实改库，必须挡掉）。
   - GET/HEAD：只应答注册表内声明的 fixture；未声明路径一律 abort（默认拒绝，不放行）。
   - OPTIONS：按 CORS 需要返回 204 + 宽松头。
3. **网络级泄漏检测兜底**：监听浏览器发出的每一个请求，凡 `/api/` 请求未被路由器
   处理过（含已知的 playwright 限制：continue_() 后网络重定向不重新路由），
   即记 LEAK 并让该路由失败、整体退出非 0。
4. **live 模式是显式 opt-in**：需同时满足 `--live` 参数、`AUDIT_ALLOW_LIVE=1`、
   `SELECTOR_API_KEY` 非空。即使 live 模式，写操作仍按第 2 条拦截/ mock——
   脚本对任何后端都只读。live 模式只影响 GET 读取落到真实已授权会话。

验收口径（防止"假通过"）
------------------------
- 每条路由判定：measured / error / unauthenticated 三态。
- 校验项：document title 非空、路由声明的 fixture 标记（选择器+文本）出现、
  readyState=complete、未出现 401/未认证空表文案。任一不满足 → 该路由 error。
- 汇总必须打印 expected/measured/error/unauthenticated 计数；
  存在 error/unauthenticated、或 measured=0（一次都没测到）时**退出非 0**，
  不允许显示"全部通过"。
- 报告 JSON 首行注明 data_environment（fixture 或 live），统计数字不得脱离环境引用。

覆盖边界（不得夸大全状态通过）
----------------------------
默认态 + 有限 Tab 焦点态（每路由最多 12 次）+ 有限 hover 态（每路由前 10 个按钮）。
未覆盖：下拉菜单/弹窗/资料明细展开态、键盘导航全路径、极端窄屏布局。
用法
----
    # 0. 先构建（审计测的是构建产物，不是 dev server）
    npm run build

    # 1. fixture 隔离模式（默认，无需 key，不会碰生产；自带静态服务器）
    python scripts/ui_contrast_audit.py

    # 2. 已授权真实会话只读复测（写操作仍被结构性拦截）
    SELECTOR_API_BASE=https://<backend> SELECTOR_API_KEY=<key> AUDIT_ALLOW_LIVE=1 \
        python scripts/ui_contrast_audit.py --live

    # 3. 自测：不连任何外部服务，验证路由防漏放、检测器兜底与失败退出
    python scripts/ui_contrast_audit.py --selftest

依赖：playwright（Python 包）+ 已下载的 Chromium。
输出：ui_audit_out/audit.json（逐节点数据）、ui_audit_out/shots/*.png（截图证据）。
"""
import argparse
import asyncio
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# 配置在调用时读取（selftest 需要临时覆盖，不能在 import 时冻结）
def _cfg():
    def _split(name, default):
        return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]

    viewports = []
    for spec in _split("AUDIT_VIEWPORTS", "w1366:1366:900,w768:768:1000,w390:390:844"):
        label, w, h = spec.split(":")
        viewports.append((label, int(w), int(h)))
    return {
        "base": os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:5173"),
        "out": os.environ.get("AUDIT_OUT", os.path.join(REPO, "ui_audit_out")),
        "routes": _split("AUDIT_ROUTES", "/,/products,/inventory,/auto-pricing,/replenishment,/packing,/procurement,/stores,/settings,/create"),
        "viewports": viewports,
    }

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SAFE_METHODS = {"GET", "HEAD"}

# ---------------------------------------------------------------------------
# fixture 注册表：声明的只读应答。未声明路径一律 abort（默认拒绝）。
# ---------------------------------------------------------------------------
FIXTURE_PRODUCT = {
    "id": "audit-fixture-0001",
    "sku": "9900000000001",
    "product_name": "Fixture Long English Product Title For Overflow AUDIT-9900000000001",
    "title": "Fixture Long English Product Title For Overflow AUDIT-9900000000001",
    "chinese_product_name": "审计夹具长英文品名",
    "price": 199.99,
    "cost": 88.5,
    "stock": 12,
    "image_url": "/fixture/present.png",
    "offer_id": "offer-fixture-1",
    "product_id": "product-fixture-1",
    "variants": [
        {"sku": "9900000000001", "color": "Gold", "size": "18cm"},
        {"sku": "9900000000002", "color": "Silver", "size": "18cm"},
    ],
    "buybox_price": 189.99,
    "holding_buybox": True,
    "monitor": {"enabled": True, "interval_minutes": 5},
}
FIXTURE_PRODUCT_2 = dict(FIXTURE_PRODUCT, id="audit-fixture-0002", sku="9900000000002",
                         product_name="Second Fixture Item AUDIT-9900000000002",
                         title="Second Fixture Item AUDIT-9900000000002", image_url="/fixture/missing.png")

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
)


def _products_payload():
    # getProducts 直接 .then(setProducts)：后端返回裸数组 Product[]
    return [FIXTURE_PRODUCT, FIXTURE_PRODUCT_2]


READ_FIXTURES = [
    # (方法, 路径正则, 应答函数)
    ("GET", r"^/api/products(\?.*)?$", lambda: _products_payload()),
    ("GET", r"^/api/products/[^/]+$", lambda: FIXTURE_PRODUCT),
    ("GET", r"^/api/products/stats/dashboard$",
     lambda: {"total_products": 2, "total_stock": 12, "buybox_count": 1, "low_stock": 0, "pending_tasks": 0}),
    ("GET", r"^/api/products/sku-audit$", lambda: {"items": [], "mismatches": 0}),
    ("GET", r"^/api/fee-categories$", lambda: []),
    ("GET", r"^/api/fee-mapping-rules$", lambda: []),
    ("GET", r"^/api/settings(\?.*)?$", lambda: {"default_margin": 0.2}),
    ("GET", r"^/api/stores$", lambda: [{"id": "default-store", "name": "Fixture Store", "active": True}]),
    ("GET", r"^/api/inventory/stores-overview$", lambda: []),
    ("GET", r"^/api/inventory/adjustments$", lambda: []),
    ("GET", r"^/api/inventory/shipments$", lambda: []),
    ("GET", r"^/api/inventory(\?.*)?$", lambda: []),
    ("GET", r"^/api/activity(\?.*)?$", lambda: []),
    ("GET", r"^/api/procurement(\?.*)?$", lambda: []),
    ("GET", r"^/api/procurement/by-no/[^/]+$", lambda: []),
    ("GET", r"^/api/operations/overview$", lambda: {"alerts": 0, "health": "ok"}),
    ("GET", r"^/api/operations/alerts$", lambda: []),
    ("GET", r"^/api/operations/[^/]+$", lambda: {}),
    ("GET", r"^/api/packing/products(\?.*)?$", lambda: []),
    ("GET", r"^/api/packing/images/[^/]+$", lambda: PNG_1X1),
    ("GET", r"^/api/packing/export(\?.*)?$", lambda: "sku,name\n9900000000001,Fixture\n"),
    ("GET", r"^/api/packing/inbound-template(\?.*)?$", lambda: "sku,qty\n"),
    ("GET", r"^/api/automatic-pricing(\?.*)?$", lambda: {"rules": []}),
    ("GET", r"^/api/buybox(\?.*)?$", lambda: []),
    ("GET", r"^/api/integrations/takealot-seller/status$",
     lambda: {"connected": True, "last_sync": "2026-10-17T10:00:00Z"}),
    ("GET", r"^/api/image-proxy(\?.*)?$", lambda: PNG_1X1),
    ("GET", r"^/api/collector/status$",
     lambda: {"online_collectors": 1, "pending": 0, "leases": 0, "updated_at": "2026-10-17T10:00:00Z"}),
]

# 声明的写操作 mock：200 假响应，请求不达网络。未声明的写操作一律 abort。
WRITE_MOCKS = [
    ("POST", r"^/api/buybox/page-open$",
     lambda: {"queued": 0, "skipped": 0, "price_changed": False}),
    ("POST", r"^/api/packing/sync-from-products$",
     lambda: {"ok": True, "updated": 0, "total_pending": 0, "failed": []}),
]

# 每路由验收标记：加载后必须出现的选择器/文本；缺任一即该路由 error。
ROUTE_EXPECT = {
    "/": [(".app-header", None)],
    "/products": [("table", "AUDIT-9900000000001")],
    "/inventory": [("table", None)],
    "/auto-pricing": [( ".app-main, main, .page", None)],
    "/replenishment": [(".app-main, main, .page", None)],
    "/packing": [(".app-main, main, .page", None)],
    "/procurement": [(".app-main, main, .page", None)],
    "/stores": [(".app-main, main, .page", None)],
    "/settings": [(".app-main, main, .page", None)],
    "/create": [("form, .app-main, main", None)],
}
UNAUTH_MARKERS = ("无效或缺失 API Key", "Invalid or missing API Key", "401")

def _init_script(live: bool) -> str:
    cfg = _cfg()
    if live:
        return """
try {
  localStorage.setItem('api_base_url', %r);
  localStorage.setItem('active_store_id', %r);
  localStorage.setItem('api_key', %r);
} catch (e) {}
""" % (os.environ.get("SELECTOR_API_BASE", ""), os.environ.get("SELECTOR_STORE_ID", "default-store"),
       os.environ.get("SELECTOR_API_KEY", ""))
    # fixture 模式：api_base_url 指向 dev server 本身，key 是明显非真实的占位串，
    # 结构上不可能把真实密钥发去任何地方。
    return """
try {
  localStorage.setItem('api_base_url', %r);
  localStorage.setItem('active_store_id', 'default-store');
  localStorage.setItem('api_key', 'fixture-not-a-real-key');
} catch (e) {}
""" % cfg["base"]

AUDIT_JS = r"""
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
  const scan = (stateTag) => {
    document.querySelectorAll('body *').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return;
      const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
      if (!own) return;
      const fs = px(cs.fontSize);
      if (!stateTag) fontSizes[fs] = (fontSizes[fs] || 0) + 1;
      const fg = rgb(cs.color); const bg = effBg(el);
      if (fg && bg) {
        const cr = ratio(fg, bg);
        const need = (fs >= 24 || (fs >= 18.66 && +cs.fontWeight >= 700)) ? 3 : 4.5;
        if (cr < need) lowContrast.push({ state: stateTag || 'default', cls: cls(el), fs, ratio: Math.round(cr*100)/100, need,
          text: own.slice(0,28), color: cs.color, bg: `rgb(${bg[0]},${bg[1]},${bg[2]})` });
      }
    });
  };

  scan(null);

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
  const fg = rgb(cs.color), bg = effBg(el);
  if (!fg || !bg) return null;
  const cr = ratio(fg, bg);
  return { tag: el.tagName.toLowerCase(), cls: (typeof el.className==='string'?el.className:'').slice(0,60),
           ratio: Math.round(cr*100)/100, outlineColor: cs.outlineColor, outlineStyle: cs.outlineStyle,
           outlineWidth: cs.outlineWidth, color: cs.color, bg: `rgb(${bg[0]},${bg[1]},${bg[2]})` };
}
"""


class Router:
    """context 级 API 路由器：默认拒绝，只放行声明的只读 fixture 与两张写 mock。"""

    def __init__(self, live: bool):
        self.live = live
        self.reads = [(m, re.compile(p), fn) for m, p, fn in READ_FIXTURES]
        self.writes = [(m, re.compile(p), fn) for m, p, fn in WRITE_MOCKS]
        self.log = []  # (method, path, action)

    async def handle(self, route, request):
        from urllib.parse import urlparse
        path = urlparse(request.url).path
        method = request.method.upper()
        if not path.startswith("/api/"):
            await route.continue_()
            return
        if method == "OPTIONS":
            await route.fulfill(status=204, headers={
                "access-control-allow-origin": "*",
                "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                "access-control-allow-headers": "*",
            })
            self.log.append((method, request.url, "cors"))
            return
        if method in WRITE_METHODS:
            for m, pat, fn in self.writes:
                if m == method and pat.match(path):
                    body = fn()
                    payload = body if isinstance(body, (str, bytes)) else json.dumps(body)
                    ctype = "text/plain" if isinstance(body, str) else "application/json"
                    await route.fulfill(status=200, content_type=ctype, body=payload)
                    self.log.append((method, request.url, "write-mock"))
                    return
            await route.abort()
            self.log.append((method, request.url, "write-abort"))
            return
        if method in SAFE_METHODS:
            if self.live:
                # live 模式：读取落到真实已授权会话；写操作在上面已被拦截。
                await route.continue_()
                self.log.append((method, request.url, "live-passthrough"))
                return
            for m, pat, fn in self.reads:
                if m == method and pat.match(path):
                    body = fn()
                    if isinstance(body, bytes):
                        await route.fulfill(status=200, content_type="image/png", body=body)
                    elif isinstance(body, str):
                        await route.fulfill(status=200, content_type="text/plain", body=body)
                    else:
                        await route.fulfill(status=200, content_type="application/json",
                                            body=json.dumps(body, ensure_ascii=False))
                    self.log.append((method, request.url, "fixture"))
                    return
            await route.abort()
            self.log.append((method, request.url, "undeclared-abort"))
            return
        await route.abort()
        self.log.append((method, request.url, "method-abort"))


# ---------------------------------------------------------------------------
# 静态 dist 服务器：审计专用 SPA 宿主。
# 关键性质：对任何路径只回 200/404，**永不发 3xx 重定向**。
# 这从构造上消灭了"continue_() 后的网络重定向绕过路由器"的缺口——
# 没有重定向，就没有链上漏放；泄漏检测器再兜底。
# ---------------------------------------------------------------------------
class _SpaStaticHandler(BaseHTTPRequestHandler):
    dist_dir = os.path.join(REPO, "dist")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _resolve(self):
        path = self.path.split("?", 1)[0]
        if path == "/fixture/present.png":
            return 200, PNG_1X1, "image/png"
        # SPA 回退：BrowserRouter 的直接导航（如 /products）回 index.html
        candidate = os.path.normpath(os.path.join(self.dist_dir, path.lstrip("/")))
        if candidate.startswith(os.path.normpath(self.dist_dir)) and os.path.isfile(candidate):
            with open(candidate, "rb") as f:
                data = f.read()
            ctype = {
                ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
                ".ico": "image/x-icon", ".woff2": "font/woff2",
            }.get(os.path.splitext(candidate)[1].lower(), "application/octet-stream")
            return 200, data, ctype
        index = os.path.join(self.dist_dir, "index.html")
        if os.path.isfile(index):
            with open(index, "rb") as f:
                return 200, f.read(), "text/html"
        return 404, b"dist/index.html not found; run npm run build first", "text/plain"

    def do_GET(self):  # noqa: N802 - http.server 接口
        status, body, ctype = self._resolve()
        self._send(status, body, ctype)

    do_HEAD = do_GET  # noqa: N802

    def do_POST(self):  # noqa: N802 - 静态服务器不应收到 POST，收到即说明绕过了路由器
        self._send(404, b"static spa server: unexpected POST", "text/plain")

    def log_message(self, *a):  # noqa: B006
        pass


def start_static_server():
    """启动静态 SPA 服务器，返回 (server, base_url)。"""
    if not os.path.isfile(os.path.join(_SpaStaticHandler.dist_dir, "index.html")):
        print(f"{_SpaStaticHandler.dist_dir}/index.html 不存在：请先 npm run build（审计测的是构建产物）。",
              file=sys.stderr)
        sys.exit(2)
    server = HTTPServer(("127.0.0.1", 0), _SpaStaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _detect_leaks(seen_requests, handled_urls):
    """浏览器发出过、但路由器没处理过的 /api 请求 = 泄漏。"""
    return [
        {"method": m, "url": u} for (m, u) in seen_requests
        if "/api/" in u and u not in handled_urls
    ]


async def audit_route(ctx, router: Router, route_path: str, label: str, w: int, h: int, shot_dir: str, base: str):
    """打开一条路由，跑默认态 + Tab 焦点态 + hover 态，返回该路由记录。"""
    page = await ctx.new_page()
    key = f"{route_path}|{label}"
    rec = {"viewport": [w, h], "status": "error", "reason": "", "writes": [], "leaks": []}

    # 网络级泄漏检测：浏览器发出的每一个 /api 请求都必须被路由器处理过。
    # playwright 对 continue_() 后产生的网络重定向不会重新路由（已知限制），
    # 检测器负责兜底：任何绕过路由器的 /api 请求都记 leak 并让该路由失败。
    # 注意按日志切片比对，避免用其他路由处理过的同名 URL 掩盖本页泄漏。
    seen_requests = []
    page.on("request", lambda req: seen_requests.append((req.method, req.url)))
    log_mark = len(router.log)

    try:
        await page.goto(base + route_path, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)
        await page.wait_for_load_state("networkidle", timeout=20000)

        title = await page.title()
        ready = await page.evaluate("() => document.readyState")
        body_text = (await page.evaluate("() => document.body ? document.body.innerText : ''"))[:4000]

        # 验收标记
        missing = []
        for selector, text in ROUTE_EXPECT.get(route_path, []):
            try:
                loc = page.locator(selector).first
                if await loc.count() == 0:
                    missing.append(f"selector:{selector}")
                elif text and text not in (await loc.inner_text()):
                    missing.append(f"text:{text}")
            except Exception as exc:  # noqa: BLE001
                missing.append(f"selector-error:{selector}:{exc}")
        unauth_hit = next((m for m in UNAUTH_MARKERS if m in body_text), None)
        if unauth_hit:
            rec["status"] = "unauthenticated"
            rec["reason"] = f"unauthenticated marker: {unauth_hit}"
        elif missing:
            rec["status"] = "error"
            rec["reason"] = "fixture validation failed: " + "; ".join(missing)
        elif ready != "complete" or not title:
            rec["status"] = "error"
            rec["reason"] = f"load incomplete (readyState={ready}, title={title!r})"
        else:
            rec["status"] = "measured"

        data = await page.evaluate(AUDIT_JS)

        # Tab 焦点态（有限次数）：记录焦点元素对比度与焦点圈颜色
        focus_records = []
        for _ in range(12):
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(120)
            fr = await page.evaluate(FOCUS_JS)
            if fr:
                focus_records.append(fr)
        data["focus"] = focus_records

        # hover 态（有限个数）：对前 10 个可见按钮做 hover 后重扫
        hovered = 0
        buttons = page.locator("button:visible")
        n = min(10, await buttons.count())
        for i in range(n):
            try:
                await buttons.nth(i).hover(timeout=1500)
                hovered += 1
                await page.wait_for_timeout(150)
                hd = await page.evaluate(AUDIT_JS)
                for node in hd["lowContrast"]:
                    node["state"] = "hover"
                    data["lowContrast"].append(node)
            except Exception:  # noqa: BLE001 - hover 失败不阻塞审计
                continue
        data["hoverSampled"] = hovered

        # 截图证据
        try:
            os.makedirs(shot_dir, exist_ok=True)
            await page.screenshot(path=os.path.join(shot_dir, f"{route_path.strip('/').replace('/', '_') or 'home'}_{label}.png"))
        except Exception:  # noqa: BLE001
            pass

        rec.update({k: v for k, v in data.items() if k != "error"})
    except Exception as exc:  # noqa: BLE001 - 审计要把失败本身记进报告
        rec["reason"] = f"exception: {str(exc)[:200]}"
    finally:
        handled = {url for (_m, url, _a) in router.log[log_mark:]}
        leaks = _detect_leaks(seen_requests, handled)
        if leaks:
            rec["leaks"] = leaks
            rec["status"] = "error"
            rec["reason"] = f"LEAK: {len(leaks)} 个 /api 请求绕过路由器，首个 {leaks[0]['method']} {leaks[0]['url'][:80]}"
        rec["writes"] = [
            {"method": m, "url": u, "action": a}
            for (m, u, a) in router.log[log_mark:] if a in ("write-mock", "write-abort")
        ]
        await page.close()
    return key, rec


async def run_audit(live: bool):
    cfg = _cfg()
    out, base = cfg["out"], cfg["base"]
    os.makedirs(out, exist_ok=True)
    shot_dir = os.path.join(out, "shots")
    router = Router(live=live)
    report = {"data_environment": "live-authorized-session" if live else "fixture-isolated",
              "routes": {}}
    server = None
    if not os.environ.get("AUDIT_BASE_URL"):
        # 未指定外部 base 时自带静态 SPA 服务器（不发 3xx），完全本地自洽
        server, base = start_static_server()
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        for label, w, h in cfg["viewports"]:
            ctx = await browser.new_context(viewport={"width": w, "height": h})
            await ctx.add_init_script(_init_script(live))
            await ctx.route("**/*", router.handle)
            for route_path in cfg["routes"]:
                key, rec = await audit_route(ctx, router, route_path, label, w, h, shot_dir, base)
                report["routes"][key] = rec
            await ctx.close()
        await browser.close()
    if server:
        server.shutdown()

    # 汇总
    expected = len(report["routes"])
    measured = sum(1 for r in report["routes"].values() if r["status"] == "measured")
    errors = [k for k, r in report["routes"].items() if r["status"] == "error"]
    unauth = [k for k, r in report["routes"].items() if r["status"] == "unauthenticated"]
    total_low = sum(r.get("lowContrastCount", 0) for r in report["routes"].values())
    total_small = sum(r.get("controls", {}).get("tooSmallCount", 0) for r in report["routes"].values())
    writes_mocked = sum(1 for r in report["routes"].values() for wrec in r.get("writes", []) if wrec["action"] == "write-mock")
    writes_aborted = sum(1 for r in report["routes"].values() for wrec in r.get("writes", []) if wrec["action"] == "write-abort")

    report["summary"] = {
        "expected": expected, "measured": measured,
        "error": len(errors), "unauthenticated": len(unauth),
        "lowContrastNodes": total_low, "tooSmallControls": total_small,
        "writesMocked": writes_mocked, "writesAborted": writes_aborted,
    }
    path = os.path.join(out, "audit.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(f"data_environment: {report['data_environment']}")
    print(f"{'route|width':28s} {'状态':>6s} {'低对比文字':>10s} {'<28px控件':>10s} {'顶栏高':>6s}")
    for key in sorted(report["routes"]):
        v = report["routes"][key]
        st = {"measured": "OK", "error": "ERROR", "unauthenticated": "401"}[v["status"]]
        if v["status"] != "measured":
            print(f"{key:28s} {st:>6s}  {v['reason'][:60]}")
            continue
        print(f"{key:28s} {st:>6s} {v['lowContrastCount']:>10d} {v['controls']['tooSmallCount']:>10d} {v.get('headerH', 0):>6d}")
    print(f"{'TOTAL':28s} {'':>6s} {total_low:>10d} {total_small:>10d}")
    print(f"expected={expected} measured={measured} error={len(errors)} unauthenticated={len(unauth)} "
          f"writes_mocked={writes_mocked} writes_aborted={writes_aborted}")
    print("saved", os.path.abspath(path))
    if errors or unauth or measured == 0:
        print("RESULT: FAIL — 存在未验收路由或零测量，不得视为视觉通过", file=sys.stderr)
        return 1
    print("RESULT: PASS (fixture 隔离默认态+焦点+hover；未覆盖菜单/弹窗/展开态)")
    return 0


# ---------------------------------------------------------------------------
# 自测：不依赖 dev server。验证路由默认拒绝、查询参数/重定向不漏放、失败退出非 0。
# ---------------------------------------------------------------------------
class _BlankHandler(BaseHTTPRequestHandler):
    """selftest 空白页服务器：另发两个真实重定向，用于验证重定向链不漏放。"""

    def _redirect(self, location, permanent=False):
        self.send_response(307 if permanent else 302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - http.server 接口
        if self.path.startswith("/redirect-get"):
            self._redirect("/api/products")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><head><title>blank</title></head><body><div id=app></div></body></html>")

    def do_POST(self):  # noqa: N802 - http.server 接口
        if self.path.startswith("/redirect-post-307"):
            self._redirect("/api/packing/sync-from-products", permanent=True)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"leaked":true}')

    def log_message(self, *a):  # noqa: B006
        pass


async def _selftest():
    failures = []

    def check(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            failures.append(name)

    server = HTTPServer(("127.0.0.1", 0), _BlankHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    blank_base = f"http://127.0.0.1:{port}"

    router = Router(live=False)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        ctx = await browser.new_context()
        await ctx.route("**/*", router.handle)
        page = await ctx.new_page()
        await page.goto(blank_base)

        async def fetch(url, method="GET", target=None):
            return await (target or page).evaluate(
                "async ([u, m]) => { try { const r = await fetch(u, {method: m, redirect: 'follow'});"
                " return {ok: r.ok, status: r.status, body: (await r.text()).slice(0, 400)}; }"
                " catch (e) { return {error: String(e).slice(0, 80)}; } }",
                [url, method])

        print("selftest: context 级路由防漏放")
        r = await fetch("http://audit.invalid/api/packing/sync-from-products", "POST")
        check("声明的写 mock（packing sync）被拦截为 mock，不达网络",
              r.get("ok") and "updated" in r.get("body", ""), str(r))
        r = await fetch("http://audit.invalid/api/packing/sync-from-products?force=1&x=2", "POST")
        check("查询参数变体仍被拦截", r.get("ok") and "updated" in r.get("body", ""), str(r))
        r = await fetch("http://audit.invalid/api/buybox/page-open", "POST")
        check("声明的写 mock（page-open）被拦截为 mock", r.get("ok") and "queued" in r.get("body", ""), str(r))
        r = await fetch("http://audit.invalid/api/inventory/adjustments", "POST")
        check("未声明写操作被 abort", "error" in r, str(r))
        r = await fetch("http://audit.invalid/api/inventory/reconciliation/apply", "POST")
        check("库存更正写操作被 abort", "error" in r, str(r))
        r = await fetch("http://audit.invalid/api/automatic-pricing/pause-all", "POST")
        check("调价写操作被 abort", "error" in r, str(r))
        r = await fetch("http://audit.invalid/api/undeclared-read")
        check("未声明 GET 被 abort", "error" in r, str(r))
        r = await fetch("http://audit.invalid/api/products")
        check("声明的只读 fixture 正常应答", r.get("ok") and "AUDIT-9900000000001" in r.get("body", ""), str(r)[:160])

        # 已知 playwright 限制：continue_() 后产生的网络重定向不会被重新路由。
        # 真实审计改用"永不发 3xx"的静态 SPA 服务器，该路径不存在；
        # 这里验证兜底：一旦发生绕过，泄漏检测器必须捕获并让路由失败。
        page2 = await ctx.new_page()
        await page2.goto(blank_base)
        seen2 = []
        page2.on("request", lambda req: seen2.append((req.method, req.url)))
        mark2 = len(router.log)
        r = await fetch(blank_base + "/redirect-get", target=page2)
        leaks2 = _detect_leaks(seen2, {u for (_m, u, _a) in router.log[mark2:]})
        check("已知限制场景（网络 302 重定向绕过路由器）：泄漏检测器必须捕获",
              len(leaks2) >= 1, f"leaks={leaks2}")
        await page2.close()

        page3 = await ctx.new_page()
        await page3.goto(blank_base)
        seen3 = []
        page3.on("request", lambda req: seen3.append((req.method, req.url)))
        mark3 = len(router.log)
        r = await fetch(blank_base + "/redirect-post-307", "POST", target=page3)
        leaks3 = _detect_leaks(seen3, {u for (_m, u, _a) in router.log[mark3:]})
        check("已知限制场景（307 重定向到写端点绕过路由器）：泄漏检测器必须捕获",
              len(leaks3) >= 1, f"leaks={leaks3}")
        await page3.close()

        actions = {}
        for m, pth, a in router.log:
            actions[a] = actions.get(a, 0) + 1
        check("路由日志无 live-passthrough / 无放行写操作",
              "live-passthrough" not in actions and "write-abort" in actions, str(actions))
        await browser.close()

    # 结构性保证：真实审计使用的静态 SPA 服务器对任何路径都不发 3xx
    # （用临时假 dist 验证，不依赖真实构建产物）
    import tempfile
    import shutil
    tmp_dist = tempfile.mkdtemp()
    with open(os.path.join(tmp_dist, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html><head><title>static</title></head><body></body></html>")
    _SpaStaticHandler.dist_dir = tmp_dist
    static_srv, static_base = start_static_server()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
            pg = await browser.new_page()
            await pg.goto(static_base)
            for probe in ("/", "/products", "/redirect-get", "/api/products", "/missing.js", "/fixture/present.png"):
                rr = await pg.evaluate(
                    "async (u) => { try { const r = await fetch(u); return {status: r.status}; }"
                    " catch (e) { return {error: String(e).slice(0,60)}; } }", static_base + probe)
                check(f"静态 SPA 服务器 {probe} 不发 3xx", rr.get("status") in (200, 404), str(rr))
            await browser.close()
    finally:
        static_srv.shutdown()
        shutil.rmtree(tmp_dist, ignore_errors=True)
        _SpaStaticHandler.dist_dir = os.path.join(REPO, "dist")

    print("selftest: 验收失败必须非零退出")
    selftest_out = os.path.join(_cfg()["out"], "selftest")
    os.environ["AUDIT_BASE_URL"] = blank_base
    os.environ["AUDIT_OUT"] = selftest_out
    os.environ["AUDIT_ROUTES"] = "/products"
    os.environ["AUDIT_VIEWPORTS"] = "w1366:1366:900"
    code = await run_audit(live=False)
    check("空白页（fixture 校验失败）→ 退出码非 0", code != 0, f"exit={code}")
    report_path = os.path.join(selftest_out, "audit.json")
    with open(report_path, encoding="utf-8") as f:
        rep = json.load(f)
    s = rep["summary"]
    check("报告注明数据环境且 measured=0 不被当作通过",
          rep["data_environment"] == "fixture-isolated" and s["measured"] == 0 and s["error"] == 1,
          f"env={rep['data_environment']} summary={s}")

    server.shutdown()
    server.server_close()
    print(f"selftest: {'ALL PASS' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description="前端视觉层浏览器实测审计（默认 fixture 隔离）")
    ap.add_argument("--live", action="store_true",
                    help="已授权真实会话只读复测（写操作仍被结构性拦截；需 AUDIT_ALLOW_LIVE=1 与 SELECTOR_API_KEY）")
    ap.add_argument("--selftest", action="store_true", help="运行防漏放与失败退出自测")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(asyncio.run(_selftest()))

    live = args.live
    if live:
        if os.environ.get("AUDIT_ALLOW_LIVE") != "1" or not os.environ.get("SELECTOR_API_KEY"):
            print("live 模式需 AUDIT_ALLOW_LIVE=1 且 SELECTOR_API_KEY 非空；默认 fixture 模式不需要 key。",
                  file=sys.stderr)
            sys.exit(2)
        print("!! live 模式：GET 读取真实已授权会话；所有写操作仍被 context 级路由拦截/mock，脚本只读。")
    else:
        print("fixture 隔离模式：不连接生产、不需要 key、所有 /api 由本地 fixture 应答。")
    sys.exit(asyncio.run(run_audit(live=live)))


if __name__ == "__main__":
    main()
