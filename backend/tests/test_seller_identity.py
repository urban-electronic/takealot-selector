"""本店卖家名：Buybox 归属的真实证据。

背景（2026-10-18 线上）：作业队列里一堆 SKU 显示「证据不足」，调价完全不动。
根因是 `holding` 只能靠「Buybox 价 == 本店价」推断，而 `holdingBuyboxVerified`
从来没有被真正赋值过——只要同价就永远判不出持位。

同价确实不能证明持位，库里就有反例：SKU 9902388556722 的 Buybox 卖家是
ValuezyVAT Registered，价格和本店一样是 106。所以不能简单把这道闸拆掉，
而是要把页面上的 "Sold by X" 用起来——缺的只是「本店叫什么」。

这些用例钉住三件事：
  1. 名字能从采集里自动学出来（按不同 SKU 数投票），手填的不会被覆盖；
  2. 名字对上 = 本店持位，可以去守位提价；
  3. 名字对不上 = 对手持位，哪怕同价也要去抢位，而不是显示「证据不足」。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import SystemSettings, DEFAULT_STORE_ID
from services import automatic_pricing as pricing
from services import seller_identity
from services.price_ownership import estimate

RULE = dict(product_id='p', offer_id='1', sku='a', enabled=True, min_price=80, max_price=400,
            strategy='takealot_buybox', lead_amount=1, max_daily_changes=10,
            cooldown_minutes=10, revision=1, smart_raise=True, reference_price=197)


def session():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def offer(price, kind='buybox', **extra):
    return {'price': price, 'offerType': kind, 'status': 'in_stock', 'isMine': False, **extra}


def snapshot(seller, buybox, mine, **extra):
    return {'buyboxSeller': seller, 'buyboxPrice': buybox, 'myPrice': mine, **extra}


# ---------------------------------------------------------------- 名字规范化

def test_normalize_tolerates_page_noise():
    assert seller_identity.normalize('  Urban\u00a0 Electronics ') == 'urban electronics'
    assert seller_identity.normalize('✓ Urban Electronics') == 'urban electronics'
    assert seller_identity.normalize('URBAN ELECTRONICS') == 'urban electronics'
    assert seller_identity.normalize('') == ''


# ---------------------------------------------------------------- 自动学习

def test_name_is_learned_from_three_distinct_skus():
    db = session()
    for sku in ('s1', 's2'):
        seller_identity.observe(db, DEFAULT_STORE_ID, snapshot('Urban Electronics', 197, 197), sku, 197, 5)
    # 两个 SKU 还不够：一个对手在两个 SKU 上恰好同价是有可能的
    assert seller_identity.name_of(db, DEFAULT_STORE_ID) == ''
    seller_identity.observe(db, DEFAULT_STORE_ID, snapshot('Urban Electronics', 197, 197), 's3', 197, 5)
    assert seller_identity.name_of(db, DEFAULT_STORE_ID) == 'Urban Electronics'


def test_price_mismatch_observations_are_not_counted():
    """价格不等时出现的名字是赢走 Buybox 的对手，计进来会把对手投成自己。"""
    db = session()
    for sku in ('s1', 's2', 's3', 's4'):
        seller_identity.observe(db, DEFAULT_STORE_ID, snapshot('ValuezyVAT Registered', 100, 197), sku, 197, 5)
    assert seller_identity.name_of(db, DEFAULT_STORE_ID) == ''


def test_out_of_stock_observations_are_not_counted():
    db = session()
    for sku in ('s1', 's2', 's3'):
        seller_identity.observe(db, DEFAULT_STORE_ID, snapshot('Urban Electronics', 197, 197), sku, 197, 0)
    assert seller_identity.name_of(db, DEFAULT_STORE_ID) == ''


def test_manual_name_is_never_overwritten_by_learning():
    db = session()
    seller_identity.set_manual(db, DEFAULT_STORE_ID, 'My Real Shop')
    for sku in ('s1', 's2', 's3', 's4'):
        seller_identity.observe(db, DEFAULT_STORE_ID, snapshot('Urban Electronics', 197, 197), sku, 197, 5)
    assert seller_identity.name_of(db, DEFAULT_STORE_ID) == 'My Real Shop'


def test_minority_name_does_not_win_the_vote():
    """多数 SKU 上出现的是本店，少数同价对手不该被学成自己。"""
    db = session()
    for sku in ('a1', 'a2', 'a3', 'a4'):
        seller_identity.observe(db, DEFAULT_STORE_ID, snapshot('Urban Electronics', 197, 197), sku, 197, 5)
    for sku in ('b1', 'b2'):
        seller_identity.observe(db, DEFAULT_STORE_ID, snapshot('ValuezyVAT Registered', 106, 106), sku, 106, 5)
    assert seller_identity.name_of(db, DEFAULT_STORE_ID) == 'Urban Electronics'


# ---------------------------------------------------------------- 三态判定

def test_holds_buybox_is_tri_state():
    assert seller_identity.holds_buybox({'buyboxSeller': 'Urban Electronics'}, 'Urban Electronics') is True
    assert seller_identity.holds_buybox({'buyboxSeller': 'ValuezyVAT Registered'}, 'Urban Electronics') is False
    assert seller_identity.holds_buybox({'buyboxSeller': 'X'}, '') is None
    assert seller_identity.holds_buybox({'buyboxSeller': ''}, 'Urban Electronics') is None


# ---------------------------------------------------------------- 调价决策

def test_verified_holding_raises_instead_of_reporting_no_evidence():
    """本店持位、没有竞品 → 独家试涨，不是「证据不足」。

    采集器确认卖家名是本店时会把该卡标成 isMine=True，所以它不会进竞品池。
    """
    snap = {'buyboxPrice': 197, 'holdingBuyboxVerified': True,
            'offers': [offer(197, isMine=True)]}
    plan = pricing.plan(197, 2, RULE, snap)
    assert plan['regime'] == 'scarcity', plan
    assert plan['target'] is not None and plan['target'] > 197


def test_verified_competitor_holding_undercuts_instead_of_freezing():
    """对手同价持位 → 降到 196 去抢，而不是显示证据不足。"""
    snap = {'buyboxPrice': 197, 'holdingBuyboxVerified': False,
            'offers': [offer(197, isMine=False)]}
    plan = pricing.plan(197, 2, RULE, snap)
    assert plan['regime'] == 'compete', plan
    assert plan['target'] == 196


def test_unknown_identity_still_keeps_the_price():
    """还不知道本店卖家名时，同价确实两个方向都不能动——但要给出可操作的说法。"""
    snap = {'buyboxPrice': 197, 'offers': [offer(120)]}
    plan = pricing.plan(197, 2, RULE, snap)
    assert plan['regime'] == 'blind' and plan['target'] is None
    assert '卖家名' in plan['reason']


def test_competitor_holding_is_not_outbid_below_the_floor():
    snap = {'buyboxPrice': 197, 'holdingBuyboxVerified': False, 'offers': [offer(197)]}
    plan = pricing.plan(197, 2, {**RULE, 'min_price': 197}, snap)
    assert plan['target'] is None


# ---------------------------------------------------------------- 持位状态机

def test_ownership_marks_competitor_at_the_same_price_as_not_holding():
    """旧逻辑把「同价」直接写成 matched/holding=True，会把对手持位当成本店持位。"""
    result = estimate(None, 106, 106, 'official_live', '2026-10-18T00:00:00+00:00', verified=False)
    assert result['holding'] is False
    assert result['state'] == 'mismatch'
    assert result['identity_verified'] is True


def test_ownership_marks_verified_hold():
    result = estimate(None, 197, 197, 'official_live', '2026-10-18T00:00:00+00:00', verified=True)
    assert result['holding'] is True
    assert result['state'] == 'matched'
    assert result['identity_verified'] is True


def test_ownership_falls_back_to_price_inference_without_identity():
    result = estimate(None, 197, 197, 'official_live', '2026-10-18T00:00:00+00:00', verified=None)
    assert result['holding'] is True
    assert result['identity_verified'] is False


def test_ownership_ignores_identity_when_prices_differ():
    """价格不同就说明 Buybox 展示的不是本店价，无需身份也判得出未持位。"""
    result = estimate(None, 100, 197, 'official_live', '2026-10-18T00:00:00+00:00', verified=True)
    assert result['holding'] is False
