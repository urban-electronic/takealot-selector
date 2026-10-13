"""PricePilot collector parsing core; adapted from native source 2026-10-12."""

import asyncio, math, random, re, time

from datetime import datetime, timezone

def log(msg): pass

def parse_price(text: str) -> float | None:
    if not text:
        return None
    s = text.strip().replace("R", "").replace(",", "").replace("\u00a0", " ").strip()
    try:
        return float(s)
    except ValueError:
        return None

async def extract_seller_name(page) -> str | None:
    """抓 Buybox 主卖家名（页面 'Sold by X'）"""
    try:
        el = await page.query_selector(
            '[class*="main-seller"], [class*="seller-information"], [class*="seller-detail"]'
        )
        if el:
            t = (await el.inner_text()).strip()
            import re

            m = re.search(r"Sold by\s+(.+)", t, re.IGNORECASE)
            if m:
                return m.group(1).strip()[:80]
            if t and len(t) < 80:
                return t.strip()
    except Exception:
        pass
    return None

async def extract_other_sellers(page, my_price) -> list[dict]:
    """抓 Other Sellers 竞品报价列表；页面无其他卖家时返回空列表。
    仅当页面明确存在 'Other sellers' 区块时解析，避免误抓推荐位价格。
    """
    offers: list[dict] = []
    try:
        markers = await page.query_selector_all("text=/other (sellers|offers)/i")
        if not markers:
            return []
        # 从标题向上寻找包含多个价格元素的容器
        container = await markers[0].evaluate(
            """(el) => {
                let p = el.parentElement;
                for (let i = 0; i < 6 && p; i++) {
                    const prices = p.querySelectorAll('[data-ref="price"], .currency');
                    if (prices.length >= 2) return p;
                    p = p.parentElement;
                }
                return null;
            }"""
        )
        if not container:
            return []
        # 容器内逐个卖家卡片：含卖家链接或 "Sold by" 的条目
        cards = await page.evaluate(
            """(root) => {
                const out = [];
                const nodes = root.querySelectorAll('a[href*="/seller/"], [class*="seller"]');
                nodes.forEach((n) => {
                    const card = n.closest('li, [class*="item"], [class*="card"], div');
                    const priceEl = card ? card.querySelector('[data-ref="price"] .currency, .currency') : null;
                    const seller = (n.innerText || '').trim().replace(/^Sold by\\s*/i, '').slice(0, 80);
                    const price = priceEl ? priceEl.innerText.trim() : '';
                    if (seller && price) out.push({ seller, price });
                });
                return out;
            }""",
            container,
        )
        seen = set()
        for c in cards:
            price = parse_price(c.get("price"))
            if not price:
                continue
            seller = (c.get("seller") or "").strip()
            if not seller or seller.lower() in seen:
                continue
            seen.add(seller.lower())
            is_mine = my_price is not None and abs(price - my_price) < 0.01
            offers.append(
                {
                    "seller": seller,
                    "price": price,
                    "status": "in_stock",
                    "offerType": "other",
                    "isMine": is_mine,
                    "deliveryLabel": None,
                    "leadtimeDays": None,
                }
            )
    except Exception:
        pass
    return offers

def map_stock(text: str | None) -> str:
    """把现货状态文本映射为枚举。Takealot 常见文案：
    In Stock / Ships in N days / Pre-Order / Backorder / Out of Stock / Sold Out / Temporarily Unavailable
    """
    if not text:
        return "out_of_stock"
    t = text.lower()
    if "in stock" in t:
        return "in_stock"
    if "ships in" in t or "ships tomorrow" in t or "delivery" in t and "pre" not in t:
        return "in_stock"
    if "pre-order" in t or "preorder" in t or "pre order" in t:
        return "pre_order"
    if "backorder" in t or "back order" in t:
        return "backorder"
    return "out_of_stock"

def map_card_stock(text: str | None) -> str:
    """卡级现货状态识别（buybox-offer 单卡文案）。
    与整页 stock 状态不同，卡级文案需区分"现货快送"与"慢配/预售"：
      - Fastest Delivery / Get It Today / Get It Tomorrow / Delivered Today → in_stock
      - "Delivery 11 Sep - 16 Sep" 等未来日期范围 → backorder（慢配，不参与现货竞价）
      - Pre-Order / Backorder → pre_order / backorder
    背景：Best Price 卡常为慢配/预售（配送日期在未来），若被误判 in_stock 会混入现货对标，
    导致引擎跟预售卖家竞价（如 309 预售卡拉低对标价触发无理由压价）。
    """
    if not text:
        return "out_of_stock"
    t = text.lower()
    if "pre-order" in t or "preorder" in t or "pre order" in t:
        return "pre_order"
    if "backorder" in t or "back order" in t:
        return "backorder"
    if "get it today" in t or "get it tomorrow" in t or "delivered today" in t or "delivered tomorrow" in t:
        return "in_stock"
    # "fastest delivery" / "free delivery" / "delivery in 2-4 days" 现货快送
    if "fastest delivery" in t:
        return "in_stock"
    # 日期范围配送（如 "Delivery 11 Sep - 16 Sep"）：未来配送 = 慢配/预售，不参与现货竞价
    import re as _re

    if _re.search(r"delivery\s+\d{1,2}\s+[a-z]{3}", t) or _re.search(r"deliver(ed|y)?\s+\d{1,2}\s+[a-z]{3}", t):
        return "backorder"
    if "in stock" in t or "ships in" in t or "ships tomorrow" in t:
        return "in_stock"
    # 兜底：有具体配送承诺但无法识别 → 视为现货（Fastest 卡通常无日期范围文案）
    if "delivery" in t or "deliver" in t:
        return "in_stock"
    return "out_of_stock"

SELECTORS = {
    # buybox 卡**只使用主选择器，不做自动降级**：
    # 缺货/无 offer 的商品本就没有 buybox 卡，此时主选择器正确返回空；
    # 而宽松候选（如 [class*="buybox"]）会匹配到页面其他元素，读出非 buybox 的价格，
    # 据此调价将造成真实亏损。故此处不配候选——改版时宁可"采不到"也不"采错"，
    # 由 selector_health() 检测大面积失效并告警，交由人工维护选择器。
    "buybox_card": [
        '[data-ref="buybox-offer"]',
    ],
    "price": [
        '[data-ref="price"] .currency',
        '[class*="price"] .currency',
        '[data-ref="price"]',
        '.currency',
    ],
    "stock": [
        '[data-ref="stock-availability-status"]',
        '[data-ref="stock-status"]',
        '[class*="stock-seller-section"]',
        '[class*="stock-availability"]',
        '[class*="availability"]',
    ],
    "variant": [
        '[class*="variant"]',
        '[data-ref*="variant"]',
        '[class*="swatch"]',
    ],
}

_selector_calls: dict[str, dict] = {k: {"ok": 0, "fail": 0, "max_idx": 0} for k in SELECTORS}

def reset_selector_stats():
    """每轮开始重置选择器健康度统计。"""
    for k in _selector_calls:
        _selector_calls[k] = {"ok": 0, "fail": 0, "max_idx": 0}

def _record_selector(kind: str, ok: bool, idx: int = 0):
    """记录一次选择器命中结果：ok=True 记成功并更新最坏候选索引，ok=False 记失败。"""
    d = _selector_calls.setdefault(kind, {"ok": 0, "fail": 0, "max_idx": 0})
    if ok:
        d["ok"] += 1
        d["max_idx"] = max(d.get("max_idx", 0), idx)
    else:
        d["fail"] += 1

def selector_health():
    """返回本轮 (degraded, broken)：降级（靠候选兜底）与失效（疑似改版）的选择器列表。
    仅当样本 >= 3 且失败率 > 50%（或无一成功）才判失效，避免单个缺货商品误报。"""
    degraded, broken = [], []
    for k, d in _selector_calls.items():
        total = d["ok"] + d["fail"]
        if total < 3:
            continue
        fail_rate = d["fail"] / total
        if d["ok"] == 0 or fail_rate > 0.5:
            broken.append(k)
        elif d.get("max_idx", 0) > 0:
            degraded.append(k)
    return degraded, broken

async def query_all_fallback(page, kind: str):
    """按优先级尝试候选选择器，返回 (elements, selector)。
    主选择器失效（改版）时自动降级到候选；全部失效返回 ([], None)。"""
    for i, sel in enumerate(SELECTORS[kind]):
        try:
            els = await page.query_selector_all(sel)
            if els:
                if i > 0:
                    log(f"  [选择器降级] {kind}: 主选择器失效，已降级到候选#{i} {sel}")
                _record_selector(kind, True, i)
                return els, sel
        except Exception:
            continue
    _record_selector(kind, False)
    return [], None

async def query_one_fallback(page, kind: str):
    """按优先级尝试候选选择器，返回首个命中的 element（未命中返回 None）。"""
    els, _ = await query_all_fallback(page, kind)
    return els[0] if els else None

def _norm_label(s: str | None) -> str:
    """规范化变体标签：折叠空白（含不换行空格）、去首尾装饰符号、转小写。
    用于变体后缀与页面按钮文本的宽松比对（页面常用 &nbsp; / 勾选符号）。"""
    if not s:
        return ""
    t = re.sub(r"[\s\u00a0]+", " ", str(s)).strip()
    t = t.strip("✓✔•·-—:：").strip()
    return t.lower()

_VARIANT_STATE_JS = r"""
el => {
  const attrs = ['aria-selected', 'aria-pressed', 'data-selected', 'data-active'];
  let selected = false;
  for (const a of attrs) {
    const v = el.getAttribute(a);
    if (v === 'true' || v === '1' || v === 'selected') { selected = true; break; }
  }
  if (!selected) {
    const c = el.getAttribute('class') || (el.className && el.className.toString ? el.className.toString() : '') || '';
    if (/(^|[\s_-])(selected|active|current)([\s_-]|$)/i.test(c)) selected = true;
  }
  let label = (el.innerText || el.textContent || '').trim();
  if (!label) label = (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
  return { selected: selected, label: label };
}
"""

async def price_of(page, card) -> float | None:
    """从卡片内提取价格，多候选选择器兜底。"""
    for i, sel in enumerate(SELECTORS["price"]):
        try:
            pr = await card.query_selector(sel)
            if pr is not None:
                p = parse_price(await pr.inner_text())
                if p is not None:
                    _record_selector("price", True, i)
                    return p
        except Exception:
            continue
    return None

async def scrape_product(page, item: dict) -> dict:
    """抓单个商品详情页，返回上报 item。任何异常返回兜底 item（buyboxPrice=None）。"""
    url = item.get("url") or ""
    product_id = item.get("productId")
    sku = item.get("sku")
    my_price = item.get("myPrice")

    result = {
        "shopId": item.get("shopId"),
        "productId": product_id,
        "sku": sku,
        "url": url,
        "buyboxPrice": None,
        "buyboxSeller": None,
        "buyboxType": None,
        "holdingBuybox": False,
        "myPrice": my_price,
        "myStock": item.get("myStock"),
        "offers": [],
    }

    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # A successful HTTP status can still be an interstitial, not a product.
        initial_title=(await page.title() or '').strip().lower()
        if initial_title in ('just a moment...', 'just a moment', 'attention required! | cloudflare'):
            result['error']='site_challenge'
            return result
        if resp and (resp.status in (401,403,404,429) or resp.status>=500):
            result['error']=f'http_{resp.status}'
            return result
        await page.wait_for_timeout(random.randint(900, 1600))
        # 等待核心区块渲染（buybox 或变体选择器），避免页面未加载完就检测
        # 等待核心区块渲染：buybox 卡或变体选择器任一出现即可（多候选兜底）
        try:
            await page.wait_for_selector(
                ", ".join(SELECTORS["buybox_card"] + SELECTORS["variant"]), timeout=12000
            )
        except Exception:
            pass
        await page.wait_for_timeout(random.randint(500, 900))

        # ---- 变体切换：页面有变体选择器且商品名含变体后缀时，切到对应变体再抓 buybox ----
        # 背景：Takealot 变体商品（颜色/尺寸）共用同一产品页 URL，默认展示主变体价格；
        # 我方 SKU 是其中一个变体，必须切到对应变体才能读到正确的 buybox 价与持有状态。
        variant_suffix = None
        nm = (item.get("name") or "").strip()
        if " - " in nm:
            variant_suffix = nm.rsplit(" - ", 1)[1].strip()
        if variant_suffix:
            from urllib.parse import quote_plus

            async def _read_cards():
                cards = []
                bcs, _ = await query_all_fallback(page, "buybox_card")
                for bc in bcs:
                    try:
                        price = await price_of(page, bc)
                        txt = ((await bc.inner_text()) or "").lower()
                        cards.append({
                            "price": price,
                            "txt": txt,
                            "isToday": "get it today" in txt,
                            "isFastest": "fastest delivery" in txt,
                        })
                    except Exception:
                        continue
                return cards

            def _calc_buybox(cards):
                # Buybox 归属（2026-10-08 修正，与主路径 _read_buybox_cards 一致）：
                # 默认购买展示位 = 现货卡中最低价（map_card_stock 判定现货），
                # 而非 Fastest Delivery 卡优先。反例 PLID91643007：Fastest 209 实为
                # 我方 Other Offers 高价卡，Buybox 主卡是最低价现货 208（Valuezy）。
                in_stock = [c["price"] for c in cards if c.get("price") and map_card_stock(c.get("txt", "")) == "in_stock"]
                if in_stock:
                    return min(in_stock)
                today = [c["price"] for c in cards if c.get("isToday") and c.get("price")]
                allp = [c["price"] for c in cards if c.get("price")]
                if today:
                    return min(today)
                if allp:
                    return min(allp)
                return None

            async def _wait_change(before, timeout_s=8.0):
                """轮询等待 buybox 价格与切换前不同，用于验证变体切换真正生效（点击与 URL 直达共用）"""
                waited = 0.0
                while waited < timeout_s:
                    await page.wait_for_timeout(500)
                    waited += 0.5
                    cards = await _read_cards()
                    cur = _calc_buybox(cards)
                    if cur is not None and (before is None or abs(cur - before) > 0.001):
                        return cur
                return None

            async def _selected_target():
                """目标变体是否已被选中（选中态确认）。

                价格未变化时用它补判，修复两类"假失败"：
                  ① 目标变体价 == 默认变体价（切换成功但价格不变，如 Earrings 120/120）；
                  ② 本页无 buybox 卡（before_price=None，价格判据永远等不到变化）。
                严格性：必须"被标记为选中的元素"其规范化文本 == 目标后缀才算命中，
                容器类元素（class 含 variant 但非选中项）不会误命中，故不会把真失败判成成功。
                读不到选中态返回 False，自动退回"价格变化"判据。"""
                target = _norm_label(variant_suffix)
                if not target:
                    return False
                vbcs, _ = await query_all_fallback(page, "variant")
                for b in vbcs:
                    try:
                        st = await b.evaluate(_VARIANT_STATE_JS)
                    except Exception:
                        continue
                    if st and st.get("selected") and _norm_label(st.get("label")) == target:
                        return True
                return False

            # 1) 先读切换前（默认变体）buybox，作为切换是否生效的基准
            before_cards = await _read_cards()
            before_price = _calc_buybox(before_cards)
            if before_price is None:
                await page.wait_for_timeout(1500)
                before_cards = await _read_cards()
                before_price = _calc_buybox(before_cards)
            sep = "&" if "?" in url else "?"
            switched_price = None
            switch_method = ""
            switch_verified = False
            # 2) URL 参数直达优先（colour_variant 实测对颜色变体有效；size_variant 兜底尺寸变体）
            for param in ("colour_variant", "size_variant"):
                try:
                    await page.goto(f"{url}{sep}{param}={quote_plus(variant_suffix)}",
                                    wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    continue
                switched_price = await _wait_change(before_price)
                if switched_price is not None:
                    switch_method = f"URL {param}"
                    switch_verified = True
                    break
                # 价格未变化 ≠ 切换失败：用选中态补判（见 _selected_target 说明）
                if await _selected_target():
                    switch_method = f"URL {param}(选中态确认)"
                    switch_verified = True
                    break
            # 3) URL 直达未能确认 → 回到原页点击精确变体按钮，轮询验证
            if not switch_verified:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(1500)
                except Exception:
                    pass
                vbcs, _ = await query_all_fallback(page, "variant")
                target_norm = _norm_label(variant_suffix)
                for b in vbcs:
                    try:
                        bt = _norm_label(await b.inner_text())
                    except Exception:
                        continue
                    if bt and bt == target_norm:
                        try:
                            await b.click()
                        except Exception:
                            pass
                        switched_price = await _wait_change(before_price)
                        if switched_price is not None:
                            switch_method = "点击"
                            switch_verified = True
                        elif await _selected_target():
                            switch_method = "点击(选中态确认)"
                            switch_verified = True
                        break
            log(f"  变体切换: 后缀='{variant_suffix}' before={before_price} after={switched_price} 方式={switch_method or '失败(仍为默认变体)'}")
            # 切换失败：页面仍是默认变体，本轮读到的 buybox/竞品价并非我方 SKU 对应变体的价格。
            # 上报并标记，由后端决定如何处理（默认：不据此触发调价，避免按错误变体价格改价）。
            if not switch_verified:
                result["variantSwitchFailed"] = True

        # 滚动触发懒加载
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(500)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(400)

        title = await page.title() or ""
        if "404" in title or "not found" in title.lower():
            result["error"] = "404"
            return result

        # 双 Buybox 卡读取（含稳定性验证：双读一致才采信）
        # Takealot 多 offer 时 buybox-offer 区块渲染两张卡：
        #   Fastest Delivery（配送最快，目标位）与 Best Price（低价，配送时效可能更慢/预售）。
        # Buybox 归属规则（2026-10-08 修正）：
        #   1) 现货卡中最低价 = 默认购买展示位（Buybox 主卡）；
        #      反例 PLID91643007：Fastest 209 实为我方 Other Offers 高价卡，
        #      Buybox 主卡是最低价现货 208（Valuezy），旧规则"Fastest优先"会把 209 误判成持有。
        #   2) 无现货卡时取 Get It Today 卡最低价；
        #   3) 最后兜底取全部卡最低价。
        # 双卡策略：Buybox 归属按上述规则；同时分别上报 Fastest 卡价与 Best Price 卡价（含卡级
        # 现货状态），供后端仲裁 holding（持任一 Buybox 位算持有）与引擎对标（Best Price 预售不参与竞价）。
        async def _read_buybox_cards():
            """返回 {buybox, fastest, best_price, cards}；无卡时返回 None。
            fastest/best_price 为单卡 dict：{price, status, deliveryLabel, isFastest, isBestPrice}"""
            cards = []
            bcs, _ = await query_all_fallback(page, "buybox_card")
            for bc in bcs:
                try:
                    price = await price_of(page, bc)
                    txt = ((await bc.inner_text()) or "").lower()
                    if price is not None:
                        cards.append({
                            "price": price,
                            "txt": txt,
                            "isToday": "get it today" in txt,
                            "isFastest": "fastest delivery" in txt,
                            "isBestPrice": "best price" in txt,
                        })
                except Exception:
                    continue
            if not cards:
                return None

            def _pick(status_kind: str):
                """从卡列表选目标卡：优先显式文案，兜底非 Fastest 卡中价格最低者（Best Price 为低价卡）"""
                explicit = [c for c in cards if c.get("isFastest" if status_kind == "fastest" else "isBestPrice")]
                if explicit:
                    return min(explicit, key=lambda c: c["price"])
                if status_kind == "best_price":
                    non_fastest = [c for c in cards if not c.get("isFastest")]
                    if non_fastest:
                        return min(non_fastest, key=lambda c: c["price"])
                return None

            def _card_info(c):
                if not c:
                    return None
                return {
                    "price": c["price"],
                    "status": map_card_stock(c["txt"]),
                    "deliveryLabel": c["txt"][:120],
                    "isFastest": c.get("isFastest"),
                    "isBestPrice": c.get("isBestPrice"),
                }

            fastest_c = _pick("fastest")
            best_c = _pick("best_price")
            # Buybox 归属（2026-10-08 实测修正）：默认购买展示位 = 现货卡中最低价，
            # 而非 Fastest Delivery 卡优先。反例 PLID91643007：Fastest 209 实为我方
            # Other Offers 高价卡，Buybox 主卡是最低价现货 208（Valuezy），
            # Fastest 优先会把自家 209 误判成持有 Buybox。
            in_stock_prices = [c["price"] for c in cards if map_card_stock(c["txt"]) == "in_stock"]
            today_prices = [c["price"] for c in cards if c["isToday"]]
            if in_stock_prices:
                buybox = min(in_stock_prices)
            elif today_prices:
                buybox = min(today_prices)
            else:
                buybox = min(c["price"] for c in cards)
            return {
                "buybox": buybox,
                "fastest": _card_info(fastest_c),
                "best_price": _card_info(best_c),
                "cards": cards,
            }

        # 稳定性验证：轮询 2 次 buybox 价一致才采信；不一致再读第 3 次取多数派；
        # 3 次互不一致则标记 unstable_price 放弃本轮（防单次偶发抓价错误——如 314 误抓 315——触发错误调价）
        r1 = await _read_buybox_cards()
        await page.wait_for_timeout(random.randint(1200, 2000))
        r2 = await _read_buybox_cards()
        buybox_price = r1["buybox"] if r1 else None
        second = r2["buybox"] if r2 else None
        stable_round = r1 if (buybox_price is not None and second is not None and abs(buybox_price - second) < 0.001) else None
        if stable_round is None:
            await page.wait_for_timeout(random.randint(1200, 2000))
            r3 = await _read_buybox_cards()
            third = r3["buybox"] if r3 else None
            candidates = [buybox_price, second, third]
            non_null = [c for c in candidates if c is not None]
            if non_null:
                counts: dict[float, int] = {}
                for c in non_null:
                    counts[c] = counts.get(c, 0) + 1
                top = max(counts.items(), key=lambda kv: kv[1])
                if top[1] >= 2:
                    buybox_price = top[0]
                    stable_round = next((r for r in (r1, r2, r3) if r and r["buybox"] == top[0]), r1)
                else:
                    log(f"  -> buybox 价格不稳定: {candidates}，本轮放弃该商品")
                    result["error"] = "unstable_price"
                    return result
            else:
                buybox_price = None
        result["buyboxPrice"] = buybox_price

        # 双卡快照上报（供后端仲裁/引擎对标）
        stable_round = stable_round or r1
        fastest_info = (stable_round.get("fastest") if stable_round else None) or None
        best_info = (stable_round.get("best_price") if stable_round else None) or None
        result["fastestPrice"] = fastest_info["price"] if fastest_info else None
        result["bestPricePrice"] = best_info["price"] if best_info else None
        result["bestPricePreorder"] = bool(best_info and best_info["status"] != "in_stock")

        # 现货状态（多候选选择器兜底，命中即停）
        stock_text = None
        for sel in SELECTORS["stock"]:
            stock_el = await page.query_selector(sel)
            if stock_el:
                t = (await stock_el.inner_text()).strip()
                if t:
                    stock_text = t
                    break
        status = map_stock(stock_text)
        # 强兜底：Buybox 区块渲染出价格即视为现货（缺货商品不展示 buybox 价格）
        if buybox_price is not None:
            status = "in_stock"
        # 兜底：stock 区块未渲染时用页面标题判定现货；标题异常（反爬拦截/加载失败/跳转首页）则标记采集失败
        if status == "out_of_stock":
            title = (await page.title() or "").lower()
            if "shop today" in title or "get it tomorrow" in title:
                status = "in_stock"
            elif buybox_price is None and (
                "online shopping" in title or "leading online store" in title
                or "/x/" not in (page.url or "")
            ):
                # 反爬拦截或页面被重定向到首页/非商品页（标题为首页特征且 URL 不再指向商品页），
                # 标记采集失败而非缺货，避免污染后端 buybox/stock 状态
                result["error"] = "blocked_or_load_failed"
                result["stockStatus"] = stock_text
                result["stockMapped"] = status
                return result
            elif buybox_price is None and "takealot.com" not in title:
                # 疑似反爬拦截或页面加载失败，标记采集失败而非缺货
                result["error"] = "blocked_or_load_failed"
                result["stockStatus"] = stock_text
                result["stockMapped"] = status
                return result
        result["stockStatus"] = stock_text
        result["stockMapped"] = status

        # 主 offer（buybox）卖家名
        buybox_seller = await extract_seller_name(page)
        # 判定是否持有 Buybox：仅依据"Buybox 展示价 == 我方当前价"（my_price 来自后端 DB）。
        # 注意：不能再用"页面无 Other sellers 区块/单卡 → 唯一卖家即我方"推断——
        # Takealot 改版后前台不再暴露卖家名，且 Buybox 只渲染赢家卡：对手赢得 Buybox 时
        # 页面同样只有一张卡（active），把单卡误认成"我方唯一"会把对手价当持有。
        # 采集器只做保守判定，最终持有结论由后端用 Seller API 权威价仲裁（权威价==Buybox 价才真持有）。
        has_other_sellers = len(await page.query_selector_all("text=/other (sellers|offers)/i")) > 0
        is_mine = buybox_price is not None and (my_price is not None and abs(buybox_price - my_price) < 0.01)
        result["holdingBuybox"] = is_mine
        result["hasOtherSellers"] = has_other_sellers
        result["buyboxSeller"] = buybox_seller
        result["buyboxType"] = "buybox"
        if buybox_price is not None:
            result["offers"].append({
                "seller": buybox_seller or "buybox",
                "price": buybox_price,
                "status": status,
                "offerType": "buybox",
                "isMine": is_mine,
                "deliveryLabel": stock_text,
                "leadtimeDays": None,
            })

        # 双卡明细上报（offerType=fastest / best_price）：供后端仲裁 holding 与引擎对标
        # Fastest 卡：目标位，现货快送；Best Price 卡：低价位，可能慢配/预售（status 由卡级文案判定）
        # isMine 逐卡独立判定（该卡价格==我方当前价才算我方持有该卡位），
        # 不复用 buybox 的 is_mine——反例 PLID91643007：buybox 归为 208 后 is_mine=False，
        # 但 fastest 209 实为我方 Other Offers 高价卡，若沿用 buybox is_mine 会把 209 误标 mine。
        if fastest_info is not None:
            result["offers"].append({
                "seller": buybox_seller or "fastest",
                "price": fastest_info["price"],
                "status": fastest_info["status"],
                "offerType": "fastest",
                "isMine": my_price is not None and abs(fastest_info["price"] - my_price) < 0.01,
                "deliveryLabel": fastest_info["deliveryLabel"],
                "leadtimeDays": None,
            })
        if best_info is not None:
            result["offers"].append({
                "seller": buybox_seller or "best_price",
                "price": best_info["price"],
                "status": best_info["status"],
                "offerType": "best_price",
                "isMine": my_price is not None and abs(best_info["price"] - my_price) < 0.01,
                "deliveryLabel": best_info["deliveryLabel"],
                "leadtimeDays": None,
            })

        # Other Sellers 竞品（页面存在多个卖家时抓取）
        for o in await extract_other_sellers(page, my_price):
            result["offers"].append(o)
        return result
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        return result
