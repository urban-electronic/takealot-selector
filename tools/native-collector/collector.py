"""Run the retained PricePilot parser in a local persistent browser, quote-only."""
import argparse
import asyncio
import getpass
import importlib.util
import json
import os
import re
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone
from urllib import request, error
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
BASE = 'https://takealot-selector-production.up.railway.app'

APP_DIR = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'TakealotCollector'
try: APP_DIR.mkdir(parents=True, exist_ok=True)
except Exception: pass
STATUS_FILE = APP_DIR / 'collector_status.json'
_CURRENT = {'profile': '', 'round': 0}

def write_status(state, **extra):
    """原子写状态文件供 GUI 只读呈现（tmp + os.replace，避免读到半截文件）。

    附带 pid / 浏览器档案 / 轮次：GUI 据此判断是否为**当前进程**的最新状态，
    从而不会把上一次运行（或上一轮）的陈旧状态当成现在的状态。"""
    payload = {'state': state, 'ts': datetime.now(timezone.utc).isoformat(),
               'pid': os.getpid(), 'profile': _CURRENT.get('profile', ''),
               'round': _CURRENT.get('round', 0), 'run_id': _CURRENT.get('run_id', ''),
               'stage': _CURRENT.get('stage', '')}
    payload.update(extra)
    try:
        tmp = STATUS_FILE.with_name(STATUS_FILE.name + '.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, STATUS_FILE)
    except Exception:
        pass

def parser_module():
    source = ROOT / 'pricepilot_collector.py'
    if not source.exists(): source = ROOT.parent.parent / 'backend/services/pricepilot_collector.py'
    spec = importlib.util.spec_from_file_location('pricepilot_parser', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def singleton(profile):
    profile.mkdir(parents=True, exist_ok=True)
    handle = (profile / '.selector-collector.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('此浏览器档案已有采集器运行，请先停止原实例') from None
    return handle

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class Api:
    def __init__(self, base, key):
        parsed = urlsplit(base)
        if parsed.scheme != 'https' or parsed.hostname != urlsplit(BASE).hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('仅连接已部署的选品系统 HTTPS 后端')
        self.base, self.key = base.rstrip('/'), key.strip()
        self.opener = request.build_opener(NoRedirect)

    def call(self, path, body=None):
        if path not in ('/api/collector/status','/api/collector/products','/api/collector/offers','/api/collector/release'):
            raise ValueError('采集器只能使用行情任务接口')
        headers = {'X-Collector-Key': self.key, 'X-Collector-Client':'local_collector',
                   'X-Store-ID':'default-store', 'Content-Type':'application/json'}
        for attempt in range(3):
            req = request.Request(self.base+path, data=json.dumps(body).encode() if body is not None else None,
                                  headers=headers, method='POST' if body is not None else 'GET')
            try:
                with self.opener.open(req, timeout=30) as response:
                    return json.loads(response.read())
            except error.HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt == 2:
                    # Never print arbitrary proxy/server bodies, reflected credentials or cookies.
                    detail=''
                    try:
                        payload=json.loads(exc.read(8192))
                        message=payload.get('detail') if isinstance(payload,dict) else None
                        if message in ('店铺不存在或已停用','采集器仅绑定自营店铺','无效或缺失 API Key，请在设置页填写'):
                            detail='：'+message
                    except (ValueError,TypeError): pass
                    if exc.code==400 and not detail:
                        detail='：请求被拒绝，未取得可识别的服务端说明；请检查本机代理/网络与服务地址'
                    if exc.code==401 and not detail: detail='：网站服务访问密钥无效或缺失'
                    raise RuntimeError(f'服务接口返回 HTTP {exc.code}{detail}') from None
            except (error.URLError, TimeoutError):
                if attempt == 2: raise RuntimeError('服务连接超时，任务稍后重试') from None
            time.sleep(2*(attempt+1))

def valid_product_url(value):
    import re
    parsed=urlsplit(value or '')
    return parsed.scheme=='https' and parsed.hostname in ('takealot.com','www.takealot.com') and bool(re.search(r'PLID\d+',parsed.path,re.I))

async def scrape(context, item, parser):
    result={'productId':item['productId'],'sku':item['sku'],'offers':[]}
    if not valid_product_url(item.get('url')): return {**result,'error':'invalid_url'}
    page=await context.new_page()
    responses=[]
    if hasattr(page,'on'):
        def on_response(response):
            if response.request.resource_type=='document': responses.append(response.status)
        page.on('response',on_response)
    quote=result
    try:
        quote=await asyncio.wait_for(parser.scrape_product(page,item),timeout=180)
        quote['collectedAt']=datetime.now(timezone.utc).isoformat()
        # Native PricePilot inferred ownership by matching prices. Keep identity unverified.
        quote['holdingBuybox']=False
        quote.pop('holdingBuyboxVerified',None)
        # Keep original "possibly ours" exclusions; they are not ownership proof.
    except asyncio.TimeoutError: quote={**result,'error':'navigation_timeout'}
    except Exception: quote={**result,'error':'collector_error'}
    finally:
        def safe_url(url):
            p=urlsplit(url or '')
            return urlunsplit((p.scheme,p.netloc,p.path,'',''))
        diagnostics={'http_status':responses[-1] if responses else None,'requested_url':safe_url(item.get('url'))}
        try:
            diagnostics.update(final_url=safe_url(page.url),page_title=(await page.title())[:160],
                buybox_cards=await page.locator('[data-ref="buybox-offer"]').count())
        except Exception: pass
        if isinstance(quote.get('variantControls'),int): diagnostics['variant_controls']=quote['variantControls']
        quote['diagnostics']=diagnostics
        await page.close()
    return quote

# 仅对「瞬时失败」重试一次：页面超时/加载失败/服务端 5xx 与 429。
# 验证页（site_challenge）与登录失效不重试——重试解决不了，还会加剧风控。
# 重试只是用新页面重放同一请求，不做任何指纹规避或验证绕过。
RETRYABLE_ERRORS = {'navigation_timeout', 'collector_error', 'blocked_or_load_failed'}
RETRYABLE_HTTP = re.compile(r'http_(?:429|500|502|503|504)')

def is_retryable(error):
    return error in RETRYABLE_ERRORS or (isinstance(error, str) and bool(RETRYABLE_HTTP.fullmatch(error)))

# 「确认被验证拦截」的错误：连续出现说明该页面当前无法访问，应停止重复访问。
# 注意与网络加载失败（blocked_or_load_failed / navigation_timeout）区分——后者可重试。
INTERCEPT_ERRORS = {'site_challenge', 'http_403'}
CHALLENGE_STOP_AFTER = 2

async def round_once(context, api, parser, challenged=None):
    challenged = {} if challenged is None else challenged
    batch=await asyncio.to_thread(api.call,'/api/collector/products')
    items=batch.get('items',[])
    leased={item['productId'] for item in items}
    semaphore=asyncio.Semaphore(2)
    successful=0;blocked=0
    async def worker(item):
        nonlocal successful,blocked
        async with semaphore:
            sku=item.get('sku')
            # 跨轮拦截：同一 SKU 连续两轮被验证页/403 拦截后，本机不再访问该商品页，
            # 直接放弃租约并提示人工恢复（在网页重新点击「立即采集」或重启采集器）。
            if challenged.get(sku,0) >= CHALLENGE_STOP_AFTER:
                blocked+=1
                print(f"SKU {sku}: 已连续 {CHALLENGE_STOP_AFTER} 轮被验证页/403 拦截，本机停止访问该商品页；"
                      f"如需恢复请在网页重新点击「立即采集」（或重启常态采集）。",flush=True)
                return
            quote=None
            for attempt in range(2):
                quote=await scrape(context,item,parser)
                error=quote.get('error')
                if error is None or not is_retryable(error) or attempt==1: break
                await asyncio.sleep(2+attempt*3)
            # 判据：仅 site_challenge / http_403 计入跨轮拦截；网络加载失败不计。
            got=quote.get('error')
            if got in INTERCEPT_ERRORS:
                challenged[sku]=challenged.get(sku,0)+1
            else:
                challenged.pop(sku,None)
            try:
                response=await asyncio.to_thread(api.call,'/api/collector/offers',{'items':[quote]})
            except Exception:
                # 单个 SKU 回传失败不该拖垮整轮：其余任务继续，未回传的租约稍后统一释放。
                # 只输出受控分类，不输出异常正文（异常可能含反射内容或敏感配置）。
                print(f"SKU {sku}: 回传失败（服务未接受），其余任务继续。",flush=True)
                return
            if response.get('processed') or response.get('skipped'): leased.discard(item['productId'])
            accepted=response.get('successful')==1 and not quote.get('error') and not quote.get('variantSwitchFailed') and quote.get('buyboxPrice') is not None
            if accepted: successful+=1
            if quote.get('error') in ('site_challenge','blocked_or_load_failed'): blocked+=1
            d=quote.get('diagnostics') or {}
            error=quote.get('error')
            reason=error if error in ('site_challenge','blocked_or_load_failed','navigation_timeout','collector_error','unstable_price','invalid_url') or isinstance(error,str) and re.fullmatch(r'http_\d{3}',error) else 'price_not_found'
            if response.get('skipped'): reason='任务已暂停或租约已失效'
            result=('Buybox R '+str(quote['buyboxPrice'])+' 已回传') if accepted else f"未取得报价：{reason} · HTTP {d.get('http_status') or '—'} · 报价卡 {d.get('buybox_cards','—')}"
            print(f"SKU {item['sku']}: {result}",flush=True)
    tasks=[asyncio.create_task(worker(item)) for item in items]
    try:
        if tasks: await asyncio.wait_for(asyncio.gather(*tasks),timeout=600)
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if leased: await asyncio.to_thread(api.call,'/api/collector/release',{'product_ids':list(leased)})
    return len(items), successful, blocked

async def run(args):
    from playwright.async_api import async_playwright
    profile=Path(args.profile).expanduser().resolve()
    api=None
    if not args.login:
        key=os.environ.get('PP_COLLECTOR_KEY') or getpass.getpass('选品系统服务访问密钥（输入不显示，输入后按回车）：')
        if not key.strip(): raise ValueError('缺少服务访问密钥')
        api=Api(BASE,key)
        print('正在核对选品系统连接…',flush=True)
        readiness=await asyncio.to_thread(api.call,'/api/collector/status')
        if readiness.get('ready') is not True: raise RuntimeError('系统连接未就绪，尚未打开采集浏览器')
        print('服务认证和自营店铺检查通过，正在启动采集浏览器。',flush=True)
    lock=singleton(profile)
    try:
        async with async_playwright() as playwright:
            context=await playwright.chromium.launch_persistent_context(
                str(profile),headless=False,locale='en-ZA',timezone_id='Africa/Johannesburg',
                viewport={'width':1366,'height':768})
            try:
                if args.login:
                    page=await context.new_page()
                    await page.goto('https://www.takealot.com',wait_until='domcontentloaded')
                    if getattr(args,'stop_file',None):
                        print('请在浏览器完成登录和南非配送地址，再点击“完成登录并保存”。',flush=True)
                        while not Path(args.stop_file).exists(): await asyncio.sleep(.5)
                    else: await asyncio.to_thread(input,'在浏览器完成登录和南非配送地址设置后，按回车保存并关闭：')
                    return
                parser=parser_module()
                _CURRENT['profile']=str(profile)
                # 每次运行一个独立 run_id：GUI 据此区分「本次运行」与上一次运行的残留状态
                _CURRENT['run_id']=uuid.uuid4().hex[:12]
                challenged={}
                round_no=0;wait=30;idle_noticed=False
                while True:
                    if getattr(args,'stop_file',None) and Path(args.stop_file).exists():break
                    try:
                        round_no+=1;_CURRENT['round']=round_no
                        # 轮次开始先写 running：在途阶段不应继续显示上一轮的汇总结果
                        _CURRENT['stage']='round_start'
                        write_status('running',claimed=0,successful=0,failed=0)
                        cycle=asyncio.create_task(round_once(context,api,parser,challenged))
                        try:
                            while not cycle.done():
                                if getattr(args,'stop_file',None) and Path(args.stop_file).exists():
                                    cycle.cancel();break
                                await asyncio.sleep(.5)
                            claimed,successful,blocked=await cycle
                        finally:
                            if not cycle.done(): cycle.cancel()
                            await asyncio.gather(cycle,return_exceptions=True)
                        if claimed==0:
                            # 空闲：只在刚进入空闲时提示一次，避免每轮重复刷同一句话
                            if not idle_noticed:
                                print('暂无到期任务（本机保持待命）；如需立即采集，请在网页点击 SKU「立即采集」或开启常态采集。',flush=True)
                                idle_noticed=True
                            write_status('idle',claimed=0,successful=0,failed=0)
                            wait=60
                        else:
                            idle_noticed=False
                            failed=claimed-successful
                            print(f'ROUND_DONE: 第 {round_no} 轮 · 领取 {claimed} 个 / 成功回传 {successful} 个 / 失败 {failed} 个',flush=True)
                            if successful>0:
                                write_status('collecting_ok',claimed=claimed,successful=successful,failed=failed)
                                wait=30
                            elif blocked>=claimed:
                                write_status('challenged',claimed=claimed,successful=0,failed=failed)
                                wait=300
                            else:
                                write_status('failed',claimed=claimed,successful=0,failed=failed)
                                wait=60
                    except asyncio.CancelledError: break
                    except Exception as exc:
                        # 只输出受控分类，绝不输出异常正文：异常可能含反射内容或敏感配置。
                        label='服务连接被拒绝或超时' if isinstance(exc,RuntimeError) else '页面或服务暂不可用'
                        print(f'本轮失败：{label}；保留原价，稍后重试。',flush=True)
                        write_status('failed',error=label)
                        wait=60
                    if args.once: break
                    for _ in range(wait*2):
                        if getattr(args,'stop_file',None) and Path(args.stop_file).exists():break
                        await asyncio.sleep(.5)
            finally: await context.close()
    finally: lock.close()

def main():
    cli=argparse.ArgumentParser(description='原PricePilot解析核心、本机持久浏览器、网页任务调度')
    cli.add_argument('--profile',default=str(ROOT/'browser_profile'),help='原PricePilot browser_profile绝对路径')
    cli.add_argument('--login',action='store_true',help='仅打开持久浏览器进行人工登录与配送设置')
    cli.add_argument('--once',action='store_true')
    cli.add_argument('--stop-file',help=argparse.SUPPRESS)
    args=cli.parse_args()
    try: asyncio.run(run(args))
    except KeyboardInterrupt: pass
    except Exception as exc: print(str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '浏览器未能启动，请检查安装和档案占用。')

if __name__=='__main__': main()
