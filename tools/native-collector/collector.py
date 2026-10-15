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
from datetime import datetime, timezone
from urllib import request, error
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
BASE = 'https://takealot-selector-production.up.railway.app'

import subprocess
APP_DIR = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'TakealotCollector'
try: APP_DIR.mkdir(parents=True, exist_ok=True)
except Exception: pass
STATUS_FILE = APP_DIR / 'collector_status.json'

def write_status(state, **extra):
    payload = {'state': state, 'ts': datetime.now(timezone.utc).isoformat()}
    payload.update(extra)
    try: STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    except Exception: pass

def notify(title, message):
    """Best-effort Windows toast; never raises. Only informative, no secrets."""
    try:
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
            "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$l=$t.GetElementsByTagName('text');"
            "$l.Item(0).AppendChild($t.CreateTextNode('{t}')) | Out-Null;"
            "$l.Item(1).AppendChild($t.CreateTextNode('{m}')) | Out-Null;"
            "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('TakealotCollector');"
            "$n.Show($t)"
        ).format(t=title.replace("'", "''"), m=message.replace("'", "''"))
        subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=15)
    except Exception: pass

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
    def __init__(self, base, key, autonomous=False):
        parsed = urlsplit(base)
        if parsed.scheme != 'https' or parsed.hostname != urlsplit(BASE).hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('仅连接已部署的选品系统 HTTPS 后端')
        self.base, self.key = base.rstrip('/'), key.strip()
        self.autonomous = bool(autonomous)
        self.opener = request.build_opener(NoRedirect)

    def call(self, path, body=None):
        if path not in ('/api/collector/status','/api/collector/products','/api/collector/offers','/api/collector/release'):
            raise ValueError('采集器只能使用行情任务接口')
        headers = {'X-Collector-Key': self.key, 'X-Collector-Client':'local_collector',
                   'X-Store-ID':'default-store', 'Content-Type':'application/json'}
        if self.autonomous:
            headers['X-Collector-Autonomous'] = '1'
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

async def is_auth_expired(page):
    """识别 Takealot 登录墙（会话过期/已退出）。

    我们只用「本人已登录会话」访问，不做验证码破解、不做浏览器指纹伪装。
    若被识别为已退出登录（跳转到登录页或页面出现登录入口），则明确标记，
    提示用户用本人的会话重新登录，而不是继续空跑或绕验证。"""
    try:
        url=(page.url or '').lower()
        if any(k in url for k in ('/login','signin','sign-in','/account/login','account/signin')):
            return True
        title=(await page.title() or '')
        if re.search(r'sign[\s-]?in|log[\s-]?in|please.?sign',title,re.I):
            return True
        if await page.query_selector('input[type="password"]') and \
           await page.query_selector('a[href*="login"], form[action*="login"], [class*="login"]'):
            return True
    except Exception:
        pass
    return False

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
        quote['diagnostics']=diagnostics
        try:
            # 会话失效优先于 site_challenge：登录墙说明需要本人重新登录，而非被风控
            if not quote.get('buyboxPrice') and await is_auth_expired(page):
                quote['error']='auth_expired'
        except Exception:
            pass
        await page.close()
    return quote

async def round_once(context, api, parser, auth_event=None, state=None):
    batch=await asyncio.to_thread(api.call,'/api/collector/products')
    items=batch.get('items',[])
    if not items:
        if getattr(api,'autonomous',False):
            print('已连接选品系统：当前没有「到期且未暂停」的目录 SKU 可采集（可能全部已暂停）。',flush=True)
        else:
            print('已连接选品系统，暂无到期任务；请在网页点击SKU立即采集或开启常态采集。',flush=True)
    leased={item['productId'] for item in items}
    semaphore=asyncio.Semaphore(2)
    blocked=0
    async def worker(item):
        nonlocal blocked
        async with semaphore:
            quote=await scrape(context,item,parser)
            response=await asyncio.to_thread(api.call,'/api/collector/offers',{'items':[quote]})
            if response.get('processed') or response.get('skipped'): leased.discard(item['productId'])
            accepted=response.get('successful')==1 and not quote.get('error') and not quote.get('variantSwitchFailed') and quote.get('buyboxPrice') is not None
            if accepted and state is not None: state['last_success_ts']=time.time()
            d=quote.get('diagnostics') or {}
            error=quote.get('error')
            if error=='auth_expired':
                if auth_event is not None: auth_event.set()
                print('AUTH_EXPIRED: 登录会话已失效；请打开 GUI 点「登录 / 配送设置」重新登录后再次采集。',flush=True)
            reason=error if error in ('site_challenge','blocked_or_load_failed','auth_expired','navigation_timeout','collector_error','unstable_price','invalid_url') or isinstance(error,str) and re.fullmatch(r'http_\d{3}',error) else 'price_not_found'
            if response.get('skipped'): reason='任务已暂停或租约已失效'
            if error in ('site_challenge','blocked_or_load_failed','auth_expired'): blocked+=1
            result=('Buybox R '+str(quote['buyboxPrice'])+' 已回传') if accepted else f"未取得报价：{reason} · HTTP {d.get('http_status') or '—'} · 报价卡 {d.get('buybox_cards','—')}"
            print(f"SKU {item['sku']}: {result}",flush=True)
    tasks=[asyncio.create_task(worker(item)) for item in items]
    try:
        if tasks: await asyncio.wait_for(asyncio.gather(*tasks),timeout=600)
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if leased: await asyncio.to_thread(api.call,'/api/collector/release',{'product_ids':list(leased)})
    return len(items), (blocked==len(items) and len(items)>0)

async def run(args):
    from playwright.async_api import async_playwright
    profile=Path(args.profile).expanduser().resolve()
    api=None
    if not args.login:
        key=os.environ.get('PP_COLLECTOR_KEY') or getpass.getpass('选品系统服务访问密钥（输入不显示，输入后按回车）：')
        if not key.strip(): raise ValueError('缺少服务访问密钥')
        api=Api(BASE,key,autonomous=not args.once)
        print('正在核对选品系统连接…',flush=True)
        readiness=await asyncio.to_thread(api.call,'/api/collector/status')
        if readiness.get('ready') is not True: raise RuntimeError('系统连接未就绪，尚未打开采集浏览器')
        print('服务认证和自营店铺检查通过，正在启动采集浏览器。',flush=True)
    lock=singleton(profile)
    try:
        async with async_playwright() as playwright:
            launch_args=dict(headless=args.headless,locale='en-ZA',timezone_id='Africa/Johannesburg',
                viewport={'width':1366,'height':768})
            if args.stealth:
                launch_args['args']=['--disable-blink-features=AutomationControlled']
                launch_args['ignore_default_args']=['--enable-automation']
            context=await playwright.chromium.launch_persistent_context(str(profile),**launch_args)
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
                auth_event=asyncio.Event()
                consecutive_block=0
                wait=30
                while True:
                    if getattr(args,'stop_file',None) and Path(args.stop_file).exists():break
                    try:
                        state={'last_success_ts':None}
                        cycle=asyncio.create_task(round_once(context,api,parser,auth_event,state))
                        try:
                            while not cycle.done():
                                if getattr(args,'stop_file',None) and Path(args.stop_file).exists():
                                    cycle.cancel();break
                                await asyncio.sleep(.5)
                            count,all_blocked=await cycle
                        finally:
                            if not cycle.done(): cycle.cancel()
                            await asyncio.gather(cycle,return_exceptions=True)
                        if auth_event.is_set():
                            write_status('auth_expired')
                            notify('Takealot 采集器', '登录会话已失效，请打开 GUI 点「登录 / 配送设置」重新登录后再次常态采集。')
                            print('登录会话已失效，停止采集；请重新登录后再启动常态采集。',flush=True)
                            break
                        # 容错：连续整轮被风控/拦截时拉长等待，避免加剧封禁
                        if all_blocked and count>0:
                            consecutive_block+=1
                            write_status('challenged', consecutive_block=consecutive_block)
                        else:
                            consecutive_block=0
                            write_status('collecting_ok' if count>0 else 'idle', last_success_ts=state.get('last_success_ts'))
                        wait=300 if consecutive_block>=2 else 30
                    except asyncio.CancelledError: break
                    except Exception as exc:
                        message=str(exc) if isinstance(exc,RuntimeError) else '页面或服务暂不可用'
                        print(f'本轮失败：{message}；保留原价，稍后重试。',flush=True)
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
    cli.add_argument('--headless',action='store_true',help='无界面后台运行（用于无人值守常态采集）')
    cli.add_argument('--stealth',action='store_true',help='实验性：去除浏览器自动化特征（仅 headless 生效），用于评估无头风控；默认关闭')
    cli.add_argument('--stop-file',help=argparse.SUPPRESS)
    args=cli.parse_args()
    try: asyncio.run(run(args))
    except KeyboardInterrupt: pass
    except Exception as exc: print(str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '浏览器未能启动，请检查安装和档案占用。')

if __name__=='__main__': main()
