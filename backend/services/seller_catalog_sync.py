"""把卖家后台的商品同步进正式产品主档。

用户要的是「就跟在搜索框里搜出来的一样」：同步之后，商品就是普通产品行，
不需要另一套核对卡片、也不会有第二个产品库。所以这里只做两件事——

1. 本地已有这条商品：按卖家后台更正 SKU、补齐空着的标题/图片/尺寸/TSIN；
2. 本地确实没有：新建一条正式产品记录。

三条底线（都是「宁可不动，也不猜」）：

- **身份只认 PLID**。标题相似度、卖家返回的行序、SKU 前后缀都不参与身份判定。
  SKU 只用来判断「这条本地记录记的是哪几个规格」。
- **多规格对不上就跳过**。SKU 是库存主键、发运行和装箱资料的关联键，猜错会把库存
  挂到别的规格上；只有单规格 1↔1 才敢自动更正，其余交给人工。
- **新建只写卖家资料里确实有的字段**。采购成本、库存、利润、费率这些系统里没有
  依据，一律留空——宁可显示「资料不全」，也不能编一个数字进去。

本模块只负责算计划，不提交事务；调用方拿到 `apply_plan` 的结果后统一 commit。
"""

import hashlib
import math
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from models import Product
from services.takealot_seller_api import offer_plan, plid_from_url

# 卖家资料里能直接落进主档的字段（都是「平台确实这么写的」，不是推算值）。
SELLER_IMAGE_HOSTS = ('takealot.s3.amazonaws.com', 'media.takealot.com')

# Takealot 链接的 slug 段在真实数据里是 64 字符截断的（对着库里已有链接量过：
# 2-5-500gb-...-hdd-sto / 4k-thump-...-sky 都是 64）。标题改过之后 slug 也不会
# 跟着变（库里有标题带 black、链接仍是不带 black 的旧 slug），说明平台按 PLID
# 解析、slug 只影响观感。所以这里按同一规则生成，链接挂了也不影响身份。
SLUG_MAX = 64


def slugify(title: str) -> str:
    text = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    if len(text) > SLUG_MAX:
        text = text[:SLUG_MAX].rstrip('-')
    return text


def product_url(plid: str, title: str) -> str:
    return 'https://www.takealot.com/%s/PLID%s' % (slugify(title) or 'product', plid)


def _seller_image(rows: List[Dict[str, Any]]) -> str:
    """只有一个候选图时才用，且必须是 Takealot 自己的图片域名。"""
    urls = {str(row.get('image_url') or '').strip() for row in rows if row.get('image_url')}
    if len(urls) != 1:
        return ''
    value = urls.pop()
    parsed = urlparse(value)
    if parsed.hostname not in SELLER_IMAGE_HOSTS or parsed.scheme not in ('https', 'http'):
        return ''
    if not parsed.path.startswith('/covers_images/'):
        return ''
    return value.replace('http://', 'https://', 1)


def _single_number(rows: List[Dict[str, Any]], source: str, factor: float = 1.0) -> Optional[float]:
    """所有规格都给出同一个正数时才采用；有一个缺失或不同就整项放弃。"""
    values = [row.get(source) for row in rows]
    if not values:
        return None
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return None
    if len(set(values)) != 1:
        return None
    return round(values[0] * factor, 6)


def group_offers(offers: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """按 PLID 归组。没有 PLID 的行没法定位身份，单独记成跳过原因。"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    orphans: List[Dict[str, Any]] = []
    for row in offers:
        plid = str(row.get('productline_id') or '').strip()
        if plid:
            groups.setdefault(plid, []).append(row)
        else:
            orphans.append(row)
    return groups, orphans


def _index(products: List[Product]) -> Tuple[Dict[str, List[Product]], Dict[str, List[Product]], Dict[str, List[Product]]]:
    """本地身份索引：PLID / TSIN / SKU 三份。

    链接和独立列都进入索引，以便发现两者冲突；TSIN、SKU 仅在没有冲突时
    补全身份。命中不唯一或已有身份相矛盾，一律跳过。
    """
    by_plid: Dict[str, List[Product]] = {}
    by_tsin: Dict[str, List[Product]] = {}
    by_sku: Dict[str, List[Product]] = {}
    for product in products:
        anchors = {plid_from_url(product.takealot_url),
                   (getattr(product, 'plid', '') or '').strip()} - {''}
        for plid in anchors:
            by_plid.setdefault(plid, []).append(product)
        for token in _tokens(product.tsin):
            by_tsin.setdefault(token, []).append(product)
        for token in _tokens(product.sku):
            by_sku.setdefault(token, []).append(product)
    return by_plid, by_tsin, by_sku


def _tokens(value: Optional[str]) -> List[str]:
    return [token for token in re.split(r'[\s,，;；]+', value or '') if token]


def _unique(hits: List[Product]) -> Optional[Product]:
    seen = {product.id: product for product in hits}
    return next(iter(seen.values())) if len(seen) == 1 else None


def _locate(plid: str, rows: List[Dict[str, Any]], indexes) -> Tuple[Optional[Product], str]:
    """找到这条卖家商品对应的本地记录，返回 (商品, 依据)。找不到或撞车都回 None。"""
    by_plid, by_tsin, by_sku = indexes
    hit = _unique(by_plid.get(plid, []))
    if hit:
        return hit, 'plid'
    for token in sorted({str(row.get('tsin_id') or '').strip() for row in rows} - {''}):
        hit = _unique(by_tsin.get(token, []))
        if hit:
            return hit, 'tsin'
    for token in sorted({str(row.get('sku') or '').strip() for row in rows} - {''}):
        hit = _unique(by_sku.get(token, []))
        if hit:
            return hit, 'sku'
    return None, ''


def _identity_hits(plid: str, rows: List[Dict[str, Any]], indexes) -> Dict[str, Product]:
    by_plid, by_tsin, by_sku = indexes
    hits = list(by_plid.get(plid, []))
    for field, index in (('tsin_id', by_tsin), ('sku', by_sku)):
        for token in {str(row.get(field) or '').strip() for row in rows} - {''}:
            hits.extend(index.get(token, []))
    return {product.id: product for product in hits}


def _ambiguous(plid: str, rows: List[Dict[str, Any]], indexes) -> bool:
    """所有标识必须指向同一产品，已有 PLID 不允许被弱匹配改绑。"""
    hits = _identity_hits(plid, rows, indexes)
    if len(hits) > 1:
        return True
    for product in hits.values():
        anchors = {plid_from_url(product.takealot_url),
                   (getattr(product, 'plid', '') or '').strip()} - {''}
        if anchors - {plid}:
            return True
    return False


def _create_payload(plid: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    skus = sorted({str(row.get('sku') or '').strip() for row in rows} - {''})
    tsins = sorted({str(row.get('tsin_id') or '').strip() for row in rows} - {''})
    title = next((str(row.get('title') or '').strip() for row in rows if row.get('title')), '')
    return {
        'plid': plid,
        'sku': '\n'.join(skus),
        'tsin': '\n'.join(tsins),
        'product_name': title,
        'product_image_url': _seller_image(rows),
        'takealot_url': product_url(plid, title),
        # 卖家后台自己标的售价，是本店售价不是竞品价，可以用来算利润；
        # 多规格价格不一致时留空，免得把一个规格的价按到整条商品上。
        'actual_sale_price_zar': _single_number(rows, 'selling_price'),
        'length_mm': _single_number(rows, 'length_cm', 10),
        'width_mm': _single_number(rows, 'width_cm', 10),
        'height_mm': _single_number(rows, 'height_cm', 10),
        'actual_weight_kg': _single_number(rows, 'weight_grams', 0.001),
    }


def build_plan(db, store_id: str, offers: List[Dict[str, Any]],
               plids: Optional[List[str]] = None) -> Dict[str, Any]:
    """算出「要更正什么、要新增什么、哪些跳过」，不写库。

    `plids` 用来收窄范围。整库同步会一次新增一百多个商品，而用户常常只关心其中
    几条（例如刚在卖家后台改过 SKU 的那几个）。收窄后所有计数都按收窄后的口径
    算——界面上写「将新增 4 个」就必须真的只写这 4 个，不能拿整库数字吓人。
    """
    wanted = {str(plid).strip() for plid in (plids or []) if str(plid).strip()}
    if wanted:
        # 没有 PLID 的孤儿行本来也不参与同步，收窄时一并排除，不要混进跳过数。
        offers = [row for row in offers
                  if str(row.get('productline_id') or '').strip() in wanted]
    groups, orphans = group_offers(offers)
    products = db.query(Product).filter(Product.store_id == store_id).all()
    active = [product for product in products if not product.is_archived]
    archived = [product for product in products if product.is_archived]
    indexes = _index(active)
    archived_plids = {plid_from_url(p.takealot_url) or (getattr(p, 'plid', '') or '').strip()
                      for p in archived} - {''}

    updates: List[Dict[str, Any]] = []
    creates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    unchanged = 0
    # 先检查整批归属，不能先放行第一条、只拒绝后来的冲突行。
    claims: Dict[str, set] = {}
    for candidate_plid, candidate_rows in groups.items():
        for product_id in _identity_hits(candidate_plid, candidate_rows, indexes):
            claims.setdefault(product_id, set()).add(candidate_plid)

    for plid, rows in groups.items():
        skus = sorted({str(row.get('sku') or '').strip() for row in rows} - {''})
        if _ambiguous(plid, rows, indexes):
            skipped.append({'plid': plid, 'skus': skus,
                            'reason': '本地有多条记录命中同一个 PLID/TSIN/SKU，身份不明，未自动处理'})
            continue
        product, basis = _locate(plid, rows, indexes)
        if product is None:
            if plid in archived_plids:
                skipped.append({'plid': plid, 'skus': skus,
                                'reason': '本地有同 PLID 的归档商品，先确认是否恢复，不重复新建'})
                continue
            payload = _create_payload(plid, rows)
            if not payload['product_name']:
                # 没标题的新行在列表里就是「未命名」，还会被当成新选品；等卖家
                # 资料补齐标题再导入。
                skipped.append({'plid': plid, 'skus': skus,
                                'reason': '卖家资料里这条没有标题，先不新建，等资料补齐再同步'})
                continue
            creates.append(payload)
            continue
        if len(claims.get(product.id, set())) > 1:
            skipped.append({'plid': plid, 'skus': skus,
                            'reason': '同一件本地商品被多个 PLID 同时命中，先人工确认归属'})
            continue
        plan = offer_plan(product, offers, plid=plid)
        changes = list(plan['changes'])
        # 身份补全：本地这条记录是靠 TSIN/SKU 找到的，说明它自己没有 PLID。
        # 补上是纯收益——以后同步不必再靠弱线索匹配，也不会被重复导入。
        if basis != 'plid' or not (getattr(product, 'plid', '') or '').strip():
            changes.append({'field': 'plid', 'label': 'PLID', 'value': plid})
        if not (product.takealot_url or '').strip():
            title = next((str(row.get('title') or '').strip() for row in rows if row.get('title')), '')
            changes.append({'field': 'takealot_url', 'label': '商品链接', 'value': product_url(plid, title)})
        if not changes:
            if plan['warnings']:
                # 典型是多规格：卖家后台改了 SKU，本地还是旧值，但对应关系不唯一。
                # 放进「跳过」而不是「无变化」，否则用户会以为已经同步好了。
                skipped.append({'plid': plid, 'skus': skus, 'reason': plan['warnings'][0]})
            else:
                unchanged += 1
            continue
        updates.append({
            'plid': plid,
            'product_id': product.id,
            'expected_updated_at': product.updated_at.isoformat(),
            'product_no': product.product_no,
            'name': product.product_name or plan['name'],
            'matched_by': basis,
            'changes': changes,
            'warnings': plan['warnings'],
        })

    stats = {
        'seller_offer_count': len(offers),
        'seller_group_count': len(groups),
        'scope_count': len(wanted),
        'local_product_count': len(active),
        'update_count': len(updates),
        'create_count': len(creates),
        'unchanged_count': unchanged,
        'skip_count': len(skipped) + len(orphans),
        'rename_count': sum(1 for item in updates
                            for change in item['changes']
                            if change['field'] == 'sku' and change.get('current')),
    }
    plan = {
        'stats': stats,
        'updates': updates,
        'creates': creates,
        'skipped': skipped,
        'orphans': len(orphans),
        'scope': sorted(wanted),
    }
    plan['token'] = _token(plan)
    return plan


def _token(plan: Dict[str, Any]) -> str:
    """预览与写入之间任何输入变了，token 就对不上，apply 必须重新预览。

    `current`（被更正的旧 SKU）也要进签名：只签「改成什么」的话，别人把旧值
    又改了一轮，计划看着一样，却会拿着过期的旧值去迁下游。
    """
    signature = repr((
        plan['stats'],
        # 范围也进签名：只同步 A 的预览不能拿去执行「整库」或「A+B」，否则用户以为
        # 只动了那几条，实际多写了一百多条。
        plan.get('scope') or [],
        [(item['plid'], item['product_id'], item['expected_updated_at'],
          [(c['field'], c['value'], c.get('current')) for c in item['changes']])
         for item in plan['updates']],
        [sorted(item.items()) for item in plan['creates']],
        [item['plid'] for item in plan['skipped']],
    ))
    return hashlib.sha256(signature.encode()).hexdigest()


class SellerSyncConflict(Exception):
    """写入过程中发现身份冲突：整批回滚，不留下半条记录。"""


def apply_plan(db, store_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """按计划写库。调用方负责 commit；这里只在出错时抛异常，由调用方 rollback。"""
    from datetime import datetime

    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError
    from models import Store

    # Hold the database write reservation until the caller commits or rolls back.
    # An in-process mutex cannot protect separate workers/connections.
    try:
        locked = db.execute(update(Store).where(Store.id == store_id).values(id=Store.id))
    except OperationalError as error:
        if 'locked' in str(error).lower() or 'busy' in str(error).lower():
            raise SellerSyncConflict('其他操作正在写入，请稍后重新预览') from error
        raise
    if locked.rowcount != 1:
        raise SellerSyncConflict('店铺已变化，请重新预览')

    from api.product_routes import _apply_calculated_fields
    from services.sku_migration import SkuMigrationConflict, migrate_sku

    created = 0
    updated = 0
    renamed = 0
    moved: Dict[str, int] = {}

    for item in plan['updates']:
        product = db.query(Product).filter(Product.id == item['product_id'],
                                           Product.store_id == store_id,
                                           Product.is_archived == False).first()
        if product is None:
            raise SellerSyncConflict('产品已删除或归档，请重新同步')
        values = {change['field']: change['value'] for change in item['changes']}
        values['updated_at'] = datetime.utcnow()
        count = db.query(Product).filter(Product.id == product.id,
                                         Product.store_id == store_id,
                                         Product.updated_at == datetime.fromisoformat(item['expected_updated_at'])).update(values, synchronize_session=False)
        if count != 1:
            raise SellerSyncConflict('同事刚修改了资料，请重新同步')
        db.refresh(product)
        rename = next((change for change in item['changes']
                       if change['field'] == 'sku' and change.get('current')), None)
        if rename:
            try:
                migration = migrate_sku(db, product, [str(rename['current'])], [str(values['sku'])], store_id)
            except SkuMigrationConflict as error:
                raise SellerSyncConflict(str(error))
            for table, rows in migration['moved'].items():
                moved[table] = moved.get(table, 0) + rows
            renamed += 1
        if any(field in values for field in ('length_mm', 'width_mm', 'height_mm', 'actual_weight_kg')):
            _apply_calculated_fields(db, product)
        updated += 1

    # 新建放在最后：上面任何一步冲突都会先抛出去，整批回滚，不会留下半批新商品。
    max_no = db.query(Product.product_no).filter(Product.store_id == store_id).order_by(Product.product_no.desc()).first()
    next_no = (max_no[0] + 1) if max_no and max_no[0] else 1
    for item in plan['creates']:
        # 幂等：同一批里重复、或上一次已经建过（token 过期重试）都不再建第二条。
        exists = db.query(Product).filter(Product.store_id == store_id, Product.plid == item['plid']).first()
        if exists:
            raise SellerSyncConflict('PLID %s 已存在，请重新同步' % item['plid'])
        product = Product(store_id=store_id, product_no=next_no, **item)
        next_no += 1
        product.recorded_at = datetime.utcnow()
        db.add(product)
        _apply_calculated_fields(db, product)
        created += 1

    return {'created': created, 'updated': updated, 'renamed': renamed, 'moved': moved}
