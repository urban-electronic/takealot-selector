"""Seller-owned offers only. Fixed GET endpoint; no write operations or secret logging."""
import base64
import hashlib
import math
import os
import re
import threading
import time
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

SECRET_SETTING = '_takealot_seller_api_secret'
_lock = threading.Lock()
_cache = {}


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


def _load_offers(key):
    """At most five pages per refresh; serialized and cached for 30 minutes."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    with _lock:
        state = _cache.setdefault(digest, {'rows': [], 'expires': 0, 'retry': 0})
        now = time.monotonic()
        if now < state['expires']:
            return state['rows']
        if now < state['retry']:
            raise ValueError('卖家接口暂时不可用，已暂停请求，请稍后再试')
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
                    params += [('fields', field) for field in ('title', 'image_url', 'sku', 'tsin_id', 'productline_id', 'selling_price', 'status')]
                response = client.get('https://marketplace-api.takealot.com/v1/offers', params=params, headers={'X-API-Key': key})
                if response.status_code != 200:
                    if response.status_code == 429:
                        state['retry'] = time.monotonic() + 1800
                    raise ValueError('卖家接口暂时不可用，已暂停请求，请稍后再试')
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
