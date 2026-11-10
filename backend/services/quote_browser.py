"""Durable quote browser profile. Login/location state stays on the collector host."""
import asyncio
import os
import signal
import threading
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from database import DB_DIR

_lock = threading.Lock()
_display_lock = threading.Lock()
_display = None
# 等浏览器锁的上限。原来是无期限 acquire()：上一个任务卡死时，后面每个任务都会
# 永远等在这把锁上，采集循环整条冻住，而且心跳还在刷新，界面显示「运行中」。
# 与其无限等，不如如实报「忙」，让这一条记成失败、让循环继续转。
LOCK_WAIT_SECONDS = 90

# ---- 常驻内存相关的浏览器策略（2026-11-10 收内存）--------------------------
# 背景：Railway 账单里内存占 54%，容器常驻基线约 1.4GB，一轮 40 个 SKU 的采集
# 里 Chromium 常年 ~1.0GB。采集只需要 DOM 文本和几个 data-ref 锚点，用不到图片
# 解码、字体栅格、音视频、扩展、后台同步这些东西——它们每一项都按「每渲染进程
# 一份」吃内存，而采集一页只跑十几秒，省下来的全是纯利。
#
# 这里只动「浏览器怎么跑」，不动任何解析/买盒判定/调价逻辑。
LEAN_BROWSER_ARGS = (
    '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run',
    '--disable-extensions', '--disable-default-apps', '--disable-component-update',
    '--disable-sync', '--disable-background-networking', '--no-default-browser-check',
    '--metrics-recording-only', '--mute-audio', '--disable-breakpad',
    '--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints',
    # 采集只读 DOM，不需要 Chromium 默认那套 1.4G 的 V8 老生代预算。
    # 上限压到 256M：页面脚本正常跑，但不会把整台机器的内存吃掉。
    '--js-flags=--max-old-space-size=256',
    # 渲染进程数封顶。单页采集最多 1 个渲染进程 + 1 个 GPU/工具进程，
    # 不封顶时站内的广告/统计 iframe 会各起一个渲染进程。
    '--renderer-process-limit=2',
)

# 采集时可以直接丢弃的资源类型。图片/字体/音视频对价格解析零贡献。
# 默认拦截（BUYBOX_BLOCK_MEDIA=0 可关掉），用于压内存与出网流量。
BLOCKED_RESOURCE_TYPES = frozenset({'image', 'media', 'font'})

# 空闲回收：锁空闲且没有采集任务在飞时，若该 profile 还有 Chromium 进程活着，
# 超过这个秒数就强杀。context.close() 在 CLEANUP_TIMEOUT 内关不掉时会留活口，
# 而一个活着的有头 Chromium 就是 1GB 起——这条是「无任务时仍常驻」的兜底。
IDLE_CLOSE_SECONDS = int(os.environ.get('BUYBOX_BROWSER_IDLE_SECONDS', '300') or 300)

# 关闭动作的上限。采集超时被取消时，收尾代码就在「已经被取消」的协程里跑：
# 这时如果 context.close() 也跟着卡住（浏览器进程无响应），整个取消流程永远走不完，
# 线程、node 驱动、Chromium 全留在那儿——2026-10-21 线程耗尽就是这么来的。
# 关不掉也要认，宁可留一个僵尸进程让下次重启清掉，也不能把线程池和 _lock 锁死。
CLEANUP_TIMEOUT = 10

# 正在飞行的采集所用的浏览器 profile。看门狗在 JOB_TIMEOUT 后放弃卡死任务时，
# 需要直接杀掉这个浏览器进程树——被卡住的线程卡在 Playwright 的同步调用里，
# asyncio 取消/ future.cancel() 都无法中断真实线程，只有浏览器进程死了，
# 调用才会报错返回、quote_context 才会放 _lock、线程才会归还池。
#
# 为什么记录 profile 而不是 PID：launch_persistent_context 返回的 BrowserContext
# 的 .browser 恒为 None（persistent context 没有独立 Browser 对象），拿不到
# .process.pid。改为按 Chromium 稳定的启动参数 --user-data-dir=<profile> 反查
# 进程，既精确（只杀我们自己的浏览器）又不依赖 Playwright 内部 API。
_current_browser_profile = None
_current_browser_lock = threading.Lock()


def _set_current_browser_profile(profile):
    global _current_browser_profile
    with _current_browser_lock:
        _current_browser_profile = profile


def current_browser_profile():
    with _current_browser_lock:
        return _current_browser_profile


def _proc_entries():
    try:
        return [e for e in os.listdir('/proc') if e.isdigit()]
    except OSError:
        return []


def _child_pids(pid, entries=None):
    """只返回 pid 的直接子进程（通过 /proc），绝不触碰应用自身进程树。

    非 Linux（本地 macOS 开发）没有 /proc，返回空列表——本机采集器走的是
    tools/native-collector，不经过这里；云端是 Linux，/proc 可用。
    """
    kids = []
    for entry in entries if entries is not None else _proc_entries():
        try:
            with open(f'/proc/{entry}/stat') as fh:
                stat = fh.read()
            # comm 字段形如 "(chrome)"，可能带空格/括号，所以从最后一个 ')' 之后切。
            tail = stat[stat.rfind(')') + 2:].split()
            # tail: state ppid ...；ppid 是第 2 个字段。
            if len(tail) >= 2 and int(tail[1]) == pid:
                kids.append(int(entry))
        except (OSError, ValueError, IndexError):
            continue
    return kids


def _pids_by_profile(profile):
    """按 Chromium 的 --user-data-dir 参数找出属于该 profile 的进程。

    Playwright 可能把 user_data_dir 转成绝对路径再传给 Chromium，配置里也可能写
    相对路径或走 symlink，所以原值 / abspath / realpath 三种写法都认。三者都指向
    同一个采集 profile，不会因此误杀别的浏览器。
    """
    if not profile or not os.path.isdir('/proc'):
        return []
    variants = {profile, os.path.abspath(profile), os.path.realpath(profile)}
    needles = [('--user-data-dir=' + v).encode() for v in variants if v]
    pids = []
    for entry in _proc_entries():
        try:
            with open(f'/proc/{entry}/cmdline', 'rb') as fh:
                cmdline = fh.read()
            if any(needle in cmdline.split(b'\0') for needle in needles):
                pids.append(int(entry))
        except (OSError, ValueError):
            continue
    return pids


# Windows 没有 SIGKILL；生产在 Railway(Linux)、开发在 macOS，都可用。
_KILL_SIGNAL = getattr(signal, 'SIGKILL', signal.SIGTERM)


def kill_current_browser():
    """强制终止当前在飞的采集浏览器进程树。

    由采集看门狗在任务被判定卡死（超过 JOB_TIMEOUT）时调用。只杀 --user-data-dir
    指向当前采集 profile 的 Chromium 进程及其子进程，不触碰应用自身或同机其他浏览器。
    无在飞浏览器、进程已退出、或非 Linux 环境时为 no-op（返回 False）。
    """
    profile = current_browser_profile()
    if not profile:
        return False
    entries = _proc_entries()
    roots = _pids_by_profile(profile)
    if not roots:
        return False
    targets = set(roots)
    for pid in roots:
        targets.update(_child_pids(pid, entries))
    for pid in targets:
        try:
            os.kill(pid, _KILL_SIGNAL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return True


def _kill_profile(profile):
    """杀掉指定 profile 的 Chromium 进程树（含子进程）。返回杀掉的目标数。

    kill_current_browser / 尾部回收 / 空闲看门狗三条路径共用这一段，保证「只杀
    带 --user-data-dir=<采集 profile> 的浏览器」，不碰应用自身和同机其他浏览器。
    """
    if not profile:
        return 0
    entries = _proc_entries()
    roots = _pids_by_profile(profile)
    if not roots:
        return 0
    targets = set(roots)
    for pid in roots:
        targets.update(_child_pids(pid, entries))
    killed = 0
    for pid in targets:
        try:
            os.kill(pid, _KILL_SIGNAL)
            killed += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return killed


def kill_current_browser():
    """强制终止当前在飞的采集浏览器进程树。

    由采集看门狗在任务被判定卡死（超过 JOB_TIMEOUT）时调用。只杀 --user-data-dir
    指向当前采集 profile 的 Chromium 进程及其子进程，不触碰应用自身或同机其他浏览器。
    无在飞浏览器、进程已退出、或非 Linux 环境时为 no-op（返回 False）。
    """
    return _kill_profile(current_browser_profile()) > 0


async def _reap_profile(profile, grace=3.0):
    """context.close() 之后核一遍进程，还活着就强杀。

    正常情况第一次检查就是空的（Chromium 秒退），这条只在「关不掉的浏览器」
    场景下才动手：那种残留会以 1GB 常驻的形式挂在账单上，而且下一次采集还会
    因为 profile 被占而启动失败。
    """
    if not profile or not os.path.isdir('/proc'):
        return False
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _pids_by_profile(profile):
            return False
        await asyncio.sleep(0.5)
    killed = _kill_profile(profile)
    if killed:
        print(f'[quote_browser] 关闭后仍有 {killed} 个浏览器进程未退出，已强制回收', flush=True)
    return killed > 0


def _headless_override():
    """读 BUYBOX_BROWSER_HEADLESS：True/False 表示显式指定，None 表示 auto。"""
    mode = (os.environ.get('BUYBOX_BROWSER_HEADLESS') or 'auto').strip().lower()
    if mode in ('1', 'true', 'yes', 'on', 'headless'):
        return True
    if mode in ('0', 'false', 'no', 'off', 'headed'):
        return False
    return None


def browser_headless(display):
    """这次浏览器要不要跑无头模式。

    auto（默认）：有 Xvfb/图形会话就保持原来的有头模式，没有就无头——与改动前
    的行为完全一致，绝不在没人确认的情况下改线上形态。
    显式覆盖用 BUYBOX_BROWSER_HEADLESS=1/0：无头比有头少掉合成器与软件栅格
    那一整块内存，是收内存最大的单项，但它同时会影响反爬指纹，所以做成开关，
    先上线观察成功率，异常时改环境变量即可回退，不用回滚代码。
    """
    forced = _headless_override()
    if forced is not None:
        return forced
    return not bool(display)


_last_release_at = time.monotonic()
_idle_guard_started = False
_idle_guard_lock = threading.Lock()


def _collector_profile():
    return os.environ.get('BUYBOX_BROWSER_PROFILE', os.path.join(DB_DIR, 'buybox_browser_profile'))


def _reap_idle_browser():
    """Own the browser lock throughout the idle decision and process cleanup."""
    if not _lock.acquire(blocking=False):
        return 0
    try:
        if time.monotonic() - _last_release_at < IDLE_CLOSE_SECONDS:
            return 0
        return _kill_profile(_collector_profile())
    finally:
        _lock.release()


def _idle_guard_loop():
    """空闲看门狗：没有采集在飞、且锁空闲超过 IDLE_CLOSE_SECONDS，就清掉残留浏览器。

    只读进程表，不碰任何采集状态；真动手时也只杀自己 profile 的进程。
    """
    while True:
        time.sleep(30)
        try:
            killed = _reap_idle_browser()
            if killed:
                print(f'[quote_browser] 空闲超过 {IDLE_CLOSE_SECONDS} 秒，回收残留浏览器进程 {killed} 个', flush=True)
        except Exception as exc:  # noqa: BLE001 看门狗绝不能自己把进程搞崩
            print(f'[quote_browser] 空闲回收异常：{type(exc).__name__}: {exc}', flush=True)


def _ensure_idle_guard():
    global _idle_guard_started
    with _idle_guard_lock:
        if _idle_guard_started:
            return
        _idle_guard_started = True
        threading.Thread(target=_idle_guard_loop, name='quote-browser-idle-guard', daemon=True).start()


async def _route_drop_heavy(route):
    """拦掉对解析零贡献的资源：图片/字体/音视频。其余请求原样放行。"""
    try:
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:  # noqa: BLE001 请求已在关闭中，忽略即可
        pass


class CollectorBusy(RuntimeError):
    """浏览器被上一个任务占着；这不是页面问题，也不该重试到天荒地老。"""


async def _bounded(coro, what):
    """跑一个收尾动作，但绝不让它无限等下去。

    CancelledError 继承自 BaseException，不会被这里的 `except Exception` 吞掉——
    外层取消信号照常向上传播，只是我们不再替卡死的浏览器陪葬。
    """
    try:
        await asyncio.wait_for(coro, timeout=CLEANUP_TIMEOUT)
    except asyncio.TimeoutError:
        print(f'[quote_browser] {what} 超过 {CLEANUP_TIMEOUT} 秒未完成，放弃等待', flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f'[quote_browser] {what} 失败：{type(exc).__name__}: {exc}', flush=True)


async def _acquire_lock(timeout):
    """异步等锁，不用 asyncio.to_thread。

    原来是 `to_thread(_lock.acquire, True, 90)`：每次采集都要借一个线程去阻塞，
    而线程正是 2026-10-21 被耗尽的资源（`can't start new thread` 之后连 DNS 都
    解析不了，图片代理全线 500）。这里改成非阻塞试探 + 异步睡眠，一次线程都不借。
    """
    deadline = time.monotonic() + timeout
    while True:
        if _lock.acquire(blocking=False):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.2)

def browser_display():
    """Local virtual display only; no VNC server or public listening port."""
    global _display
    if os.environ.get('DISPLAY'):
        return os.environ['DISPLAY']
    if not shutil.which('Xvfb'):
        return None
    with _display_lock:
        if _display is None or _display.poll() is not None:
            _display = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1366x768x24', '-nolisten', 'tcp'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time
            for _ in range(30):
                if os.path.exists('/tmp/.X11-unix/X99'): break
                if _display.poll() is not None: raise RuntimeError('云端显示器启动失败')
                time.sleep(.1)
        return ':99'

@asynccontextmanager
async def quote_context(wait=True, wait_seconds=None, block_media=False):
    """拿采集浏览器。wait_seconds 给交互式调用用：宁可 25 秒后说「忙」，
    也不要让用户在新建产品页上干等 90 秒——采集循环每 5 分钟就占一次锁。

    block_media=True 时丢弃图片/字体/音视频请求：买盒采集只读 DOM，这些请求
    对价格零贡献，却在渲染进程里各占一份解码缓冲。交互式看页面（cloud_browser
    截图给用户设提货点）必须保持 False，否则用户看到的是一堆破图。
    """
    held = False
    limit = LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if wait:
        held = await _acquire_lock(limit)
        if not held:
            raise CollectorBusy(f'采集浏览器被上一个任务占用超过 {limit} 秒，本轮跳过')
    elif not _lock.acquire(blocking=False):
        raise CollectorBusy('采集浏览器忙，请稍后再打开')
    else:
        held = True
    playwright = context = None
    _ensure_idle_guard()
    try:
        profile = os.environ.get('BUYBOX_BROWSER_PROFILE', os.path.join(DB_DIR, 'buybox_browser_profile'))
        os.makedirs(profile, exist_ok=True)
        # 从这一刻起，这个 profile 对应的浏览器可被看门狗按 --user-data-dir 定位并终止。
        _set_current_browser_profile(profile)
        # 显式无头时不拉 Xvfb：那一台虚拟显示器本身也要几十 MB，无头模式完全用不到它。
        if _headless_override():
            display = None
        else:
            display = await asyncio.to_thread(browser_display)
        # 容器里 launch_persistent_context 偶发 TargetClosedError：上一次进程被
        # 重启杀掉后，profile 目录里会留下 SingletonLock/Socket，下一次启动直接
        # 失败；/dev/shm 只有 64MB 也会让渲染进程崩掉。两者都在这里兜住，
        # 并重试一次——采集是自动调价的上游，起不来浏览器就等于全线停摆。
        #
        # 注意：重试只包住「启动」这一段。yield 之后的异常是调用方自己的（采集逻辑
        # 出错），绝不能在这里被捕获重试——那会把整轮采集悄悄重跑一遍。
        args = list(LEAN_BROWSER_ARGS)
        for attempt in range(2):
            await asyncio.to_thread(_clear_singleton, profile)
            try:
                playwright, context = await _launch(profile, display, args)
                break
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                raise
        if block_media:
            # 路由挂在 context 上，页面自己开的 iframe/worker 请求同样被拦。
            try:
                await context.route('**/*', _route_drop_heavy)
            except Exception as exc:  # noqa: BLE001 拦不住不影响采集，只是少赚内存
                print(f'[quote_browser] 资源拦截设置失败，按原样继续采集：{type(exc).__name__}', flush=True)
        try:
            yield context
        finally:
            try:
                await _bounded(context.close(), '关闭浏览器上下文')
            finally:
                await _bounded(playwright.stop(), '停止 Playwright 驱动')
                # 收尾核账：context.close() 超时或静默失败时进程还活着，这里强杀。
                # 放在 playwright.stop() 之后做，避免杀进程和驱动退出互相打断。
                await _bounded(_reap_profile(profile), '回收关闭后残留的浏览器进程')
                # 释放宽限：前一个 Chromium 刚 stop 就立刻启动下一个会偶发
                # TargetClosedError（线上实测约 7% 的采集因此失败）。这里在**仍持有
                # _lock** 时多等一会儿，确保上一个进程真的退出、把 profile 交出来，
                # 再让下一轮进来。
                await asyncio.sleep(1.2)
    finally:
        # 上下文已退出：这个 profile 不再属于「正在飞行的采集」。清掉它，否则看门狗
        # 下一次放弃卡死任务时，可能杀到后续正常采集刚启动的同一个 profile 浏览器。
        _set_current_browser_profile(None)
        global _last_release_at
        _last_release_at = time.monotonic()
        if held:
            _lock.release()


async def _launch(profile, display, args, headless=None):
    """启动持久化上下文。返回 (playwright, context)，两者都由调用方负责关闭。

    headless=None 时按 BUYBOX_BROWSER_HEADLESS 与有无显示器自动判定
    （见 browser_headless），默认行为与改动前完全一致。
    """
    from playwright.async_api import async_playwright
    headless_on = browser_headless(display) if headless is None else bool(headless)
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile, headless=headless_on, args=args,
            env={**os.environ, **({'DISPLAY': display} if display and not headless_on else {})},
            locale='en-ZA', timezone_id='Africa/Johannesburg',
            viewport={'width': 1366, 'height': 768})
    except BaseException:
        # 启动失败要把 playwright 驱动收掉，否则每失败一次就漏一个 node 进程。
        # 超时取消同样必须收尾；CancelledError 不属于 Exception。
        await _bounded(playwright.stop(), '停止启动未完成的 Playwright 驱动')
        raise
    return playwright, context


def _clear_singleton(profile):
    """清掉上次进程残留的 profile 单例锁。只在锁文件属于已死进程时才有害，
    但这里无法可靠判断 pid 归属（容器重启后 pid 会复用），直接删更安全：
    我们持有全局 _lock，此刻确定没有别的采集在用这个 profile。"""
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        path = os.path.join(profile, name)
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass
