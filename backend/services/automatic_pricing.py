"""PricePilot rule port + per-offer opt-in worker. No enabled rules by default."""
import asyncio
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from sqlalchemy.exc import IntegrityError
import httpx
from models import Product, SystemSettings, DEFAULT_STORE_ID
from services.takealot_seller_api import configured_key

PREFIX = '_pricing_rule:'
_stop = threading.Event()
_thread = None

# 降价必须和涨价一样有闸。涨价这边有三重限制（单次 3%、累计 20%、间隔 12h），
# 降价原来只有利润底价兜着——一次读到离谱报价就能把价格砍到地板，而底价是
# 「20% 毛利」，对一件 R 1999 的货就是砍到 R 800 也叫合规。
DEFAULT_MAX_DROP_PCT = 15
# 累计涨幅上限。原来这个 20 只在 _raise_ceiling() 里写一次、在决策理由里又硬编码
# 一次，两处各写各的；改上限时漏掉一处，用户看到的理由就和实际闸门对不上。
DEFAULT_RAISE_CEILING_PCT = 20
# 可售天数比空运周期（约 21 天）还短就算库存紧张。此时降价是纯亏：库存是固定的，
# 便宜卖只会让同样的货少赚钱，还会把断货提前。这是算术，不是保守。
DEFAULT_TIGHT_COVER_DAYS = 21
# 可售天数超过半年算滞销。这时压着资金才是最大成本，让利回笼现金比每单多赚重要。
DEFAULT_GLUT_COVER_DAYS = 180
# 真实报价不会离参考价差 4 倍。差这么多的只有两种：我们读错了（划线价、运费、
# 分期价），或者页面结构变了。两种都不该拿去调价。
OUTLIER_RATIO = 4.0


def _pressure(rule, stock):
    """库存压力，决定「该不该降价」和「该涨多快」。

    返回 (pressure, cover_days)。没有销量观测就返回 unknown —— 不知道能卖
    几天的时候，不该假装知道，行为保持原样。
    """
    velocity = rule.get('velocity')
    if isinstance(velocity, bool) or not isinstance(velocity, (int, float)) or velocity <= 0:
        return 'unknown', None
    if isinstance(stock, bool) or not isinstance(stock, (int, float)) or stock <= 0:
        return 'unknown', None
    cover = stock / velocity
    if cover <= rule.get('tight_cover_days', DEFAULT_TIGHT_COVER_DAYS):
        return 'tight', cover
    if cover >= rule.get('glut_cover_days', DEFAULT_GLUT_COVER_DAYS):
        return 'glut', cover
    return 'normal', cover


def _sane(price, reference):
    """这个报价能不能信。

    拿一个明显读错的数字去调价，一次就能把价格砍到地板上；而这一轮不跟，
    最坏只是少赚一次抢位。两边的代价差得太远，所以宁可不跟。
    """
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return False
    if isinstance(reference, bool) or not isinstance(reference, (int, float)) or reference <= 0:
        return True
    return reference / OUTLIER_RATIO <= price <= reference * OUTLIER_RATIO


def _raise_pct(rule, pressure):
    """单次涨幅百分比。库存紧张时走更快的档位。"""
    pct = rule.get('raise_step_pct', 3)
    if pressure == 'tight':
        pct = max(pct, rule.get('tight_step_pct', 5))
    return pct


def _raise_step(current, rule, pressure):
    """单次涨价幅度（元）。

    快卖完的时候，断货的成本（少卖的那些天）远高于让掉的几个点毛利，
    所以同样的守位场景应该一次多要一点，而不是照着 3% 慢慢挪。
    """
    return max(1, math.floor(current * _raise_pct(rule, pressure) / 100.0))

def decide(current, stock, rule, snapshot):
    """保留 (target, reason) 的旧契约，内部走 plan()。

    调价只有两种方向，用户的原话是：抢价格的时候必然是让利的，
    能自己持住价的时候就要尽可能多要。这里把这两种方向写成两套显式规则，
    而不是一堆 if 里靠顺序隐式决定——因为「现在是抢位还是守位」直接决定
    这一步是降价还是涨价，判错了就是真金白银。
    """
    result = plan(current, stock, rule, snapshot)
    return result['target'], result['reason']


def _pools(raw_offers, typed):
    """把竞品分成「能抢走 Buybox 的」和「抢不走的」。

    Takealot 的 Buybox 只给现货报价，预售/缺货/预订单抢不走它。
    旧实现把所有非本店报价一起取最小值，于是一个缺货的低价会把整条 SKU
    拖进「建议价低于最低价，放弃竞争」——明明还有一个打得过的竞品在。
    """
    foreign = [o for o in raw_offers if not o.get('isMine') and (not typed or o.get('offerType') != 'buybox')]
    live = [o for o in foreign if o.get('status') == 'in_stock'
            and isinstance(o.get('price'), (int, float)) and math.isfinite(o['price']) and o['price'] > 0]
    dark = [o for o in foreign if o.get('status') in ('pre_order', 'backorder', 'out_of_stock')]
    return foreign, live, dark


def _winnable(pool, rule):
    """这个卡位里，哪些价格我们跟得起（跟完之后仍不低于利润底价）。

    旧实现直接取整池最低价：池里只要有一个低于底价的报价，
    整条 SKU 就判「放弃竞争」——哪怕旁边还有一个完全打得过的竞品。
    等于被一个我们本来就抢不起的低价钉死在原地。
    """
    lead = rule.get('lead_amount', 1)
    floor = rule['min_price']
    return [o for o in pool if math.floor(o['price'] - lead) >= floor]


def _raise_ceiling(rule, current):
    """提价的天花板：人工上限，与「参考价 + 累计涨幅」取小。"""
    pct = rule.get('raise_ceiling_pct', DEFAULT_RAISE_CEILING_PCT)
    reference = rule.get('reference_price') or current
    return min(rule.get('max_price') or math.inf, math.floor(reference * (1 + pct / 100.0)))


def plan(current, stock, rule, snapshot):
    """一份可解释的调价决策：动不动、为什么、底在哪、顶在哪、有几分把握。

    返回结构直接进 rule['plan']，界面据此渲染「决策卡片」，
    不再需要用户从 last_result 的一句话里反推系统在想什么。
    """
    floor = rule.get('min_price')
    p = dict(regime='hold', target=None, reason='', confidence='low', risk='low',
             current=current, floor=floor, ceiling=rule.get('max_price'),
             cheapest=None, gap_pct=None, live_count=0, dark_count=0, sole_seller=False,
             pressure='unknown', cover_days=None, outliers=0)
    if stock <= 0:
        p.update(regime='off', reason='无库存，跳过')
        return p
    pressure, cover = _pressure(rule, stock)
    p.update(pressure=pressure, cover_days=round(cover, 1) if cover is not None else None)
    if snapshot.get('error'):
        p.update(regime='blind', reason='公开页采集失败，保持现价')
        return p
    raw_offers = snapshot.get('offers') or []
    typed = any(o.get('offerType') in ('fastest', 'best_price') for o in raw_offers)
    foreign, live, dark = _pools(raw_offers, typed)
    # 离谱报价先剔掉再决策：它不参与最低价计算，也不该把整条 SKU 拖进抢位。
    # 剔除数量如实透出，让人看得见「这轮少看了几个报价」，而不是悄悄当没有。
    reference = rule.get('reference_price') or current
    outliers = [o for o in live if not _sane(o['price'], reference)]
    live = [o for o in live if _sane(o['price'], reference)]
    p['outliers'] = len(outliers)
    buybox = snapshot.get('buyboxPrice')
    price_matches = (not isinstance(buybox, bool) and isinstance(buybox, (int, float))
                     and math.isfinite(buybox) and abs(current - buybox) < .01)
    # 持位证据三态（采集器按本店卖家名比对页面上的 "Sold by X" 得出）：
    #   True  → 页面说 Buybox 是本店的
    #   False → 页面说 Buybox 是别人的（哪怕价格和我们一样）
    #   None  → 还不知道本店卖家名，判断不了
    # 原来只有"价格相同"这一个信号，于是同价时既不敢涨价也不敢降价，
    # 永远停在「证据不足」。现在按卖家名判：同价 + 对手持位 = 应该去抢。
    verified = snapshot.get('holdingBuyboxVerified')
    holding = price_matches and verified is True
    cheapest = min((o['price'] for o in live), default=None)
    p.update(live_count=len(live), dark_count=len(dark), cheapest=cheapest,
             sole_seller=bool(raw_offers) and not foreign)
    if cheapest is not None and current:
        p['gap_pct'] = round((current - cheapest) / current * 100, 1)
    # 置信度只由证据强度决定：能证明「谁在持位」、且竞品报价带类型信息，才算高。
    if holding and len(live) >= 2 and typed:
        p['confidence'] = 'high'
    elif holding or (live and typed):
        p['confidence'] = 'medium'
    # 风险看的是「不跟价会怎样」，和抢不抢得到无关。
    if not holding and cheapest is not None and floor and cheapest < floor:
        p['risk'] = 'high'          # 现货最低价已在利润底价之下，这个位置抢不起
    elif p['gap_pct'] is not None:
        gap = p['gap_pct']
        p['risk'] = 'urgent' if gap >= 15 else 'high' if gap >= 8 else 'medium' if gap >= 3 else 'low'
    if price_matches and verified is None:
        # 只有「判断不了」才保持现价。这一条是最后的安全网：不知道本店叫什么，
        # 就无法区分「自己持位」和「对手同价持位」，两个方向都不能动。
        p.update(regime='blind', reason='与Buybox同价，但还无法确认本店卖家名（到设置里填一次即可自动核实）')
        return p
    # 走到这里：要么已确认本店持位（holding），要么已确认对手持位
    # （price_matches 但 verified 是 False）——后者要走下面的抢位分支。
    if holding and time.time() - rule.get('last_changed_epoch', 0) < rule.get('cooldown_minutes', 10) * 60:
        p.update(regime='climb' if live else 'scarcity', reason='持有 Buybox，处于冷却期')
        return p
    day = datetime.now(ZoneInfo('Africa/Johannesburg')).date().isoformat()
    if rule.get('change_day') == day and rule.get('today_changes', 0) >= rule['max_daily_changes']:
        p.update(reason='达到每日调价次数上限')
        return p
    if holding:
        ceiling = _raise_ceiling(rule, current)
        p['ceiling'] = ceiling
        if live:
            low = min(o['price'] for o in live)
            if low <= current:
                p.update(regime='hold', reason='现货竞品持平或更低，保持现价')
                return p
            # 守位时唯一正确的方向：贴着下一个现货竞品的价，把价格顶到
            # 「刚好还是最低」的位置。这不是猜测——对手的报价就是证据。
            target = min(math.floor(low - rule.get('lead_amount', 1)), ceiling)
            if rule.get('smart_raise'):
                target = min(target, current + _raise_step(current, rule, pressure))
            if target <= current:
                p.update(regime='climb', reason='已到守位上限，保持现价')
                return p
            p.update(regime='climb', target=target,
                     reason=f'持有 Buybox，现货次低价 R {int(low)}，提价到 R {int(target)} 仍最低')
            return p
        if not raw_offers:
            # 一条报价都没采到，和「我是独家」是两件完全不同的事。
            # 前者是证据缺失，后者是最大的定价权，判错方向就会乱涨价。
            p.update(regime='hold', reason='页面没有采到任何报价，无法判断竞争格局，保持现价')
            return p
        if outliers and not live:
            # 报价都在、但一个都不可信：这是证据出了问题，不是「没人竞争」。
            # 顺着这条往下走就会判成独家去涨价，方向正好反了。
            p.update(regime='hold',
                     reason=f'{len(outliers)} 个竞品报价偏离参考价 {OUTLIER_RATIO:g} 倍以上，已剔除；本轮证据不足，保持现价')
            return p
        # 走到这里说明有报价、但都不是现货：竞品全缺货，或只有本店在售。
        # 两种都是「此刻没人能抢走 Buybox」，提价有依据，但必须可回滚。
        now = time.time()
        trial = rule.get('scarcity_trial_started', 0)
        if (trial and now - trial >= 48 * 3600 and rule.get('sales_recent')
                and rule.get('sku_last_sale_epoch') is not None and rule['sku_last_sale_epoch'] <= trial):
            back = rule.get('scarcity_base_price', current)
            if back >= rule['min_price'] and back < current:
                p.update(regime='scarcity', target=back, trial='rollback',
                         reason='试涨48小时未见新成交，回退试涨前价格并暂停试涨24小时')
                return p
        interval_hours = rule.get('raise_interval_hours', 12)
        if pressure == 'tight':
            # 快没货了，观察期也砍一半：慢慢涨等于把剩下的库存留给别人。
            interval_hours = max(1, interval_hours / 2)
        interval = interval_hours * 3600
        if now < rule.get('scarcity_pause_until', 0) or now - rule.get('last_scarcity_raise', 0) < interval:
            p.update(regime='scarcity', reason='缺货试涨观察中，暂不重复涨价')
            return p
        step = _raise_step(current, rule, pressure)
        target = min(ceiling, max(current + 1, current + step))
        if target <= current or target < rule['min_price']:
            p.update(regime='scarcity', reason='已达智能试涨上限，保持现价')
            return p
        p.update(regime='scarcity', target=target, trial='raise',
                 reason=('本店是唯一在售，' if p['sole_seller'] else '确认竞品非现货，')
                        + f'智能试涨到 R {int(target)}（每次约{_raise_pct(rule, pressure):g}%，'
                        + f'累计不超过参考价{rule.get("raise_ceiling_pct", DEFAULT_RAISE_CEILING_PCT):g}%）')
        return p
    # 抢位：只降不升。降价就是让利，所以下限必须由利润底价来守。
    if pressure == 'tight':
        # 可售天数比空运周期还短：现在降价是纯亏。库存是固定的，便宜卖只会
        # 让同样的货少赚钱，还会把断货提前——降价的好处一天都兑现不了，
        # 代价却立刻发生。这条不是保守，是算术。
        p.update(regime='hold',
                 reason=f'可售约 {int(cover)} 天，库存紧张，不跟降（同样的货少卖钱，还会提前断货）')
        return p
    if rule['strategy'] == 'follow_lowest':
        able = _winnable(live, rule)
        if not able:
            below = cheapest is not None and floor is not None and cheapest < floor
            p.update(regime='compete',
                     reason=('现货最低价已低于利润底价，这个位置抢不起，保持现价' if below
                             else '没有现货竞品，保持现价'))
            return p
        raw = math.floor(min(o['price'] for o in able) - rule.get('lead_amount', 1))
        if raw >= current:
            p.update(regime='compete', reason='未持有 Buybox，只降不升')
            return p
    else:
        fastest = [o for o in live if o.get('offerType') == 'fastest']
        if not fastest:
            fastest = [o for o in live if o.get('offerType') == 'buybox']
        if not typed and not fastest:
            fastest = live
        best = [o for o in live if o.get('offerType') == 'best_price']
        raw = None
        for pool in (fastest, best):
            able = _winnable(pool, rule)
            if not able:
                continue
            candidate = math.floor(min(o['price'] for o in able) - rule.get('lead_amount', 1))
            if candidate < current:
                raw = candidate
                break
        if raw is None:
            below = cheapest is not None and floor is not None and cheapest < floor
            p.update(regime='compete',
                     reason=('现货最低价已低于利润底价，这个位置抢不起，保持现价' if below
                             else '两个现货卡位均不可达，保持现价'))
            return p
    if raw < rule['min_price']:
        p.update(regime='compete', reason='建议价低于最低价，放弃竞争')
        return p
    target = min(math.floor(raw), rule.get('max_price') or math.inf)
    if target >= current:
        p.update(regime='compete', reason='未持有时不跟涨')
        return p
    # 降价也要有闸。涨价这边有单次步长和累计上限，降价原来只有利润底价兜着，
    # 而底价是「20% 毛利」——对一件 R 1999 的货，砍到 R 800 也算合规。
    # 库存压力不能覆盖配置的单次降幅上限。
    drop_cap = max(0, min(100, rule.get('max_drop_pct', DEFAULT_MAX_DROP_PCT)))
    boundary = math.ceil(current * (1 - drop_cap / 100.0))
    if floor:
        boundary = max(boundary, math.ceil(floor))
    capped = target < boundary
    if capped:
        target = boundary
    if abs(target - current) < rule.get('min_change', 1):
        p.update(regime='compete', reason='变化小于调整阈值')
        return p
    p.update(regime='compete', target=target,
             reason=(f'未持有 Buybox，降到 R {int(target)} 抢位'
                     + (f'（一次最多降 {drop_cap:g}%，本轮只走这一步）' if capped else '')
                     + f'（让利 {int(current - target)}，底价 R {int(floor)}）'))
    return p

async def collect(product, offer, seller_name=None):
    from services.quote_browser import quote_context
    from services.pricepilot_collector import scrape_product
    url = product.takealot_url or ''
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.takealot.com', 'takealot.com') or not re.search(r'PLID\d+', parsed.path, re.I):
        return {'error': 'invalid_url', 'offers': []}
    async with quote_context() as context:
        page = None
        try:
            page = await context.new_page()
            from services.collection_diagnostics import safe_url, classify
            responses=[]
            def on_response(response):
                if response.request.resource_type=='document': responses.append(response.status)
            page.on('response', on_response)
            snapshot=await scrape_product(page, {'url': url, 'productId': product.id, 'sku': offer['sku'],
                'name': offer.get('title') or product.product_name, 'myPrice': offer['selling_price'],
                'myStock': offer.get('stock_at_takealot_total'), 'shopId': DEFAULT_STORE_ID,
                # 本店在 Takealot 的卖家名：采集器用它把页面上的 "Sold by X" 判成
                # 是不是本店，而不是靠价格猜。空串表示还不知道，退回价格推断。
                'mySellerName': seller_name or ''})
            diagnostics={'diag_version':3,'http_status':responses[-1] if responses else None,'requested_url':safe_url(url),'final_url':safe_url(page.url)}
            # 每一项诊断各自兜底。原来整段共用一个 try：只要 body.inner_text 超时一次，
            # 后面的元素计数就全丢——恰恰是页面最慢、最需要诊断的时候什么都没留下。
            async def _count(selector):
                try: return await page.locator(selector).count()
                except Exception: return None
            try: diagnostics['page_title']=(await page.title())[:160]
            except Exception: pass
            try:
                text=(await page.locator('body').inner_text(timeout=2000)).lower()
                markers=['verify you are human','checking your browser','automated traffic','unusual traffic','complete the captcha']
                diagnostics['block_reason']=next((m for m in markers if m in text),None)
            except Exception: pass
            # 只记数量，不存页面内容。两个计数合起来才能分清「商品没 Buybox」和
            # 「我们的锚点失效了」：有价格却没有 Buybox 区块 = 页面结构变了；
            # 两者都是 0 = 落地页根本不是商品页（验证页/跳转页）。
            diagnostics['buybox_cards']=await _count('[data-ref="buybox-offer"]')
            diagnostics['price_nodes']=await _count('[data-ref="price"]')
            # 只记长度和金额个数，不存卡片正文。用来区分三种「卡片在、价没有」：
            # 长度接近 0 = 骨架屏还没渲染；有文本但金额 0 个 = 卡片里确实没报价；
            # 金额 1 个 = 价在卡里，只是选择器没命中（新的文本兜底能救）。
            try:
                from services.pricepilot_collector import distinct_prices, STRUCK_SELECTORS
                card=page.locator('[data-ref="buybox-offer"]').first
                txt=await card.inner_text(timeout=1500)
                diagnostics['buybox_card_text_len']=len(txt)
                diagnostics['buybox_card_amounts']=len(distinct_prices(txt))
                # 摘掉划线价后还剩几个金额：1 = 文本兜底能救；2+ = 卡里真有两处
                # 独立报价（运费/满减），不采信才对。
                try: diagnostics['buybox_card_struck']=await card.locator(STRUCK_SELECTORS).count()
                except Exception: pass
                # 卡片内部有哪些 data-ref —— 只记属性名，不记正文也不记金额。
                # 页面改版时，这就是「价格锚点改成什么了」的直接答案。
                try:
                    refs=await card.evaluate(
                        "el=>[...new Set([...el.querySelectorAll('[data-ref]')].map(n=>n.getAttribute('data-ref')))].slice(0,20)")
                    diagnostics['buybox_card_refs']=refs if isinstance(refs,list) else []
                except Exception: pass
            except Exception: pass
            if isinstance(snapshot.get('variantControls'),int): diagnostics['variant_controls']=snapshot['variantControls']
            # 这一轮把哪些变体维度切成功了、哪个没切成功。多维度变体（颜色+尺码）
            # 少切一个就读不到报价，只记"变体控件有几个"看不出是缺哪个维度，
            # 所以把标签本身也记下来（是商品规格名，不是页面正文）。
            for key,dest in (('variantApplied','variant_applied'),('variantUnmatched','variant_unmatched')):
                value=snapshot.get(key)
                if isinstance(value,list):
                    diagnostics[dest]=[str(item)[:40] for item in value[:6] if isinstance(item,str)]
            # 变体控件的可读标签。只读、不点击：用来判断"确认不了是哪个变体"到底是
            # 页面上没有这个规格，还是控件本身没有文本（单选/下拉），后者属于我们
            # 匹配逻辑的缺口，不是商品的问题。
            if isinstance(snapshot.get('variantControls'),int) and snapshot['variantControls']>0:
                try:
                    from services.pricepilot_collector import main_variant_elements
                    els,_=await main_variant_elements(page)
                    sample=[]
                    for b in els[:10]:
                        try:
                            info=await b.evaluate(
                                "el=>({tag:el.tagName,text:(el.innerText||'').trim().slice(0,24),"
                                "aria:(el.getAttribute('aria-label')||'').slice(0,24),"
                                "sel:!!el.querySelector('input:checked')||el.getAttribute('aria-checked')==='true'})")
                            sample.append(info)
                        except Exception: continue
                    diagnostics['variant_sample']=sample
                except Exception: pass
            diagnostics['raw_error']=re.sub(r'https?://[^\s]+',lambda m:safe_url(m.group()),str(snapshot.get('error') or ''))[:200]
            snapshot['diagnostics']=diagnostics
            code,message=classify(snapshot)
            if code: snapshot.update(error=code,error_message=message)
            return snapshot
        finally:
            if page is not None: await page.close()

def official_offer(client, key, offer_id):
    response = client.get(f'https://seller-api.takealot.com/v2/offers/offer/ID{offer_id}', headers={'Authorization': f'Key {key}'})
    if response.status_code != 200: raise ValueError('卖家报价读取失败，暂停本轮调价')
    payload = response.json()
    data = payload.get('offer', payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict) or str(data.get('offer_id')) != str(offer_id): raise ValueError('卖家商品身份核对失败')
    for field in ['selling_price', 'stock_at_takealot_total']:
        if isinstance(data.get(field), bool) or not isinstance(data.get(field), (int, float)) or not math.isfinite(data[field]):
            raise ValueError('卖家现价或库存无效')
    if data['selling_price'] <= 0: raise ValueError('卖家现价无效')
    return data

def shared_snapshot(db, product_id, offer_id, sku, max_age=600):
    """Repricing consumes collection output without scheduling or launching collection."""
    row = db.query(SystemSettings).filter(SystemSettings.key == f'_pricing_monitor:{DEFAULT_STORE_ID}:{product_id}:{offer_id}').first()
    monitor = json.loads(row.value) if row else {}
    try:
        age = time.time() - datetime.fromisoformat(monitor['last_success_at']).timestamp()
    except (KeyError, ValueError, TypeError):
        age = math.inf
    if monitor.get('sku') != sku or monitor.get('status') == 'error' or not 0 <= age <= max_age:
        return {'error': '行情缺失、失败或已过期，等待Buybox采集', 'offers': []}
    snapshot = monitor.get('snapshot')
    if not isinstance(snapshot, dict) or snapshot.get('error'):
        return {'error': '等待Buybox完整行情采集', 'offers': []}
    # Recheck identity at consumption, including snapshots produced by older
    # collectors using an automatically inferred name or a since-edited name.
    from services.seller_identity import name_of, holds_buybox, normalize
    confirmed_name = name_of(db, DEFAULT_STORE_ID)
    value = dict(snapshot)
    value['holdingBuyboxVerified'] = holds_buybox(snapshot, confirmed_name)
    value['offers'] = [dict(offer, isMine=bool(confirmed_name and
                      normalize(offer.get('seller')) == normalize(confirmed_name)))
                       for offer in snapshot.get('offers', []) if isinstance(offer, dict)]
    return value

def process(row_id):
    from database import SessionLocal
    with SessionLocal() as db:
        row = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
        if not row: return
        rule = json.loads(row.value)
        if not rule.get('enabled') or rule.get('next_due', 0) > time.time(): return
        original = row.value
        rule['next_due'] = time.time() + 900
        if db.query(SystemSettings).filter(SystemSettings.key == row_id, SystemSettings.value == original).update({'value': json.dumps(rule)}) != 1:
            db.rollback(); return
        db.commit(); claim = json.dumps(rule)
        result = dict(rule); changed = False
        try:
            product = db.query(Product).filter(Product.id == rule['product_id'], Product.store_id == DEFAULT_STORE_ID, Product.is_archived == False).first()
            if not product: raise ValueError('商品已归档或不可用')
            if rule.get('use_system_min'):
                from services.replenishment_economics import system_floor
                floor = system_floor(product)
                if floor is None: raise ValueError('系统利润底价尚不可用，请补成本或设置该SKU最低价')
                rule['min_price'] = floor; result['min_price'] = floor
                if rule.get('max_price') and floor > rule['max_price']: raise ValueError('利润底价已高于人工上限，请调整区间')
            key = configured_key(db)
            if not key: raise ValueError('卖家接口未配置')
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                offer = official_offer(client, key, rule['offer_id'])
                if offer.get('sku') != rule['sku']: raise ValueError('卖家SKU变更，请重新绑定')
                if not rule.get('reference_price'):
                    rule['reference_price'] = offer['selling_price']; result['reference_price'] = offer['selling_price']
                profile_row = db.query(SystemSettings).filter(SystemSettings.key == f'operations_profile:{DEFAULT_STORE_ID}:{product.id}').first()
                profile = json.loads(profile_row.value) if profile_row else {}
                metrics = next((o for o in profile.get('offers', []) if o['id'] == rule['offer_id']), {})
                rule['sku_last_sale_epoch'] = metrics.get('last_sale_epoch')
                try: rule['sales_recent'] = time.time() - datetime.fromisoformat(profile['synced_at']).timestamp() < 7200
                except (KeyError, ValueError): rule['sales_recent'] = False
                # 日销与可售天数：调价判断「该不该降、该涨多快」全靠它。
                # 口径直接复用补货那边的 velocity_of()，不在这里另写一份——
                # 两边算出不同的日销，就会出现「补货说还有 40 天、调价说只剩 5 天」。
                from services.operations_planner import velocity_of
                rule['velocity'] = round(velocity_of(profile), 3)
                rule['cover_days'] = (round(offer['stock_at_takealot_total'] / rule['velocity'], 1)
                                      if rule['velocity'] > 0 else None)
                if offer['stock_at_takealot_total'] <= 0:
                    # 走同一个 plan()：库存为零在它第一行就返回 off。
                    # 这里手工拼一个 dict 的话，决策卡片会缺字段（pressure/
                    # cover_days/outliers），前端就得对两种情况分别兜底。
                    snapshot = {'offers': []}
                    decision = plan(offer['selling_price'], offer['stock_at_takealot_total'], rule, snapshot)
                else:
                    snapshot = shared_snapshot(db, product.id, rule['offer_id'], rule['sku'])
                    decision = plan(offer['selling_price'], offer['stock_at_takealot_total'], rule, snapshot)
                target, reason = decision.get('target'), decision.get('reason') or ''
                # 决策本身要落库：界面上的「决策卡片」读的就是它，而不是
                # 从 last_result 的一句话里反推系统当时在想什么。
                result['plan'] = {k: v for k, v in decision.items()
                                  if k in ('regime', 'target', 'reason', 'confidence', 'risk',
                                           'floor', 'ceiling', 'cheapest', 'gap_pct',
                                           'live_count', 'dark_count', 'sole_seller', 'current',
                                           'pressure', 'cover_days', 'outliers')}
                result.update(current_price=offer['selling_price'], stock=offer['stock_at_takealot_total'],
                    # 日销与可售天数一起落库：界面据此解释「为什么不跟降」，
                    # 也是下一轮判断的输入。
                    velocity=rule['velocity'], cover_days=rule['cover_days'],
                    buybox_price=snapshot.get('buyboxPrice'),
                    # 三态如实透传：True 本店持位 / False 对手持位 / None 待核实
                    holding_buybox=snapshot.get('holdingBuyboxVerified'),
                    holding_verified=snapshot.get('holdingBuyboxVerified') is True,
                    price_matches_buybox=snapshot.get('buyboxPrice')==offer['selling_price'],
                    competitor_price=min((o['price'] for o in snapshot.get('offers', []) if o.get('status') == 'in_stock' and not o.get('isMine') and isinstance(o.get('price'), (int, float))), default=None))
                if target is not None:
                    # A pause or edited range invalidates the claimed cycle before money-affecting writes.
                    db.expire_all(); live = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
                    if not live or live.value != claim: return
                    time.sleep(10)
                    fresh = official_offer(client, key, rule['offer_id'])
                    if fresh['selling_price'] != offer['selling_price'] or fresh['stock_at_takealot_total'] <= 0:
                        raise ValueError('采集期间现价或库存已变化，下一轮重新评估')
                    # Persist intent before PATCH; uncertainty pauses the rule, preventing duplicate retries.
                    result['enabled'] = False; result['write_pending'] = True; result['pending_target'] = target; result['pending_from'] = offer['selling_price']; result['last_result'] = '正在提交改价'
                    intent = json.dumps(result)
                    if db.query(SystemSettings).filter(SystemSettings.key == row_id, SystemSettings.value == claim).update({'value': intent}) != 1:
                        db.rollback(); return
                    db.commit()
                    if not db.query(SystemSettings).filter(SystemSettings.key == row_id, SystemSettings.value == intent).first(): return
                    time.sleep(10)
                    # Recheck after the delay; a pause in this window must invalidate the write.
                    db.expire_all()
                    if not db.query(SystemSettings).filter(SystemSettings.key == row_id, SystemSettings.value == intent).first(): return
                    response = client.patch(f'https://seller-api.takealot.com/v2/offers/offer/ID{rule["offer_id"]}',
                        headers={'Authorization': f'Key {key}'}, json={'selling_price': target})
                    if response.status_code not in (200, 202) or response.json().get('validation_errors'):
                        raise ValueError('平台改价未成功，已暂停，请核对店铺现价后再启动')
                    time.sleep(10)
                    actual = official_offer(client, key, rule['offer_id'])
                    if actual['selling_price'] != target: raise ValueError('平台改价尚未核验，已暂停，请核对店铺现价')
                    changed = True; result['current_price'] = target; result['write_pending'] = False
                    result['last_changed_epoch'] = time.time(); result['last_changed_at'] = datetime.now(timezone.utc).isoformat()
                    day = datetime.now(ZoneInfo('Africa/Johannesburg')).date().isoformat()
                    result['today_changes'] = rule.get('today_changes', 0) + 1 if rule.get('change_day') == day else 1
                    result['change_day'] = day; result['enabled'] = True
                    result['history'] = ([{'at': result['last_changed_at'], 'from': offer['selling_price'], 'to': target}] + rule.get('history', []))[:50]
                    if decision.get('trial') == 'raise':
                        result['last_scarcity_raise'] = time.time()
                        if not rule.get('scarcity_trial_started'):
                            result['scarcity_trial_started'] = time.time(); result['scarcity_base_price'] = offer['selling_price']
                    elif decision.get('trial') == 'rollback':
                        result['scarcity_trial_started'] = 0; result['scarcity_pause_until'] = time.time() + 86400
                    reason = f'已核验改价 R {offer["selling_price"]} → R {target}'
                result['last_result'] = reason
        except Exception as error:
            if result.get('write_pending'): result['enabled'] = False
            result['last_result'] = str(error) if isinstance(error, ValueError) else '本轮采集或接口不可用，未确认改价；查看后重试'
        finally:
            db.expire_all(); live = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
            # Preserve a concurrently requested pause and new configuration.
            if live:
                active = json.loads(live.value)
                if active.get('revision') == rule.get('revision'):
                    result['last_evaluated_at'] = datetime.now(timezone.utc).isoformat()
                    result['next_due'] = time.time() + 600
                    live.value = json.dumps(result, ensure_ascii=False); db.commit()

def start_worker():
    global _thread
    if _thread and _thread.is_alive(): return
    _stop.clear()
    def run():
        from database import SessionLocal
        while not _stop.is_set():
            try:
                with SessionLocal() as db:
                    ids = [r.key for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX + DEFAULT_STORE_ID + ':')).all() if json.loads(r.value).get('enabled')]
                for row_id in ids:
                    if _stop.is_set(): break
                    process(row_id)
            except Exception:
                print('Automatic pricing worker unavailable; will retry', flush=True)
            _stop.wait(30)
    _thread = threading.Thread(target=run, daemon=True, name='automatic-pricing'); _thread.start()

def stop_worker(): _stop.set()
