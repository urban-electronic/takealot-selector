"""Seller-owned offers only. Fixed GET endpoint; no write operations or secret logging."""
import base64
import hashlib
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

SECRET_SETTING = '_takealot_seller_api_secret'
_lock = threading.Lock()
_cache = {}
OFFERS_LOCK_WAIT_SECONDS = 20.0


@contextmanager
def _offers_read_lock():
    # 只有拿到本次占用才允许释放；忙时不碰缓存、不发额外请求。
    if not _lock.acquire(timeout=OFFERS_LOCK_WAIT_SECONDS):
        raise ValueError('卖家资料读取正忙，请稍后重试')
    try:
        yield
    finally:
        _lock.release()


def _cipher():
    master = os.environ.get('API_KEY', '').strip()
    if len(master) < 24:
        raise ValueError('服务端访问密钥不足以保护卖家密钥')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('takealot-seller-secret-v1:' + master).encode()).digest()))


def encrypt_key(key):
    if not re.fullmatch(r'[a-fA-F0-9]{128}', key):
        raise ValueError('请提供完整的 Takealot Seller API 密钥')
    return _cipher().encrypt(key.encode()).decode()


def configured_key(db):
    from models import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == SECRET_SETTING).first()
    if not row:
        return ''
    try:
        return _cipher().decrypt(row.value.encode()).decode()
    except (InvalidToken, ValueError):
        return ''


def seller_api_reason(status):
    """把卖家接口的失败归到一个用户能行动的原因上（DIAG-OFFER-01）。

    401/403 是密钥，429 是限流，5xx 是上游故障——原来三者都是一句
    「卖家报价读取失败」，用户只能反复点重试，也不知道该去改密钥还是等。
    这里只带状态码和类别：不透出响应正文（正文里可能有账号信息）。
    """
    if status in (401, 403): return f'HTTP {status}，卖家接口密钥无效或无权限'
    if status == 429: return 'HTTP 429，上游限流，稍后自动重试'
    if 500 <= status < 600: return f'HTTP {status}，上游服务异常'
    return f'HTTP {status}'


def _unavailable_reason(status):
    """`_load_offers` 失败与冷却期共用的一句文案（SELLER-REASON-01）。

    401/403 是密钥问题，收尾说「请稍后再试」等于劝用户白等——密钥坏了等多久
    都不会好；429/5xx 是上游问题，收尾说「检查密钥」又让人去改一个没坏的配置。
    所以状态码归因之后，下一步动作必须按类别分开写。
    """
    reason = seller_api_reason(status)
    if status in (401, 403):
        return f'卖家接口暂时不可用（{reason}），已暂停请求，请先检查卖家 API 密钥'
    return f'卖家接口暂时不可用（{reason}），已暂停请求，请稍后再试'


def _load_offers(key):
    """At most five pages per refresh; serialized and cached for 30 minutes."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    with _offers_read_lock():
        state = _cache.setdefault(digest, {'rows': [], 'expires': 0, 'retry': 0})
        now = time.monotonic()
        if now < state['expires']:
            return state['rows']
        if now < state['retry']:
            # 冷却期内要沿用上一次的真实原因：否则 401（密钥坏了）在 5 分钟冷却里
            # 又被改写成「暂时不可用，请稍后再试」，用户永远看不到该改密钥。
            raise ValueError(state.get('last_error') or '卖家接口暂时不可用，已暂停请求，请稍后再试')
        # Failure cooldown starts before network access: no automatic repeated retries.
        state['retry'] = now + 300
        rows = []
        token = None
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            for page in range(5):
                if page:
                    time.sleep(10)
                params = [('limit', '1000')] if token is None else [('continuation_token', token)]
                if token is None:
                    params += [('fields', field) for field in ('title', 'image_url', 'sku', 'tsin_id', 'productline_id', 'selling_price', 'status', 'length_cm', 'width_cm', 'height_cm', 'weight_grams')]
                response = client.get('https://marketplace-api.takealot.com/v1/offers', params=params, headers={'X-API-Key': key})
                if response.status_code != 200:
                    if response.status_code == 429:
                        state['retry'] = time.monotonic() + 1800
                    state['last_error'] = _unavailable_reason(response.status_code)
                    raise ValueError(state['last_error'])
                payload = response.json()
                if not isinstance(payload.get('items'), list):
                    raise ValueError('卖家接口资料格式异常')
                rows.extend(payload['items'])
                token = payload.get('continuation_token')
                if not token:
                    break
            if token:
                raise ValueError('卖家商品数量超出本次读取上限，请联系管理员')
        state.update(rows=rows, expires=time.monotonic() + 1800, retry=0)
        state.pop('last_error', None)   # 成功一次就清掉，别让旧原因留在下次冷却里
        return rows


def seller_product(url, key):
    if not key:
        return None
    parsed = urlparse(url)
    match = re.search(r'PLID(\d+)', parsed.path, re.I)
    if parsed.scheme != 'https' or parsed.hostname not in ('takealot.com', 'www.takealot.com') or parsed.username or parsed.password or not match:
        return None
    rows = [r for r in _load_offers(key) if str(r.get('productline_id')) == match[1]]
    if not rows:
        return None
    first = next((r for r in rows if r.get('title')), None)
    if not first:
        return None
    prices = []
    for row in rows:
        p = row.get('selling_price')
        if isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and p > 0:
            prices.append(float(p))
    image = next((r.get('image_url') for r in rows if r.get('image_url')), None)
    if image:
        img = urlparse(image)
        if img.hostname in ('takealot.s3.amazonaws.com', 'media.takealot.com') and img.scheme in ('http', 'https') and img.path.startswith('/covers_images/'):
            image = image.replace('http://', 'https://', 1)
        else:
            image = None
    warnings = ['来自本店卖家 API；售价为本店报价，请核对，非官网实时最低价。']
    if not image:
        warnings.append('卖家接口未提供商品图片，请补充。')
    if not prices:
        warnings.append('卖家接口未提供有效售价，请补充。')
    if len(set(prices)) > 1:
        warnings.append('不同规格报价不同，展示最低规格报价，请核对所选规格。')
    if not any(r.get('status') == 'buyable' for r in rows):
        warnings.append('本店该商品当前不可购买；报价不代表当前可成交价格。')
    return {'normalized_url': parsed.scheme + '://' + parsed.netloc + parsed.path,
            'tsin': str(first.get('tsin_id') or '') or None, 'product_name': first['title'],
            'product_image_url': image, 'actual_sale_price_zar': min(prices) if prices else None,
            'in_stock_price': None, 'takealot_category_path': None,
            'variants': [{'sku': r.get('sku'), 'label': r.get('title'), 'image_url': image or ''} for r in rows if r.get('sku')],
            'warnings': warnings, 'success': bool(image and prices), 'data_source': 'seller_api',
            'diagnostics': [{'stage': 'seller_api', 'reason': 'exact_productline_match'}]}


ASSIST_FIELDS = {'sku': 'SKU', 'product_name': '英文标题', 'product_image_url': '图片', 'tsin': 'TSIN',
                 'length_mm': '长度(mm)', 'width_mm': '宽度(mm)', 'height_mm': '高度(mm)', 'actual_weight_kg': '重量(kg)'}


def plid_from_url(url):
    match = re.search(r'PLID(\d+)', url or '', re.I)
    return match.group(1) if match else ''


def offer_plan(product, offers, plid=None):
    """Exact identity only; never infer seller identity from a title.

    归属一律靠 PLID 判定（比 SKU 可靠：SKU 会被运营改名）。SKU 只用来判断
    「本地记录的是哪几个规格」，以及决定要不要按卖家后台更正。

    `plid` 由调用方传入时优先用它：链接为空但库里已经存了 PLID 的商品
    （卖家后台导入的行就是这样）不能因为抠不到链接就判成「无匹配」。
    """
    skus = re.split(r'[\s,，;；]+', (product.sku or '').strip()) if product.sku else []
    plid = plid or plid_from_url(getattr(product, 'takealot_url', '')) or (getattr(product, 'plid', '') or '')
    plid_rows = [r for r in offers if str(r.get('productline_id')) == str(plid)] if plid else []
    candidates = plid_rows
    if skus:
        exact = [r for r in offers if str(r.get('sku')) in skus]
        # All existing SKUs must map; decorated historical SKUs require manual review.
        if set(str(r.get('sku')) for r in exact) == set(skus):
            if candidates and any(r not in candidates for r in exact):
                candidates = []
            else:
                candidates = exact
        else:
            candidates = []
    proposed = {}; warnings = []
    official_skus = sorted({str(r.get('sku') or '').strip() for r in plid_rows if r.get('sku')})
    replace_sku = None
    if skus and not candidates and official_skus:
        # 本地 SKU 与卖家后台对不上。以卖家后台为准更正，但只在能确定一一对应时做：
        # 单规格（本地 1 个、该 PLID 下也只有 1 个）映射唯一；多规格没有可靠对应
        # 关系，猜错会把库存和发运挂到别的规格上，宁可交给人工。
        if len(skus) == 1 and len(official_skus) == 1:
            replace_sku = (skus[0], official_skus[0])
            candidates = plid_rows
        else:
            warnings.append('本地 SKU 与卖家后台不一致（本地：%s；卖家后台：%s），'
                            '多规格无法自动对应，请人工核对。'
                            % ('、'.join(skus), '、'.join(official_skus)))
    if candidates:
        if not skus:
            proposed['sku'] = '\n'.join(sorted(set(str(r['sku']) for r in candidates if r.get('sku'))))
        elif replace_sku:
            proposed['sku'] = replace_sku[1]
        for field, source in [('product_name', 'title'), ('tsin', 'tsin_id'), ('product_image_url', 'image_url')]:
            vals = {str(r[source]) for r in candidates if r.get(source)}
            if len(vals) == 1:
                value = vals.pop()
                if field == 'product_image_url':
                    parsed = urlparse(value)
                    if parsed.hostname not in ('takealot.s3.amazonaws.com', 'media.takealot.com') or parsed.scheme not in ('https', 'http') or not parsed.path.startswith('/covers_images/'):
                        continue
                    value = value.replace('http://', 'https://', 1)
                proposed[field] = value
        for field, source, factor in [('length_mm', 'length_cm', 10), ('width_mm', 'width_cm', 10), ('height_mm', 'height_cm', 10), ('actual_weight_kg', 'weight_grams', .001)]:
            vals = [r.get(source) for r in candidates]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in vals) and len(set(vals)) == 1:
                proposed[field] = round(vals[0] * factor, 6)
        if len(candidates) > 1:
            warnings.append('多规格商品仅补齐所有匹配规格一致的资料；尺寸有差异时请人工核对。')
    else:
        if not warnings:
            warnings.append('本店卖家资料无精确匹配，请人工补充；不会猜测 SKU。')
    changes = []; differences = []
    for field, value in proposed.items():
        current = getattr(product, field)
        empty = current is None or current == ''
        if field == 'product_image_url' and product.product_image_path:
            continue
        if value and empty:
            changes.append({'field': field, 'label': ASSIST_FIELDS[field], 'value': value})
        elif current != value:
            if field == 'sku' and replace_sku:
                # 这是替换而不是补齐，必须把旧值一起交给界面确认。
                changes.append({'field': field, 'label': ASSIST_FIELDS[field],
                                'value': value, 'current': current})
            else:
                differences.append(ASSIST_FIELDS[field] + '与卖家资料不同，保留现有值')
    manual = [label for field, label in [('purchase_cost_cny', '采购成本'), ('fee_category', 'Fee品类'), ('shipping_method', '运输方式')] if not getattr(product, field)]
    fingerprint = hashlib.sha256(repr((product.id, product.store_id, product.updated_at, changes)).encode()).hexdigest()
    return {'id': product.id, 'product_no': product.product_no, 'name': product.product_name or '未命名',
            'changes': changes, 'differences': differences, 'warnings': warnings, 'manual': manual, 'token': fingerprint}
