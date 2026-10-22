"""库存压力、降价护栏、报价离群校验。

这三个是同一件事的三面：库存决定「该不该降、该涨多快」，护栏保证「一次不会
砍太狠」，离群校验保证「不照着读错的数字改」。缺一个，另外两个都会被绕过。
"""
import pytest

from services import automatic_pricing as pricing
from services.operations_planner import velocity_of

BASE = dict(product_id='p', offer_id='1', sku='a', enabled=True, min_price=80, max_price=300,
            strategy='takealot_buybox', lead_amount=1, max_daily_changes=10,
            cooldown_minutes=10, revision=1, reference_price=200, smart_raise=True)


def offer(price, kind='fastest', **extra):
    return {'price': price, 'offerType': kind, 'status': 'in_stock', 'isMine': False, **extra}


def plan(current, stock, snapshot, **rule):
    return pricing.plan(current, stock, {**BASE, **rule}, snapshot)


# ── 库存压力 ────────────────────────────────────────────────────────────────

def test_tight_stock_refuses_to_undercut():
    """库存只够卖 3 天时，降价是纯亏：库存固定，便宜卖只会少赚钱还提前断货。"""
    p = plan(200, 3, {'offers': [offer(190)]}, velocity=1.0)
    assert p['pressure'] == 'tight'
    assert p['cover_days'] == 3.0
    assert p['regime'] == 'hold'
    assert p['target'] is None
    assert '库存紧张' in p['reason']


def test_normal_stock_keeps_competing():
    """同一个场景，库存够卖 100 天时该照常抢位——否则就是把整条 SKU 冻住。"""
    p = plan(200, 50, {'offers': [offer(190)]}, velocity=0.5)
    assert p['pressure'] == 'normal'
    assert p['cover_days'] == 100.0
    assert p['regime'] == 'compete'
    assert p['target'] == 189


def test_tight_stock_raises_faster_while_holding():
    """快卖完时断货成本远高于几个点毛利，守位应该一次多要一点。"""
    snap = {'buyboxPrice': 200, 'holdingBuyboxVerified': True, 'offers': [offer(300)]}
    assert plan(200, 50, snap, velocity=0.5)['target'] == 206      # 正常档 3%
    assert plan(200, 3, snap, velocity=1.0)['target'] == 210       # 紧张档 5%


def test_glut_keeps_default_discount_limit():
    """滞销判断不能放宽默认单次降价上限。"""
    snap = {'offers': [offer(150)]}
    normal = plan(200, 50, snap, velocity=0.5)     # 可售 100 天
    glut = plan(200, 300, snap, velocity=1.0)      # 可售 300 天
    assert normal['pressure'] == 'normal'
    assert normal['target'] == 170                 # 被 15% 护栏挡住
    assert glut['pressure'] == 'glut'
    assert glut['target'] == 170                   # 仍受默认 15% 上限约束


def test_unknown_velocity_keeps_old_behaviour():
    """没有销量观测就不假装知道，行为和加这个维度之前完全一致。"""
    p = plan(200, 5, {'offers': [offer(190)]})
    assert p['pressure'] == 'unknown'
    assert p['cover_days'] is None
    assert p['regime'] == 'compete'
    assert p['target'] == 189


# ── 降价护栏 ────────────────────────────────────────────────────────────────

def test_single_drop_is_capped():
    """竞品报价合理但跌幅过大时，本轮只走一步，不一次砍到位。"""
    p = plan(2000, 50, {'offers': [offer(1200)]},
             velocity=0.5, min_price=800, max_price=5000, reference_price=2000)
    assert p['regime'] == 'compete'
    assert p['target'] == 1700                     # 2000 × 85%
    assert '一次最多降' in p['reason']


def test_drop_cap_is_configurable():
    snap = {'offers': [offer(1200)]}
    common = dict(velocity=0.5, min_price=800, max_price=5000, reference_price=2000)
    assert plan(2000, 50, snap, **common)['target'] == 1700
    # 放宽护栏后直接贴到竞品下方。R2000 的货领先差额是 0.5% = R10（不再是固定的
    # R1）——R1 在 R2000 上只有 0.05%，对手一个 R1 就抹平，等于没领先。
    assert plan(2000, 50, snap, max_drop_pct=50, **common)['target'] == 1190


@pytest.mark.parametrize('cap,expected', [(5, 190), (15, 170), (50, 149)])
def test_glut_respects_configured_drop_limit(cap, expected):
    p = plan(200, 300, {'offers': [offer(150)]}, velocity=1.0, max_drop_pct=cap)
    assert p['pressure'] == 'glut'
    assert p['target'] == expected


def test_drop_cap_never_goes_below_the_floor():
    """护栏价和利润底价取更严的那个，两个都得守。"""
    p = plan(1000, 50, {'offers': [offer(910)]},
             velocity=0.5, min_price=900, max_price=5000, reference_price=1000, max_drop_pct=50)
    # 贴到竞品下方 R5（1000 的 0.5%），护栏 500 和底价 900 都不生效。
    assert p['target'] == 905
    assert p['target'] >= p['floor']


def test_lead_amount_scales_with_price():
    """R150 和 R3000 不能都只领先 R1：后者等于没领先。"""
    def lead(price, **rule):
        return pricing.plan(price, 50, {**BASE, **rule},
                            {'offers': [offer(price)]})['lead_amount']
    assert lead(150) == 1          # 0.75 → 1，小额商品保持 R1
    assert lead(1000) == 5
    assert lead(3000) == 15        # 固定 R1 时这里是 1
    assert lead(3000, lead_pct=0) == 1        # 显式关掉缩放 = 旧行为
    assert lead(3000, lead_amount=8) == 15    # lead_amount 是下限，不封顶
    assert lead(200000) == pricing.LEAD_CAP   # 大额商品不会让到 R1000


# ── 报价离群校验 ────────────────────────────────────────────────────────────

def test_sane_bounds():
    assert pricing._sane(2000, 2000) is True
    assert pricing._sane(500, 2000) is True         # 恰好 1/4
    assert pricing._sane(499, 2000) is False
    assert pricing._sane(8000, 2000) is True        # 恰好 4 倍
    assert pricing._sane(8001, 2000) is False
    assert pricing._sane(0, 2000) is False
    assert pricing._sane(True, 2000) is False
    assert pricing._sane(2000, None) is True        # 没有参考价就不做判断


def test_outlier_quote_is_ignored():
    """R 1999 的货读到 R 199 的竞品价：这个数字不能用来调价。"""
    p = plan(2000, 50, {'offers': [offer(199)]}, velocity=0.5, reference_price=2000)
    assert p['outliers'] == 1
    assert p['live_count'] == 0
    assert p['target'] is None


def test_outlier_quote_never_triggers_scarcity():
    """最危险的路径：报价被剔干净后，剩下的证据看起来像「没人竞争」，
    顺着走就会去涨价——方向正好反了。"""
    snap = {'buyboxPrice': 2000, 'holdingBuyboxVerified': True, 'offers': [offer(199)]}
    p = plan(2000, 50, snap, velocity=0.5, reference_price=2000)
    assert p['regime'] == 'hold'
    assert p['target'] is None
    assert '剔除' in p['reason']


# ── 日销口径 ────────────────────────────────────────────────────────────────

def test_velocity_of_matches_daily_sales():
    from datetime import date, timedelta
    today = date(2026, 10, 18)
    profile = {'observed_from': (today - timedelta(days=9)).isoformat(),
               'sales': [{'date': (today - timedelta(days=i)).isoformat(), 'quantity': 2}
                         for i in range(10)]}
    assert velocity_of(profile, today) == pytest.approx(2.0)


def test_velocity_of_caps_a_single_spike():
    """一天爆单不能把日销算成 10 倍，否则「可售天数」会假性变短、触发涨价。"""
    from datetime import date, timedelta
    today = date(2026, 10, 18)
    sales = [{'date': today.isoformat(), 'quantity': 30}]
    sales += [{'date': (today - timedelta(days=i)).isoformat(), 'quantity': 1} for i in range(1, 20)]
    profile = {'observed_from': (today - timedelta(days=19)).isoformat(), 'sales': sales}
    # 基线 (30+19)/20 = 2.45，封顶 3 倍 = 7.35
    assert velocity_of(profile, today) == pytest.approx(7.35)


def test_velocity_of_without_observation_window_is_zero():
    assert velocity_of({}, None) == 0.0
    assert velocity_of({'sales': [{'date': '2026-10-18', 'quantity': 5}]}, None) == 0.0
