"""本店在 Takealot 的卖家名：Buybox 归属的唯一真实依据。

## 为什么需要它

Buybox 归属原来只能靠「Buybox 展示价 == 本店价」推断，于是只要同价，
`plan()` 就落进「与 Buybox 同价但卖家身份未核实」→ 界面显示「证据不足」→
既不涨价也不降价，永远不动。用户看到的就是一条永远不会调价的 SKU。

同价确实不能证明持位。库里有实测反例：
SKU 9902388556722 的 Buybox 卖家是 `ValuezyVAT Registered`，价格和本店一样是 106。
所以那道防线是对的，不能拆——缺的是「本店卖家名」。

商品页的 `Sold by X` 就是真实证据（`snapshot['buyboxSeller']`），
缺的只是「本店叫什么」。这个模块负责把「本店叫什么」确定下来：

1. **手填优先**：用户在设置里填的名字是权威，任何自动推断都不覆盖它。
2. **候选统计**：按不同 SKU 数统计同价卖家，仅供核对。
   同一个竞争卖家可以在多个 SKU 上同价持位，票数不能证明它是本店。
   历史 source=auto 也只作为候选；name_of 仅返回已手工确认的名字。

学到了名字之后，`buyboxSeller` 与它相同 → 本店持位（可以守位提价）；
不同 → 是对手持位（可以去抢位让利）。两条路都是真的调价。
"""
import re
import time
from datetime import datetime, timezone

from models import SystemSettings

PREFIX = '_seller_identity:'
# 候选提示的最少不同 SKU 数，不构成身份认证。
MIN_SKUS = 3
# 还要占观察到的 SKU 数的这个比例，避免两个名字都常见时误判。
MIN_SHARE = 0.6
# 每个名字最多记多少个 SKU，防止记录无限增长。
MAX_SKUS = 60


def normalize(name):
    """卖家名的比较口径：折叠空白、去掉首尾装饰、小写。

    页面上的 `Sold by` 常带 `&nbsp;`、勾选符号、结尾的 `(PTY) LTD` 之类差异，
    直接字符串相等会漏判，而漏判会把「本店持位」误判成「对手持位」。
    """
    text = re.sub(r'[\s\u00a0]+', ' ', str(name or '')).strip()
    text = text.strip('✓✔•·-—:：|').strip()
    return text.lower()


def _key(store):
    return PREFIX + store


def load(db, store):
    row = db.query(SystemSettings).filter_by(key=_key(store)).first()
    if not row:
        return {'name': '', 'source': '', 'observations': {}}
    try:
        value = row_from_json(row.value)
    except (TypeError, ValueError):
        return {'name': '', 'source': '', 'observations': {}}
    return value


def row_from_json(raw):
    import json
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def save(db, store, value):
    import json
    payload = json.dumps(value, ensure_ascii=False)
    row = db.query(SystemSettings).filter_by(key=_key(store)).first()
    if row:
        row.value = payload
    else:
        db.add(SystemSettings(key=_key(store), value=payload))
    db.commit()


def name_of(db, store):
    """Only an explicitly confirmed name is evidence of our seller identity.

    Repeated same-price winners can be the same competitor on many SKUs. Votes
    remain useful suggestions, including legacy source=auto rows, but no number
    of price matches verifies ownership.
    """
    value = load(db, store)
    return (value.get('name') or '').strip() if value.get('source') == 'manual' else ''


def set_manual(db, store, name):
    """用户手填。传空串表示清除，交还给自动学习。"""
    value = load(db, store)
    cleaned = (name or '').strip()[:80]
    value['name'] = cleaned
    value['source'] = 'manual' if cleaned else ''
    value['updated_at'] = datetime.now(timezone.utc).isoformat()
    save(db, store, value)
    return value


def observe(db, store, snapshot, sku, own_price, stock):
    """从一次采集里学习本店卖家名。返回 (当前名字, 是否发生了学习)。

    只在证据可用时计数：页面读到了卖家名、Buybox 价等于本店价、本店有货。
    价格不等时出现的名字是「赢走 Buybox 的对手」，不计入投票——那正是我们要
    识别的另一类，计进来会把对手投成自己。
    """
    if not isinstance(snapshot, dict):
        return name_of(db, store), False
    seller = (snapshot.get('buyboxSeller') or '').strip()
    buybox = snapshot.get('buyboxPrice')
    if not seller or not isinstance(buybox, (int, float)) or isinstance(buybox, bool):
        return name_of(db, store), False
    if not isinstance(own_price, (int, float)) or abs(buybox - own_price) >= .01:
        return name_of(db, store), False
    if not isinstance(stock, (int, float)) or stock <= 0:
        return name_of(db, store), False

    key = normalize(seller)
    if not key:
        return name_of(db, store), False

    value = load(db, store)
    observations = value.get('observations')
    if not isinstance(observations, dict):
        observations = {}
    entry = observations.get(key)
    if not isinstance(entry, dict):
        entry = {'name': seller[:80], 'skus': [], 'count': 0}
    entry['count'] = int(entry.get('count') or 0) + 1
    skus = [s for s in (entry.get('skus') or []) if isinstance(s, str)]
    if sku and sku not in skus and len(skus) < MAX_SKUS:
        skus.append(str(sku))
    entry['skus'] = skus
    entry['seen_at'] = datetime.now(timezone.utc).isoformat()
    observations[key] = entry
    value['observations'] = observations

    # 手工填过就不再自动覆盖：用户的名字是权威。
    if value.get('source') == 'manual' and value.get('name'):
        save(db, store, value)
        return value['name'], False

    learned = _dominant(observations)
    changed = False
    if learned:
        if normalize(value.get('name')) != normalize(learned['name']) or value.get('source') != 'auto':
            value['name'] = learned['name']
            value['source'] = 'auto'
            value['learned_at'] = datetime.now(timezone.utc).isoformat()
            changed = True
    save(db, store, value)
    return (value.get('name') or ''), changed


def _dominant(observations):
    """投票：按不同 SKU 数取第一，并要求达到门槛。"""
    ranked = []
    total_skus = 0
    seen = set()
    for key, entry in observations.items():
        if not isinstance(entry, dict):
            continue
        skus = {s for s in (entry.get('skus') or []) if isinstance(s, str)}
        seen |= skus
        ranked.append((len(skus), entry))
    total_skus = len(seen)
    if not ranked or total_skus <= 0:
        return None
    ranked.sort(key=lambda item: (-item[0], -(item[1].get('count') or 0)))
    best_skus, best = ranked[0]
    if best_skus < MIN_SKUS:
        return None
    if best_skus / total_skus < MIN_SHARE:
        return None
    name = (best.get('name') or '').strip()
    return {'name': name, 'skus': best_skus} if name else None


def seed_from_monitors(db, store):
    """从库里已有的采集快照补学一次本店卖家名。

    新装/升级后库里已经有几十条成功快照，没必要等下一轮采集才学会——
    每等一轮，用户就多看到一次「证据不足」。这里只读快照、只写身份记录，
    不动任何采集状态。
    """
    import json
    from models import SystemSettings
    from services.buybox_monitor import PREFIX as MONITOR_PREFIX
    if name_of(db, store):
        return 0
    learned = 0
    rows = db.query(SystemSettings).filter(
        SystemSettings.key.startswith(MONITOR_PREFIX + store + ':')).all()
    for row in rows:
        try:
            monitor = json.loads(row.value)
        except (TypeError, ValueError):
            continue
        if not isinstance(monitor, dict) or monitor.get('status') != 'success':
            continue
        snapshot = monitor.get('snapshot')
        if not isinstance(snapshot, dict):
            continue
        _name, changed = observe(db, store, snapshot, monitor.get('sku'),
                                 monitor.get('current_price'), monitor.get('stock'))
        learned += 1 if changed else 0
    return learned


def holds_buybox(snapshot, our_name):
    """页面上的 Buybox 卖家是不是本店。

    三态，调用方必须区分：
      True  —— 认出了本店名字且一致 → 本店持位
      False —— 认出了名字但不一致   → 对手持位（可以去抢）
      None  —— 还不知道本店名字      → 无法判断，维持保守
    """
    if not our_name:
        return None
    if not isinstance(snapshot, dict):
        return None
    seller = (snapshot.get('buyboxSeller') or '').strip()
    if not seller:
        return None
    return normalize(seller) == normalize(our_name)
