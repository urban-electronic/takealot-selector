"""PricePilot collector parsing core; adapted from native source 2026-10-12."""

import asyncio, math, random, re, time

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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

_MONTHS = {m: i for i, m in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), start=1)}


def _days_until(day: int, month: str) -> int | None:
    """「9 月 16 日」是几天之后。跨年（12 月看 1 月）按明年算。"""
    m = _MONTHS.get((month or '')[:3].lower())
    if not m:
        return None
    today = datetime.now(ZoneInfo('Africa/Johannesburg')).date()
    try:
        target = today.replace(month=m, day=day)
    except ValueError:
        return None
    if target < today:
        try:
            target = target.replace(year=today.year + 1)
        except ValueError:
            return None
    return max(0, (target - today).days)


def parse_leadtime(text: str | None) -> int | None:
    """从配送文案里读出「几天到」。读不出来就返回 None，不猜。

    这是唯一能拿到的时效证据：卡片上的配送承诺。之前 leadtimeDays 在
    三个报价类型上全是 None，于是「时效位」和「价格位」在引擎眼里毫无区别
    ——它会在最快配送卡位上靠降价去抢一个由配送速度决定的展示位。

    只解析明确的送达承诺，区间以截止日为代表值（"2-4 days" → 4）。
    发货时长、工作日不等于自然日送达天数，未知时保留原文、不推断。
    """
    if not text:
        return None
    # Dispatch promises and working-day durations cannot be compared with calendar
    # arrival days without a dispatch date and the destination holiday calendar.
    arrival = re.search(r'\b(?:get it|delivery|delivered|delivers)\b(?:\s+(?:in|on|by|from|between))?\s+([^.;\n]+)', text.lower())
    if not arrival:
        return None
    t = re.split(r'\bships?\b', arrival.group(1), maxsplit=1)[0]
    if re.search(r'\b(?:working|business)\s+days?\b', t):
        return None
    if 'today' in t:
        return 0
    if 'tomorrow' in t:
        return 1
    # "delivery in 2-4 days" / "delivers in 3 - 5 working days"
    m = re.search(r'^(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:working\s+)?days', t)
    if m:
        return int(m.group(2))
    m = re.search(r'^(\d{1,2})\s*(?:working\s+)?days', t)
    if m:
        return int(m.group(1))
    # "delivery 11 sep - 16 sep" / "delivered 11 sep to 16 sep"
    m = re.search(r'(\d{1,2})\s+([a-z]{3})[a-z]*\s*(?:-|–|to)\s*(\d{1,2})\s+([a-z]{3})', t)
    if m:
        return _days_until(int(m.group(3)), m.group(4))
    # "delivery 11 sep"
    m = re.search(r'(\d{1,2})\s+([a-z]{3})[a-z]*', t)
    if m:
        return _days_until(int(m.group(1)), m.group(2))
    return None


SELECTORS = {
    # buybox 卡**只使用主选择器，不做自动降级**：
    # 缺货/无 offer 的商品本就没有 buybox 卡，此时主选择器正确返回空；
    # 而宽松候选（如 [class*="buybox"]）会匹配到页面其他元素，读出非 buybox 的价格，
    # 据此调价将造成真实亏损。故此处不配候选——改版时宁可"采不到"也不"采错"；
    # 真改版了由 classify() 报 page_structure_changed / page_not_recognized
    # （0 个 buybox 卡但页面有价格 / 什么都认不出），交人工维护选择器。
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

async def query_all_fallback(page, kind: str):
    """按优先级尝试候选选择器，返回 (elements, selector)。
    主选择器失效（改版）时自动降级到候选；全部失效返回 ([], None)。"""
    for i, sel in enumerate(SELECTORS[kind]):
        try:
            els = await page.query_selector_all(sel)
            if els:
                if i > 0:
                    log(f"  [选择器降级] {kind}: 主选择器失效，已降级到候选#{i} {sel}")
                return els, sel
        except Exception:
            continue
    return [], None

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

# 变体控件宽松探针：查常见变体 UI 特征，但**排除推荐商品卡**。
# 实测（2026-10-15，PLID97512496）：Takealot 推荐商品卡用
# `colour-variant-icon-text` / `product-card-module_*` 渲染 "More Colours" teaser，
# 会被 [class*="variant"] / [data-ref*="variant"] 误命中（单页 11-12 个），
# 导致单报价商品被误判为变体商品。误报方向安全（继续尝试切换，失败仍保守上报），
# 漏报会把变体商品默认规格当目标 SKU 报价采回，不可接受，故探针刻意取宽。
_VARIANT_PROBE_JS = r"""
() => {
  const nodes = document.querySelectorAll(
    '[data-ref*="variant"], [data-ref*="swatch"], [class*="variant"], [class*="swatch"],' +
    ' [aria-label*="colour" i], [aria-label*="color" i], [aria-label*="size" i],' +
    ' [role="radiogroup"], [role="listbox"]');
  let count = 0;
  for (const n of nodes) {
    if (n.closest('[class*="product-card"]')) continue;
    count++;
  }
  return count;
}
"""

# 主商品核心区块就绪判定：buybox 卡或主商品区变体控件任一出现
# （不用 text= 选择器；与 page_variant_controls 的作用域保持一致，
#  避免推荐卡 teaser 让等待提前返回、变体区尚未渲染就下结论）
_CORE_BLOCK_JS = r"""
() => {
  if (document.querySelector('[data-ref="buybox-offer"]')) return true;
  const nodes = document.querySelectorAll(
    '[data-ref*="variant"], [data-ref*="swatch"], [class*="variant"], [class*="swatch"]');
  for (const n of nodes) {
    if (!n.closest('[class*="product-card"]')) return true;
  }
  return false;
}
"""

# 元素作用域判定脚本：是否位于推荐商品卡内（与 _VARIANT_PROBE_JS 同一规则）
_CARD_SCOPE_JS = "(el) => !!el.closest('[class*=\"product-card\"]')"

async def main_variant_elements(page):
    """主商品区的变体控件元素（排除推荐商品卡内的变体 teaser）。

    修复背景（2026-10-15 实测失败样本 PLID97512496，SKU 9902348621330）：
    商品名 "4K Thump action camera - professional package for motor, biking,
    skydiving" 中的 " - " 只是标题分隔符，并非变体后缀；该页主商品区没有任何
    变体控件（页面自身 API 响应也无 variant/colour 结构，单 buybox 卡单报价），
    旧逻辑却据此尝试 colour_variant/size_variant 切换，均无法确认，误报
    variant_unmatched，阻塞本轮采集。单报价商品页不存在"默认变体 vs 目标变体"
    歧义，PLID 已唯一确定商品与报价，无需切换，应直接读取唯一报价。
    注意：点击路径同样必须排除推荐卡——卡内 swatch 的文案可能恰好等于变体后缀，
    点击会跳到推荐商品，属于采错价，不可接受。

    返回 (elements, complete)。complete=False 表示有元素的作用域无法判定
    （evaluate 抛异常：元素已分离、执行上下文销毁等）。此时**不得**据本次
    结果断定"无变体控件"——Codex 复核 P1（2026-10-15）：把"未能判断"降级成
    "确定无变体"会清空变体后缀并放行默认变体报价，属于采错价，不可接受。
    """
    els, _ = await query_all_fallback(page, "variant")
    main = []
    complete = True
    for el in els:
        try:
            in_card = await el.evaluate(_CARD_SCOPE_JS)
        except Exception:
            complete = False
            continue
        if not in_card:
            main.append(el)
    return main, complete

async def page_variant_controls(page):
    """主商品区真实变体控件数量；**无法判断时返回 None**。

    调用方必须把 None 当作"可能存在变体"保守处理（继续切换校验，失败则
    variantSwitchFailed），不得当作 0 放行默认报价。返回 int 时含义确定：
    0 = 主商品区确实没有变体控件；>0 = 有（含探针补计）。
    """
    main, complete = await main_variant_elements(page)
    if not complete:
        return None
    count = len(main)
    try:
        probe = await page.evaluate(_VARIANT_PROBE_JS)
        if isinstance(probe, int) and probe > count:
            count = probe
    except Exception:
        # 探针执行失败（上下文销毁等）：无法判断，返回 None 交由调用方保守处理
        return None
    return count

# 卡片文本里的 ZAR 金额。R 后面可能紧跟数字，也可能是 "R1 299" 这种空格千分位。
_AMOUNT = re.compile(r'R\s?(\d[\d\s,\u00a0]*(?:\.\d{2})?)', re.I)

def _to_amount(raw: str) -> float | None:
    s = (raw or '').replace('\u00a0', ' ').replace(' ', '').replace(',', '')
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None

def distinct_prices(text: str) -> list[float]:
    """一段文本里出现的所有不同金额（升序去重）。只用来判断"能不能唯一确定一个价"。"""
    if not text:
        return []
    out: list[float] = []
    for m in _AMOUNT.finditer(text):
        v = _to_amount(m.group(1))
        if v is not None and v not in out:
            out.append(v)
    return sorted(out)

# 卡片里"当前价"通常紧挨着一个划线原价，页面文本形如 `r 269r 899`（两个相邻元素，
# innerText 之间没有空白）。直接数金额会得到 2 个，"卡内唯一金额"这个判据就失效了——
# 这正是 2026-10-17 那批全量 price_not_found 的成因。先把划线/原价节点摘掉再数：
# 判据本身（摘掉后只剩一个金额才采信）不变，只是不再被划线价干扰。
STRUCK_SELECTORS = ','.join([
    's', 'del', 'strike',
    '[style*="line-through"]', '[class*="line-through"]', '[class*="lineThrough"]',
    '[class*="strike"]', '[class*="was-price"]', '[class*="old-price"]',
    '[class*="list-price"]', '[class*="rrp"]',
    '[data-ref*="was"]', '[data-ref*="list-price"]', '[data-ref*="rrp"]',
])

# 在克隆节点上摘掉划线价再取文本：不动真实页面，避免影响后续变体/库存读取。
_CARD_TEXT_JS = """
(el, struck) => {
  const clone = el.cloneNode(true);
  try { clone.querySelectorAll(struck).forEach(n => n.remove()); } catch (e) {}
  return clone.textContent || '';
}
"""

async def card_text_without_struck(card) -> str:
    """卡片文本，去掉划线原价节点。取不到就返回空串，让调用方放弃而不是猜。"""
    try:
        return await card.evaluate(_CARD_TEXT_JS, STRUCK_SELECTORS)
    except Exception:
        return ''

async def price_of(page, card) -> float | None:
    """从卡片内提取价格。

    三条路，按可信度从高到低：
    1) 按选择器取价格节点（正常路径，最可信）；
    2) 摘掉划线原价节点后读卡片文本，卡内只剩一个金额才采信；
    3) 原始卡片文本本来就只有唯一金额时同样采信。

    为什么需要 2/3：Takealot 卡片把"当前价"和"划线原价"并排渲染，文本是
    `r 269r 899` 这种——两个金额都在卡里，直接数就永远"不唯一"。但当前价是
    唯一真实成交价，划线价只是营销锚点。摘掉划线节点后仍不唯一（出现运费、
    满减、第二个真实报价）就放弃。宁可采不到，也不采错：这条价直接喂自动调价，
    读错就是真亏钱。
    """
    for sel in SELECTORS["price"]:
        try:
            pr = await card.query_selector(sel)
            if pr is not None:
                p = parse_price(await pr.inner_text())
                if p is not None:
                    return p
        except Exception:
            continue
    # 降级：候选选择器一个都没命中，改从卡片文本里取唯一金额。
    amounts = distinct_prices(await card_text_without_struck(card))
    if len(amounts) == 1:
        return amounts[0]
    # 摘不干净（划线价不是独立节点）时，原始文本本来就只有唯一金额也可信。
    try:
        amounts = distinct_prices(await card.inner_text())
    except Exception:
        return None
    if len(amounts) == 1:
        return amounts[0]
    return None

CHALLENGE_TITLES = ('just a moment...', 'just a moment', 'attention required! | cloudflare')

async def wait_initial_document(page):
    """One passive load wait, without reloads or interaction with verification."""
    initial=(await page.title() or '').strip().lower()
    if initial not in CHALLENGE_TITLES: return False
    try:
        await page.wait_for_function(
            "!['just a moment...', 'just a moment', 'attention required! | cloudflare'].includes(document.title.trim().toLowerCase())",
            timeout=12000)
    except Exception:
        return None
    return True if (await page.title() or '').strip().lower() not in CHALLENGE_TITLES else None

def variant_labels_from_name(name):
    """商品名里所有尾随的变体段：'... Shapewear - Nude - L' → ['Nude', 'L']。

    原来只取最后一段（`rsplit(' - ', 1)`），于是 'Nude - L' 只得到 'L'：
    拿尺码去当颜色参数、再去找标签等于 'L' 的按钮，两处都必然落空——
    2026-10-18 SKU 9902415247821 连续 3 轮 variant_unmatched 就是这么来的。
    变体是**多维**的：颜色和尺码都得选，少选一个页面就停在 'Select an option'
    占位状态，Buybox 卡里根本没有报价（实测 card_text_len=5）。

    这里只做「切分」，不做「是不是变体」的判断——那要靠页面：页面没有变体
    控件时 PLID 已唯一确定报价，调用方会把整组标签清空。
    """
    text = (name or "").strip()
    if " - " not in text:
        return []
    parts = [p.strip() for p in text.split(" - ")]
    return [p for p in parts[1:] if p]


# 下拉型变体的触发器文案。Takealot 的尺码是「Select an option」按钮，
# 选项要点开才在 DOM 里出现，所以不能只靠「找标签相同的控件」。
_DROPDOWN_TRIGGER_RE = re.compile(
    r'^(select an option|select a colour|select a color|select colour|select color|'
    r'select a size|select size|choose|please select|select)$', re.I)

# 弹出列表里可能的选项节点。分两轮：先只找语义明确的选项，找不到再放宽到
# li/button/a。一上来就用宽选择器会在整页几百个 li 上逐个判可见性，慢且容易误点。
_OPTION_SELECTORS = (
    ('[role="option"]', '[role="menuitem"]', '[role="radio"]',
     '[data-ref*="option"]', '[class*="option-item"]', '[class*="optionItem"]'),
    ('ul li', 'ol li', 'button', 'a'),
)


async def _selected_for_label(page, label):
    """某个变体值当前是否处于「已选中」状态。读不到一律返回 False（保守）。"""
    target = _norm_label(label)
    if not target:
        return False
    # ① 原生 select：选中项文本等于目标
    try:
        selects = await page.query_selector_all('select')
    except Exception:
        selects = []
    for node in selects:
        try:
            chosen = await node.evaluate(
                "el => { const o = el.options[el.selectedIndex]; return o ? (o.text || '').trim() : ''; }")
        except Exception:
            continue
        if chosen and _norm_label(chosen) == target:
            return True
    # ② 主商品区里带选中态的控件
    els, _complete = await main_variant_elements(page)
    for el in els:
        try:
            state = await el.evaluate(_VARIANT_STATE_JS)
        except Exception:
            continue
        if state and state.get("selected") and _norm_label(state.get("label")) == target:
            return True
    return False


async def _pick_open_option(page, target):
    """在下拉弹出列表里找文本等于 target 的可见选项。找不到返回 None。"""
    for selectors in _OPTION_SELECTORS:
        try:
            candidates = await page.query_selector_all(','.join(selectors))
        except Exception:
            continue
        for node in candidates:
            try:
                if await node.evaluate(_CARD_SCOPE_JS):
                    continue
                if not await node.is_visible():
                    continue
                if _norm_label(await node.inner_text()) == target:
                    return node
            except Exception:
                continue
    return None


async def _wait_selected(page, label, timeout_ms=4000, interval_ms=300):
    """点击后等页面把选中态落定，返回是否确认选中。

    原来点完固定等 700ms 就判一次。Takealot 切变体会重新请求报价再重绘，
    700ms 时 DOM 常常还没更新——2026-10-27 实测 4 条商品同时在
    「成功 → 失败 → 成功」之间来回跳，failures 全是 1，失败率约 50%，
    而 diagnostics 里页面确实有同名按钮（variant_sample 能读到 "Grey"）。
    判据不变（还是 `_selected_for_label`），只是给页面更多时间：
    减少假红，不会把没选中读成选中。
    """
    waited = 0
    while True:
        if await _selected_for_label(page, label):
            return True
        if waited >= timeout_ms:
            return False
        await page.wait_for_timeout(interval_ms)
        waited += interval_ms


async def apply_variant_label(page, label):
    """把某一个变体维度切到 label。返回 (是否确认选中, 方式)。

    三种控件都试：原生 select → 标签相同的可点控件 → 下拉触发器点开再选。
    每条路径都以「选中态」收尾验证，不用价格变化当证据——本页在选全之前
    压根没有价格，价格判据在这里永远等不到。
    """
    target = _norm_label(label)
    if not target:
        return False, ""
    if await _selected_for_label(page, label):
        return True, "already"

    # A) 原生 select
    try:
        selects = await page.query_selector_all('select')
    except Exception:
        selects = []
    for node in selects:
        try:
            options = await node.evaluate(
                "el => [...el.options].map(o => (o.text || '').trim())")
        except Exception:
            continue
        for option_text in options or []:
            if _norm_label(option_text) != target:
                continue
            try:
                await node.select_option(label=option_text)
            except Exception:
                break
            await page.wait_for_timeout(600)
            if await _selected_for_label(page, label):
                return True, "select"
            break

    els, _complete = await main_variant_elements(page)

    # B) 标签精确相同的控件，直接点
    for el in els:
        try:
            if _norm_label(await el.inner_text()) != target:
                continue
        except Exception:
            continue
        # 元素可能在视口外或被吸顶头部盖住：先滚进视口再点。原来直接 click()，
        # 被遮挡时抛 ElementClickIntercepted 后走 except 静默跳过，界面上只留下
        # 一句「未能切到变体」，看不出是点不进去还是点了没反应。
        # 滚动本身失败不能连累点击（老实现/测试桩可能没有这个方法）。
        scroll = getattr(el, 'scroll_into_view_if_needed', None)
        if scroll is not None:
            try:
                await scroll(timeout=3000)
            except Exception:
                pass
        try:
            await el.click()
        except Exception:
            continue
        if await _wait_selected(page, label):
            return True, "click"

    # C) 下拉触发器：点开，再从弹出列表里选
    for el in els:
        try:
            trigger_text = _norm_label(await el.inner_text())
        except Exception:
            continue
        if not _DROPDOWN_TRIGGER_RE.match(trigger_text or ""):
            continue
        try:
            await el.click()
        except Exception:
            continue
        await page.wait_for_timeout(700)
        option = await _pick_open_option(page, target)
        if option is not None:
            try:
                await option.click()
            except Exception:
                pass
            if await _wait_selected(page, label):
                return True, "dropdown"
            # 下拉选完后会把选中的值写回触发器自身文本（"Select an option" → "L"）。
            # 部分实现不给选项加 aria-selected，这条是最直接的证据；只认**这个
            # 触发器自己**的文本变化，不会把别处的同名文案当成已选中。
            try:
                if _norm_label(await el.inner_text()) == target:
                    return True, "dropdown(触发器回显)"
            except Exception:
                pass
        # 没选中就把下拉收起来，避免遮挡后面的点击
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
    return False, ""


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
        document_settled=await wait_initial_document(page)
        if document_settled is None:
            result['error']='site_challenge'
            return result
        # A naturally completed initial interstitial may have returned 403 before
        # navigating to the product. Still require real quote cards below.
        if resp and (resp.status in (401,403,404,429) or resp.status>=500) and not (document_settled and resp.status==403):
            result['error']=f'http_{resp.status}'
            return result
        await page.wait_for_timeout(random.randint(900, 1600))
        # 等待核心区块渲染：buybox 卡或**主商品区**变体控件任一出现即可。
        # 不用 text=/class*="variant" 宽选择器——推荐卡 teaser 遍布页面，会让等待
        # 提前返回，主商品区尚未渲染就做变体判断（实测 PLID97512496 推荐卡内有
        # 11-12 个 "More Colours" teaser，主商品区实为 0 个变体控件）。
        try:
            await page.wait_for_function(_CORE_BLOCK_JS, timeout=12000)
        except Exception:
            pass
        await page.wait_for_timeout(random.randint(500, 900))

        # ---- 变体选择：多维（颜色 + 尺码）逐个切到目标值，再抓 buybox ----
        # 背景：Takealot 变体商品共用同一产品页 URL，默认展示主变体；我方 SKU 是
        # 其中一个变体**组合**，每个维度都得切到目标值，页面才会给出报价。
        # 只切一个维度不够：2026-10-18 PLID91715202（名 '... - Nude - L'）页面停在
        # 'Select an option'，Buybox 卡文本只有 5 个字符，压根没有报价。
        variant_labels = variant_labels_from_name(item.get("name"))
        # 前置门控：仅当页面**主商品区真实存在变体控件**时才尝试选择。名称含 " - "
        # 不等于存在变体（"... - professional package for motor, biking, skydiving"
        # 只是标题分隔符）；无控件时 PLID 唯一确定商品与报价，不存在默认/目标变体
        # 歧义，直接读取唯一报价，不再误报 variant_unmatched。
        variant_controls = await page_variant_controls(page)
        if variant_labels and (variant_controls is None or variant_controls == 0):
            # ① 无法判断（探测失败/元素作用域不可判定）——保守保留标签，继续尝试，
            #    确认不了则上报 variantSwitchFailed，绝不按"无变体"放行默认报价；
            # ② 确定 0，但变体区可能比 buybox 卡稍晚渲染——短等待后复核一次。
            await page.wait_for_timeout(600)
            variant_controls = await page_variant_controls(page)
        if variant_labels and variant_controls == 0:
            variant_labels = []
        result["variantControls"] = variant_controls
        if variant_labels:
            confirmed, methods, unmatched = [], [], []
            for label in variant_labels:
                ok, method = await apply_variant_label(page, label)
                methods.append(f"{label}:{method or 'fail'}")
                if ok:
                    confirmed.append(label)
            # 严格口径：**每一个**变体段都必须确认选中。少确认一个，页面给的就是
            # 默认变体的价，拿它去调价等于采错价。所以这里不做"页面上没这个值就
            # 忽略"的宽松处理——名称尾段到底是副标题还是缺失的变体，程序分不清，
            # 分不清就按不安全的那一侧处理：不采信，并在 diagnostics 里点名是哪段。
            unmatched = [lab for lab in variant_labels if lab not in confirmed]
            result["variantApplied"] = confirmed
            result["variantMethods"] = methods
            if unmatched:
                result["variantUnmatched"] = unmatched
                result["variantSwitchFailed"] = True
            log(f"  变体选择: 标签={variant_labels} 已确认={confirmed} 未匹配={unmatched} 方式={methods}")
            # 选全之后 buybox 会重新渲染，等它出价再往下读
            await page.wait_for_timeout(900)


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
                            # 卡片上的配送承诺就是时效证据。采在这里，而不是等
                            # 后端再想办法——后端拿不到这段文案。
                            "leadtime": parse_leadtime(txt),
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
                    "leadtimeDays": c.get("leadtime"),
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
                # Buybox 主卡自己的时效。价格相等时可能匹配到多张卡，取最短的那个
                # ——主卡是「最低价现货」，同价里客户会挑最快的。
                "buyboxLeadtime": min((c["leadtime"] for c in cards
                                       if abs(c["price"] - buybox) < 0.01 and c.get("leadtime") is not None),
                                      default=None),
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
        # Buybox 归属判定，两种证据：
        #   ① 本店卖家名已知 → 直接比页面上的 "Sold by X"。这是真证据：页面自己
        #      说了谁在持位，不需要再猜。
        #   ② 不知道本店叫什么 → 退回「Buybox 展示价 == 我方当前价」推断，并把
        #      结论标成"未核实"（None），让后端继续保守处理。
        # 只用价格推断会出错：库里 SKU 9902388556722 的 Buybox 卖家是
        # ValuezyVAT Registered，价格和本店一样是 106 —— 同价不等于持位。
        # 也不能再用"页面无 Other sellers 区块/单卡 → 唯一卖家即我方"推断——
        # Takealot 改版后 Buybox 只渲染赢家卡，对手赢时同样只有一张卡。
        my_seller_name = (item.get("mySellerName") or "").strip()
        has_other_sellers = len(await page.query_selector_all("text=/other (sellers|offers)/i")) > 0
        price_matches = buybox_price is not None and (my_price is not None and abs(buybox_price - my_price) < 0.01)
        if my_seller_name and buybox_seller:
            seller_match = _norm_label(buybox_seller) == _norm_label(my_seller_name)
            if seller_match:
                # 名字一致但价格不一致：页面在说两件矛盾的事，不下结论。
                holding_verified = True if price_matches else None
                is_mine = True
            else:
                holding_verified = False
                is_mine = False
        else:
            holding_verified = None
            is_mine = price_matches
        result["holdingBuybox"] = is_mine
        result["holdingBuyboxVerified"] = holding_verified
        result["hasOtherSellers"] = has_other_sellers
        result["buyboxSeller"] = buybox_seller
        result["buyboxType"] = "buybox"
        buybox_leadtime = stable_round.get("buyboxLeadtime") if stable_round else None
        result["buyboxLeadtime"] = buybox_leadtime
        if buybox_price is not None:
            result["offers"].append({
                "seller": buybox_seller or "buybox",
                "price": buybox_price,
                "status": status,
                "offerType": "buybox",
                "isMine": is_mine,
                "deliveryLabel": stock_text,
                "leadtimeDays": buybox_leadtime,
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
                "leadtimeDays": fastest_info.get("leadtimeDays"),
            })
        if best_info is not None:
            result["offers"].append({
                "seller": buybox_seller or "best_price",
                "price": best_info["price"],
                "status": best_info["status"],
                "offerType": "best_price",
                "isMine": my_price is not None and abs(best_info["price"] - my_price) < 0.01,
                "deliveryLabel": best_info["deliveryLabel"],
                "leadtimeDays": best_info.get("leadtimeDays"),
            })

        # Other Sellers 竞品（页面存在多个卖家时抓取）
        for o in await extract_other_sellers(page, my_price):
            result["offers"].append(o)
        return result
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        return result
