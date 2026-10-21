"""Opt-in Buybox collection. This module has no price-changing request path."""
import asyncio
import json
import math
import threading
import time
from datetime import datetime, timezone
import httpx
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from models import Product, SystemSettings, DEFAULT_STORE_ID
from services.automatic_pricing import collect, official_offer
from services.takealot_seller_api import configured_key

PREFIX = '_pricing_monitor:'
# 采集循环自己的心跳。之前系统里没有任何地方能回答「循环还在转吗」：
# 界面只能看到每个 SKU 的 last_attempt_at，看不出是循环在跑还是有人手动点了。
WORKER_STATE = '_buybox_worker_state'
# 这三类错误是网站侧拦截，不是本程序故障，退避策略要分开处理。
THROTTLED_CODES = ('site_challenge', 'rate_limited', 'http_forbidden')
# 采集器自身暂时起不来（浏览器启动失败等）：这是可自愈的瞬时故障，按用户设定的
# 间隔重试就行。用指数退避会把一次启动抖动放大成 6 小时停采——2026-10-17 那次
# 浏览器起不来，界面就一直显示「约 8 小时后自动重试」。
# collector_busy 同理：浏览器被上一个任务占着（它卡死了没释放锁），不是页面问题，
# 也不是本程序的确定性故障。卡死的任务由看门狗在 JOB_TIMEOUT 后放弃，锁迟早会
# 放开；这时候如果按指数退避，第 5 次就排到 80 分钟后、第 7 次封顶 6 小时——
# 浏览器明明恢复了，这一行还在睡。界面文案写的是「等下一轮自动重试」，
# 所以退避必须真的按用户间隔走。
TRANSIENT_CODES = THROTTLED_CODES + ('collector_unavailable', 'collector_busy', 'collect_timeout')
_stop = threading.Event()
_thread = None
_supervisor = None
_stopped = False
# 上次自愈的时间。0 表示还没修过，所以进程启动后的第一轮就会修一次。
_last_repair_at = 0.0

# 同一轮里最多试几次。原来一次没跑成就直接判失败、等下一轮：浏览器偶发
# RuntimeError（2026-10-18 SKU 9902391031247）、首屏骨架还没渲染完就读价，
# 都会被当成故障写进记录，用户看到的就是一条红杠。同轮重试一次的成本远低于
# 让用户等 30 分钟再看一条红色。
COLLECT_ATTEMPTS = 2
# 两次尝试之间的等待。给页面留出把报价渲染出来的时间。
COLLECT_RETRY_DELAY = 5.0
# 单个采集任务的硬上限。浏览器一旦卡死（profile 锁、渲染进程挂住），
# 这个任务永远不会返回；以前循环会一直等下去，而且等待期间还在刷新心跳，
# 界面上永远是「运行中」——2026-10-20 线上就是这么停采 17 分钟的。
# 到点就放弃这一个任务并如实记账，让循环继续转、让自愈能跑起来。
JOB_TIMEOUT = 240
# 单次采集在**协程内部**的硬上限。这是 JOB_TIMEOUT 治不了的那半个问题：
# 看门狗只能 `future.cancel()`，而 ThreadPoolExecutor 里已经跑起来的任务取消不了，
# 线程会带着 Playwright 的 node 驱动 + Chromium 一起挂在那里，`_lock` 也不释放。
# 2026-10-21 线上就是这样把线程耗光的——`/api/image-proxy` 全线 500
# （RuntimeError: can't start new thread），而内存只用了 0.9G/8G。
# 现在把上限下沉到协程里：到点由 asyncio 取消 collect()，quote_context 的
# finally 正常跑完（关页面、关浏览器、放锁），线程干干净净回到池子里。
# 必须比 JOB_TIMEOUT 小，否则看门狗先动手，又回到取消不了线程的老路。
COLLECT_TIMEOUT = JOB_TIMEOUT - 60


def normalize_interval_minutes(value, default_minutes=5):
    """把记录里的 interval_minutes 归一成合法的正整数（分钟）。

    `interval_minutes` 在库里有三个来源：用户配置（Pydantic 保证 int≥5）、
    旧版本写回的行、以及直接改库的历史数据。后两者可能是 null，而 `.get(key, 5)`
    只在**键缺失**时给默认值，键存在但值是 null 时返回 None。

    null 往下游走有两条路，两条都得堵：
      1. 读路径（`interval_seconds`）：`None * 60` 抛 TypeError；
      2. 写路径（`collect-now` / `web-open`）：把 null 原样塞回 `MonitorBulk`
         或库里，前者被 Pydantic 判 422（点「立即采集」直接报错），后者把
         null 一直传下去，让第 1 条永远有货。

    默认值 5 与 loop_status、repair_monitor、前端 `Number(x) || 5` 的既有口径一致，
    没有引入新语义。
    """
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default_minutes


def interval_seconds(value, default_minutes=5):
    """采集间隔（秒）。

    为什么这个归一不能省：`process()` 里算 `next_due` 的那段在 `finally` 里，
    抛出去就整轮采集一起中断（`run()` 只打印一句「worker unavailable」），
    而这一行因为 `next_due` 没被推进，每一轮都会被重新选中、每一轮再抛一次——
    一条脏记录足以让 28 个 SKU 永久停采，日志里却只有一句通用失败。
    """
    return normalize_interval_minutes(value, default_minutes) * 60

# 这些是「这一轮压根没跑起来」或「页面还没渲染完」的信号，同轮重试有意义。
# 网站验证（site_challenge）不在里面：验证不会因为隔 5 秒再开一次就解除，
# 重试只是白白多打一次请求，对站点也不礼貌。
_RETRYABLE_ERRORS = ('collector_unavailable', 'price_not_found')


def _should_retry_collect(snapshot):
    """这一轮的结果值不值得再试一次。"""
    if not isinstance(snapshot, dict):
        return True
    from services.collection_diagnostics import classify
    code, _ = classify(snapshot)
    # Explicit site/identity failures outrank empty DOM counters: a challenge
    # page also has zero cards. Never revisit it as a skeleton-page retry.
    if code in ('site_challenge', 'http_forbidden', 'rate_limited',
                'variant_unmatched', 'product_not_found', 'invalid_url',
                'product_redirect', 'unstable_price', 'tls_error'):
        return False
    if snapshot.get('error') in ('variant_unmatched', 'http_forbidden', 'rate_limited'):
        return False
    diagnostics = snapshot.get('diagnostics') or {}
    # 一个元素都没认出来 = 落地页还没渲染成商品页，再等一会儿往往就有了。
    if diagnostics.get('buybox_cards') == 0 and diagnostics.get('price_nodes') == 0:
        return True
    # Buybox 卡在，但卡里几乎没有文本（实测 5 个字符）= 骨架屏，等它填内容。
    text_len = diagnostics.get('buybox_card_text_len')
    if isinstance(text_len, int) and text_len <= 10 and snapshot.get('error'):
        return True
    return str(snapshot.get('error') or '') in _RETRYABLE_ERRORS


class CollectTimeout(RuntimeError):
    """单次采集在自己的硬上限内没返回，已就地取消并释放浏览器。

    与 collector_busy 区分：busy 是「别人占着浏览器」，这条是「自己卡住了」。
    两者的共同点是都不该在同轮里重试——重试只会再卡一次。
    """


async def _collect_once(product, own, seller_name):
    """跑一次采集，并给它一个能被真正取消的上限。

    `asyncio.wait_for` 超时会 cancel 内层协程，`collect()` 的 `async with
    quote_context()` 因此走正常退出路径：关页面 → 关 context → 停 Playwright →
    放 _lock。这正是「放弃任务」原来缺的那一步——线程池的 future.cancel()
    对已经在跑的线程无效，只能把协程取消掉才收得回资源。
    """
    try:
        return await asyncio.wait_for(
            collect(product, own, seller_name), timeout=COLLECT_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise CollectTimeout(
            f'采集超过 {COLLECT_TIMEOUT} 秒未返回，已取消并释放浏览器') from exc


def _collect_with_retry(product, own, result, seller_name=None):
    """采一次；遇到可自愈的失败就在同一轮里再试，最多 COLLECT_ATTEMPTS 次。

    只对「页面没渲染完 / 浏览器抖了一下」重试。配置错误、商品下架、变体确认
    不了这些重试也不会变好，立刻抛出，由调用方按原逻辑记录。
    超时也不重试：能卡满 COLLECT_TIMEOUT 的任务，再给一次机会只会再卡一遍，
    而每卡一次都要多占一个浏览器进程。
    """
    last = None
    for attempt in range(1, COLLECT_ATTEMPTS + 1):
        try:
            snapshot = asyncio.run(_collect_once(product, own, seller_name))
        except CollectTimeout:
            raise
        except Exception:
            if attempt >= COLLECT_ATTEMPTS:
                raise
            last = None
            time.sleep(COLLECT_RETRY_DELAY)
            continue
        if not _should_retry_collect(snapshot):
            if attempt > 1:
                result['collect_attempts'] = attempt
            return snapshot
        last = snapshot
        if attempt < COLLECT_ATTEMPTS:
            result['collect_attempts'] = attempt + 1
            time.sleep(COLLECT_RETRY_DELAY)
    return last


def process(row_id):
    from database import SessionLocal
    with SessionLocal() as db:
        row = db.query(SystemSettings).filter(SystemSettings.key == row_id).first()
        if not row: return
        config = json.loads(row.value)
        if config.get('web_requested') and not config.get('enabled'): return
        if not (config.get('enabled') or config.get('request_once')) or config.get('next_due', 0) > time.time(): return
        original = row.value
        started_at=time.time()
        config.update(status='running', next_due=time.time()+300)
        claim = json.dumps(config, ensure_ascii=False)
        if db.query(SystemSettings).filter(SystemSettings.key==row_id, SystemSettings.value==original).update({'value':claim}) != 1:
            db.rollback(); return
        db.commit(); result = dict(config)
        # 自证标记：这一条记录是哪个版本的采集代码写的。诊断字段有可能被中间层
        # 丢掉，所以标记放在 result 顶层——顶层一定落库。排查「代码上线了但线上
        # 行为没变」时，先看这个字段就能分清是部署没生效还是另有进程在写。
        result['diag_build'] = 'bb-v7'
        stage='catalog'
        try:
            product = db.query(Product).filter(Product.id==config['product_id'], Product.store_id==DEFAULT_STORE_ID, Product.is_archived==False).first()
            if not product: raise ValueError('商品已归档或不属于本店')
            key = configured_key(db)
            if not key: raise ValueError('卖家接口未配置')
            stage='seller_api'
            profile_row=db.query(SystemSettings).filter(SystemSettings.key==f'operations_profile:{DEFAULT_STORE_ID}:{product.id}').first()
            profile=json.loads(profile_row.value) if profile_row else {}
            cached=next((o for o in profile.get('offers',[]) if str(o.get('id'))==str(config['offer_id']) and o.get('sku')==config['sku']),{})
            try: age=time.time()-datetime.fromisoformat(profile['synced_at']).timestamp()
            except (KeyError,ValueError): age=math.inf
            if profile.get('data_source')=='seller_api' and 0<=age<3900 and isinstance(cached.get('price'),(int,float)) and cached['price']>0 and isinstance(cached.get('stock'),(int,float)):
                own={'offer_id':config['offer_id'],'sku':config['sku'],'selling_price':cached['price'],'stock_at_takealot_total':cached['stock'],'title':cached.get('title')}
                result['own_price_source']='official_snapshot';result['own_price_asof']=profile['synced_at']
            else:
                with httpx.Client(timeout=25, follow_redirects=False) as client:
                    own = official_offer(client, key, config['offer_id'])
                result['own_price_source']='official_live';result['own_price_asof']=datetime.now(timezone.utc).isoformat()
            if own.get('sku') != config['sku']: raise ValueError('卖家SKU已变更，需重新绑定')
            stage='public_page'
            # 本店卖家名：采集器拿它把页面上的 "Sold by X" 判成是不是本店。
            # 还不知道时传空串，采集器退回价格推断并标"未核实"。
            from services import seller_identity
            our_seller = seller_identity.name_of(db, DEFAULT_STORE_ID)
            snapshot = _collect_with_retry(product, own, result, our_seller)
            from services.collection_diagnostics import classify
            result['diagnostics']=snapshot.get('diagnostics', {})
            code,message=classify(snapshot)
            result['error_code']=code
            bb = snapshot.get('buyboxPrice')
            if snapshot.get('error') or isinstance(bb,bool) or not isinstance(bb,(int,float)) or not math.isfinite(bb) or bb<=0:
                raise ValueError(snapshot.get('error_message') or message or '未读取到有效Buybox报价')
            competitors=[o['price'] for o in snapshot.get('offers',[]) if o.get('status')=='in_stock' and not o.get('isMine') and not isinstance(o.get('price'),bool) and isinstance(o.get('price'),(int,float)) and math.isfinite(o['price']) and o['price']>0]
            # 学习本店卖家名：只在「Buybox 价 == 本店价、本店有货」的观察里投票，
            # 按不同 SKU 数取众数。学到之后，同一轮的下一行就能按卖家名判持位，
            # 不用再靠价格猜。手工填过的名字不会被覆盖。
            try:
                seller_identity.observe(db, DEFAULT_STORE_ID, snapshot, config['sku'],
                                        own['selling_price'], own['stock_at_takealot_total'])
            except Exception:
                db.rollback()
            result['snapshot'] = snapshot
            # 持位状态如实落库：verified 是 True/False/None 三态，
            # None 表示还不知道本店卖家名，界面要显示"待核实"而不是"已核实"。
            result.update(status='success', buybox_price=bb, competitor_price=min(competitors, default=None),
                          holding_buybox=snapshot.get('holdingBuyboxVerified'),
                          holding_verified=snapshot.get('holdingBuyboxVerified') is True,
                          seller_name=our_seller,
                          price_matches_buybox=abs(bb-own['selling_price'])<.01, current_price=own['selling_price'],
                          stock=own['stock_at_takealot_total'], last_success_at=datetime.now(timezone.utc).isoformat(),
                          source='cloud_browser', quote_time_source='cloud_completion', error='', error_code='', error_stage='', failures=0)
            from services.price_ownership import estimate,pending
            check_own=own
            check_source=result['own_price_source'];check_asof=result['own_price_asof']
            try:
                if abs(bb-own['selling_price'])>=.01:
                    with httpx.Client(timeout=5,follow_redirects=False) as client:
                        check_own=official_offer(client,key,config['offer_id'])
                    if check_own.get('sku')!=config['sku']: raise ValueError('SKU changed')
                    check_source='official_live';check_asof=datetime.now(timezone.utc).isoformat()
                result['price_ownership']=estimate(config.get('price_ownership'),bb,check_own['selling_price'],check_source,check_asof,
                                                   verified=snapshot.get('holdingBuyboxVerified'))
            except Exception:
                result['price_ownership']=pending(config.get('price_ownership'),failed=True)
        except Exception as error:
            # 非 ValueError 表示这一轮根本没跑完（浏览器起不来、卖家接口超时）。
            # 这时不能沿用上一轮的 error_code 和 diagnostics：上一轮的
            # price_not_found 会让界面把「浏览器坏了」显示成「页面没有 Buybox 价」，
            # 旧计数（buybox_cards=2 等）也会被当成这一轮的结果。2026-10-17 就是
            # 这样被误导了好几轮。清掉，如实记下失败类型。
            finished = isinstance(error, ValueError)
            # 浏览器被上一个任务占着：既不是页面问题，也不是「采集器没起来」。
            # 混成 collector_unavailable 会让界面劝用户「点立即采集重试」，
            # 而实际重试只会继续等同一把锁。
            from services.quote_browser import CollectorBusy
            busy = isinstance(error, CollectorBusy)
            # 自己卡住了：浏览器已经就地取消并释放，这条要如实写 collect_timeout，
            # 而不是混成 collector_unavailable（那会让人去查「采集器是不是没起来」）。
            timed_out = isinstance(error, CollectTimeout)
            code = result.get('error_code') if finished else (
                'collect_timeout' if timed_out else ('collector_busy' if busy else 'collector_unavailable'))
            if not finished:
                result['diagnostics'] = {'stage': stage, 'failure': type(error).__name__}
            result.update(status='error', error_stage=stage, error_code=code or ('seller_read_failed' if stage=='seller_api' else type(error).__name__),
                          error=str(error) if (finished or busy or timed_out) else '采集器不可用（浏览器或接口未就绪）；未改价', failures=config.get('failures',0)+1)
        finally:
            result.update(request_once=False, last_attempt_at=datetime.now(timezone.utc).isoformat(),last_attempt_source='cloud_browser')
            delay=interval_seconds(config.get('interval_minutes'))
            if result.get('status')=='error':
                if result.get('error_code') in TRANSIENT_CODES:
                    # 网站拦截随时可能解除，指数退避只会把"恢复检测"拖到 6 小时：
                    # 每轮都白等。这里按用户设定的间隔重试，且不低于 30 分钟。
                    delay=max(1800, delay)
                else:
                    # 自身故障（解析器、配置、网络）才用指数退避，避免反复空转。
                    delay=min(6*3600,max(300, delay*2**min(result['failures']-1,4)))
            result['next_due']=time.time()+delay if result.get('status')=='error' else max(time.time()+10,started_at+delay)
            db.expire_all()
            # Keep a concurrent stop or changed schedule intact.
            db.query(SystemSettings).filter(SystemSettings.key==row_id,SystemSettings.value==claim).update({'value':json.dumps(result,ensure_ascii=False)})
            db.commit()

def _maybe_repair(db):
    """每 5 分钟修一次记录，其余循环直接跳过。

    循环本身 10 秒一转，每条记录都全表扫一遍没必要；5 分钟已经远快于任何
    用户能感知的恢复时间，也和最短采集间隔一致。
    """
    global _last_repair_at
    now = time.time()
    if now - _last_repair_at < 300:
        return
    _last_repair_at = now
    try:
        from services.collection_maintenance import repair
        outcome = repair(db, now=now)
        if outcome.get('changed'):
            print(f"Collection records repaired: {outcome['changed']}", flush=True)
    except Exception:
        # 自愈失败不能拖垮采集循环：下一轮还会再试。
        db.rollback()
    try:
        # 本店卖家名：启动时先从已有快照里补学一次。学到了之后，
        # 同一轮的采集就能按卖家名判持位，不用再等下一轮。
        from services import seller_identity
        if seller_identity.seed_from_monitors(db, DEFAULT_STORE_ID):
            print('Seller identity learned from existing snapshots', flush=True)
    except Exception:
        db.rollback()


def start_worker():
    global _thread, _supervisor, _stopped
    if _thread and _thread.is_alive(): return
    _stopped = False
    _stop.clear()
    def run():
        from database import SessionLocal
        while not _stop.is_set():
            try:
                with SessionLocal() as db:
                    from api.collector_routes import connected
                    # 自愈要先于排期，而且**与本机采集器是否在线无关**：本机采集器
                    # 也会写出 error 记录，同样需要修。放在 connected 判断之前，
                    # 否则「有本机采集器」就等于「记录永远不自愈」。
                    _maybe_repair(db)
                    if connected(db):
                        _record_cycle(db, 0, 'local_collector')
                        _stop.wait(10)
                        continue
                    jobs=[]
                    for row in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX+DEFAULT_STORE_ID+':')).all():
                        value=json.loads(row.value)
                        if not (value.get('web_requested') and not value.get('enabled')) and (value.get('enabled') or value.get('request_once')) and value.get('next_due',0)<=time.time(): jobs.append((value.get('next_due',0),row.key))
                    _record_cycle(db, len(jobs), None)
                # 一轮到底要跑多久，必须记下来。28 个 SKU 串行采、每个十几秒，
                # 实际一轮可能远超用户设的 5 分钟间隔——那时「下一轮已延迟」
                # 不是故障，是排期本身排不下。不测这个数就只能猜。
                round_started=time.time()
                # Two bounded jobs; never open an unbounded browser fleet.
                # 不能用 with：卡死的任务会让 pool.__exit__ 的 shutdown(wait=True)
                # 无限等下去，循环照样冻住——只是把卡点从 wait 挪到了退出。
                pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='buybox-read')
                started={}; owners={}
                try:
                    pending=set()
                    for _,key in sorted(jobs):
                        if _stop.is_set(): break
                        while len(pending)>=2 and not _stop.is_set():
                            done,pending=_wait_with_heartbeat(pending, SessionLocal, started, owners)
                            for finished in done: finished.result()
                        if _stop.is_set(): break
                        future=pool.submit(process,key)
                        pending.add(future); started[future]=time.time(); owners[future]=key
                        if _stop.wait(2): break
                    while pending:
                        done,pending=_wait_with_heartbeat(pending, SessionLocal, started, owners)
                        for future in done: future.result()
                finally:
                    pool.shutdown(wait=False)
                if jobs:
                    with SessionLocal() as db:
                        _record_round_seconds(db, time.time()-round_started, len(jobs))
            except Exception:
                print('Buybox collection worker unavailable; retry scheduled',flush=True)
            _stop.wait(10)
    _thread=threading.Thread(target=run,daemon=True,name='buybox-monitor');_thread.start()
    if _supervisor is None or not _supervisor.is_alive():
        _supervisor=threading.Thread(target=_supervise,daemon=True,name='buybox-supervisor');_supervisor.start()


def _supervise():
    """采集是自动调价的上游。线程万一死掉不能静默停摆，这里每 30 秒确认一次并拉起。"""
    while True:
        time.sleep(30)
        try:
            if not _stopped and not (_thread and _thread.is_alive()):
                print('Buybox worker was not alive; restarting',flush=True)
                start_worker()
        except Exception:
            pass


def _wait_with_heartbeat(pending, session_factory, started=None, owners=None):
    """Keep the owning loop observable while bounded collection jobs run.

    No supervisor heartbeat: a dead owner must still become stale. Waiting does
    not count as another cycle or a successful quote, and never starts new jobs.

    但「等待期间刷新心跳」有个致命副作用：任务卡死时心跳永远新鲜，界面一直显示
    「运行中」，谁也不知道采集已经停了。所以这里必须带**每个任务自己的**硬上限：
    超时就放弃这个任务、把这一行记成 collect_timeout，并让循环继续往下走。
    """
    started = started if isinstance(started, dict) else {}
    while True:
        done, remaining = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
        with session_factory() as db:
            try:
                row = db.query(SystemSettings).filter_by(key=WORKER_STATE).first()
                if row:
                    value = json.loads(row.value)
                    value['alive_at'] = time.time()
                    row.value = json.dumps(value, ensure_ascii=False)
                    db.commit()
            except Exception:
                db.rollback()
        if done:
            return done, remaining
        now = time.time()
        stuck = {future for future in remaining if now - started.get(future, now) > JOB_TIMEOUT}
        if stuck:
            for future in stuck:
                future.cancel()
            _record_stuck(session_factory, stuck, owners or {})
            return set(), remaining - stuck


def _record_stuck(session_factory, futures, owners):
    """把「超过 JOB_TIMEOUT 还没返回」的任务如实落库，而不是留在 running 里。

    任务线程可能还挂在那里（浏览器卡死时无法可靠中断），但它已经不产生任何结果；
    继续假装它在跑，界面就永远显示「采集中」，用户只能反复点立即采集。

    旧 diagnostics 必须清掉：那一份计数属于上一轮（可能是成功的那一轮），
    留着它等于把旧结论挂在新结论下面——「复制诊断」会带出 buybox_cards=0 /
    price_nodes=12 这类数字，谁看都会以为是这一轮量到的，于是去查解析锚点，
    而真正的问题是浏览器没返回。`process()` 里对「这一轮没跑完」也是同样处理
    （见那句「不能沿用上一轮的 error_code 和 diagnostics」）。
    """
    keys = [owners.get(future) for future in futures]
    keys = [key for key in keys if key]
    now = time.time()
    with session_factory() as db:
        try:
            for key in keys:
                row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
                if not row:
                    continue
                value = json.loads(row.value)
                if value.get('status') not in ('running', 'queued'):
                    continue
                value.update(
                    status='error', error_stage='browser', error_code='collect_timeout',
                    error=f'采集超过 {JOB_TIMEOUT} 秒没有返回（浏览器卡住），本轮已放弃，稍后重试',
                    last_attempt_at=datetime.now(timezone.utc).isoformat(),
                    last_attempt_source='cloud_browser',
                    failures=int(value.get('failures') or 0) + 1,
                    next_due=now + max(300, interval_seconds(value.get('interval_minutes'))))
                value.pop('diagnostics', None)
                row.value = json.dumps(value, ensure_ascii=False)
            state = db.query(SystemSettings).filter_by(key=WORKER_STATE).first()
            if state:
                payload = json.loads(state.value)
                payload.update(stuck_at=datetime.now(timezone.utc).isoformat(), stuck_jobs=len(keys))
                state.value = json.dumps(payload, ensure_ascii=False)
            elif keys:
                db.add(SystemSettings(key=WORKER_STATE, value=json.dumps(
                    {'stuck_at': datetime.now(timezone.utc).isoformat(), 'stuck_jobs': len(keys)},
                    ensure_ascii=False)))
            db.commit()
            if keys:
                print(f'Buybox collection job exceeded {JOB_TIMEOUT}s; abandoned {len(keys)}', flush=True)
        except Exception:
            db.rollback()


def _record_cycle(db, jobs, paused_by):
    """每轮循环写一次心跳：证明循环在转，也让界面能说清「为什么这一轮没采」。"""
    try:
        row=db.query(SystemSettings).filter_by(key=WORKER_STATE).first()
        value=json.loads(row.value) if row else {}
        value.update(alive_at=time.time(), cycles=int(value.get('cycles',0))+1,
                     last_cycle_at=datetime.now(timezone.utc).isoformat(),
                     last_jobs=jobs, paused_by=paused_by)
        if row: row.value=json.dumps(value,ensure_ascii=False)
        else: db.add(SystemSettings(key=WORKER_STATE,value=json.dumps(value,ensure_ascii=False)))
        db.commit()
    except Exception:
        db.rollback()


def _record_round_seconds(db, seconds, jobs):
    """记下「采 N 个 SKU 实际花了多久」。只保留最近一次，够用来判断排期是否排得下。"""
    try:
        row=db.query(SystemSettings).filter_by(key=WORKER_STATE).first()
        value=json.loads(row.value) if row else {}
        value.update(round_seconds=round(seconds,1), round_jobs=jobs, round_at=datetime.now(timezone.utc).isoformat())
        if row: row.value=json.dumps(value,ensure_ascii=False)
        else: db.add(SystemSettings(key=WORKER_STATE,value=json.dumps(value,ensure_ascii=False)))
        db.commit()
    except Exception:
        db.rollback()


def _age_seconds(stamp, now):
    """ISO 时间戳到现在的秒数；解析不了就返回 None（界面按「未知」处理）。"""
    try:
        return int(now - datetime.fromisoformat(stamp).timestamp())
    except (TypeError, ValueError):
        return None


def loop_status(db, store=DEFAULT_STORE_ID):
    """采集循环健康度：循环活着吗、有没有在采、下一轮还有多久。

    原来界面上只有每个 SKU 的 last_attempt_at，看不出「循环停了」和
    「这一条刚好没到期」的区别，用户只能反复点立即采集。
    """
    now=time.time()
    row=db.query(SystemSettings).filter_by(key=WORKER_STATE).first()
    state=json.loads(row.value) if row else {}
    alive_at=state.get('alive_at') or 0
    monitors=[]
    for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX+store+':')).all():
        try: monitors.append(json.loads(r.value))
        except Exception: pass
    enabled=[m for m in monitors if m.get('enabled') or m.get('request_once')]
    # next_due=0 是「已到期 / 没排期」的哨兵值，不是 1970 年的时间戳。
    # 把它当成真实时间参与 min()，算出来的延迟会是「已延迟 1792265800 秒」——
    # 界面上就是「已延迟 2987107 分钟」，比不显示更糟。
    dues=[m.get('next_due') for m in enabled if isinstance(m.get('next_due'),(int,float)) and m['next_due'] > 0]
    intervals=[m.get('interval_minutes') for m in enabled if isinstance(m.get('interval_minutes'),int)]
    return {
        'worker_alive': bool(alive_at and now-alive_at < 60),
        'worker_last_seen_at': datetime.fromtimestamp(alive_at,timezone.utc).isoformat() if alive_at else None,
        'cycles': state.get('cycles'),
        'last_cycle_at': state.get('last_cycle_at'),
        'last_jobs': state.get('last_jobs'),
        # 有本机采集器在线时云端循环会让位，这不是故障，要说清楚。
        'paused_by': state.get('paused_by'),
        'enabled': len(enabled),
        'due_now': len([m for m in enabled if (m.get('next_due') or 0) <= now]),
        'running': len([m for m in enabled if m.get('status') in ('running','queued')]),
        'success': len([m for m in enabled if m.get('status')=='success']),
        'error': len([m for m in enabled if m.get('status')=='error']),
        'interval_minutes': max(set(intervals),key=intervals.count) if intervals else None,
        'next_due_in': int(min(dues)-now) if dues else None,
        # 上一轮到现在多久了。心跳只能证明「线程还在等」，不能证明「循环在转」：
        # 任务卡死时心跳照样新鲜。界面拿这个数和间隔比，才知道是排期排不下还是真停了。
        'cycle_age_seconds': _age_seconds(state.get('last_cycle_at'), now),
        # 最近一次放弃卡死任务的时间和个数。>0 说明采集环境出过问题，要提示重启。
        'stuck_at': state.get('stuck_at'),
        'stuck_jobs': state.get('stuck_jobs') or 0,
        # 上一轮实测耗时 + 设定间隔：两者一比就知道「延迟」是故障还是排期排不下。
        'round_seconds': state.get('round_seconds'),
        'round_jobs': state.get('round_jobs'),
        # 每个 SKU 平均多少秒。界面用它解释「为什么一轮跑不完」。
        'seconds_per_sku': round(state['round_seconds']/state['round_jobs'],1)
            if state.get('round_seconds') and state.get('round_jobs') else None,
        'last_attempt_at': max([m.get('last_attempt_at') or '' for m in enabled],default='') or None,
    }


def stop_worker():
    global _stopped
    _stopped=True
    _stop.set()


def catalog(db, store):
    """Collection-only SKU metadata; does not read pricing rules or calculate floors."""
    import re
    profiles={r.key:json.loads(r.value)for r in db.query(SystemSettings).filter(SystemSettings.key.startswith('operations_profile:'+store+':')).all()}
    monitors={r.key:json.loads(r.value)for r in db.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX+store+':')).all()}
    from services.product_names import chinese_name
    rows=[]
    for p in db.query(Product).filter(Product.store_id==store,Product.is_archived==False).all():
        profile=profiles.get('operations_profile:'+store+':'+p.id,{})
        offers=profile.get('offers',[])
        if not offers and len(profile.get('offer_ids',[]))==1:
            skus=re.split(r'[\s,，;；]+',(p.sku or '').strip())
            if len(skus)==1 and skus[0]:offers=[dict(id=profile['offer_ids'][0],sku=skus[0],title=p.product_name,price=profile.get('current_price'),stock=profile.get('stock'))]
        for offer in offers:
            from services.collection_diagnostics import displayed_monitor
            monitor=displayed_monitor(monitors.get(PREFIX+store+':'+p.id+':'+str(offer['id']),{}))
            cn, name_status = chinese_name(p, offer['sku'])
            rows.append(dict(product_id=p.id,offer_id=str(offer['id']),sku=offer['sku'],name=offer.get('title')or p.product_name, chinese_name=cn, name_status=name_status,
                url=p.takealot_url,price=offer.get('price'),stock=offer.get('stock'),group=profile.get('group')or'未分类',
                monitor=monitor,rule={}))
    return rows
