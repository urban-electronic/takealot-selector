"""后台无人值守采集器守护进程。

职责（仅行情采集，绝不调价/采购）：
  1. 从本机 DPAPI 解密已保存的服务密钥（与 GUI 同一份 service-key.dpapi），通过
     PP_COLLECTOR_KEY 环境变量注入采集器子进程；密钥只在内存中使用，不落盘、不打印。
  2. 以「持续模式」启动 collector.py（可选 --headless / --stealth），使本机长驻领租约、云端 worker 自动退避让路。
  3. 作为 supervisor：采集器异常退出时自动重启（退避 5s）；但若采集器因「登录会话失效(auth_expired)」退出，
     则停止自动重启并提示人工重新登录，避免无限重启循环。stop-file 存在时优雅退出。
  4. 提供 Windows 计划任务安装/卸载，实现「登录即后台常驻、失败自动重启」。

用法（需在本机以当前 Windows 用户运行，DPAPI 才能解密）：
  python run_background.py start [--headless] [--stealth]   启动守护（前台，可配合计划任务）
  python run_background.py stop                             发送停止信号（写 stop-file）
  python run_background.py install [--headless] [--stealth] 注册计划任务（登录触发 + 失败重启）
  python run_background.py uninstall                        删除计划任务
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'TakealotCollector'
STOP_FILE = STATE / 'collector.background.stop'
LOG_FILE = STATE / 'collector.background.log'
PROFILE_FILE = STATE / 'profile-path.txt'
COLLECTOR_STATUS = STATE / 'collector_status.json'
TASK_NAME = 'TakealotCollector'


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('本机不是 Windows，无法使用 DPAPI 解密密钥')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError('Windows 加密存储不可用，服务密钥未保存')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def get_profile():
    if PROFILE_FILE.exists():
        p = PROFILE_FILE.read_text(encoding='utf-8').strip()
        if p:
            return Path(p)
    return STATE / 'browser_profile'


def get_key():
    dpapi = STATE / 'service-key.dpapi'
    if not dpapi.exists():
        raise RuntimeError('未找到已保存的服务密钥；请先在 GUI 中设置并记住一次连接配置。')
    return protect(dpapi.read_bytes(), True).decode()


def resolve_python():
    """解析真正能跑采集器的解释器：优先当前解释器（若自带 playwright），
    否则回退到 TakealotCollector 捆绑运行时 venv。"""
    try:
        import importlib.util
        if importlib.util.find_spec('playwright'):
            return sys.executable
    except Exception:
        pass
    cand = STATE / 'runtime-cpython-313' / 'Scripts' / 'python.exe'
    if cand.exists():
        return str(cand)
    return sys.executable


def log(line):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {line}\n')
    except Exception:
        pass


def launch_collector(headless, stealth=False):
    key = get_key()
    profile = get_profile()
    env = os.environ.copy()
    env['PP_COLLECTOR_KEY'] = key
    env['PYTHONIOENCODING'] = 'utf-8'
    py = resolve_python()
    cmd = [py, '-u', str(ROOT / 'collector.py'),
           '--profile', str(profile), '--stop-file', str(STOP_FILE)]
    if headless:
        cmd.append('--headless')
    if stealth:
        cmd.append('--stealth')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    return subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                            stdout=open(LOG_FILE, 'a', encoding='utf-8'),
                            stderr=subprocess.STDOUT, creationflags=flags)


def status_state():
    try:
        import json
        data = json.loads(COLLECTOR_STATUS.read_text(encoding='utf-8'))
        return data.get('state')
    except Exception:
        return None


def supervisor(headless, stealth=False):
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 显式 start：清除可能残留的停止信号
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    while not STOP_FILE.exists():
        try:
            p = launch_collector(headless, stealth)
            log(f'[supervisor] collector 已启动 pid={p.pid} headless={headless} stealth={stealth}')
        except Exception as exc:
            log(f'[supervisor] 启动失败：{exc}')
            time.sleep(10)
            continue
        p.wait()
        log(f'[supervisor] collector 退出 code={p.returncode}')
        if STOP_FILE.exists():
            break
        if status_state() == 'auth_expired':
            log('[supervisor] 检测到登录会话失效，停止自动重启；请打开 GUI 重新登录后再次 start。')
            print('登录会话已失效，已停止自动重启。请打开 GUI 点「登录 / 配送设置」重新登录，然后重新运行 start。')
            break
        log('[supervisor] 异常退出，5s 后自动重启…')
        time.sleep(5)
    log('[supervisor] 检测到停止信号，退出。')


def stop():
    STOP_FILE.touch()
    print(f'已发送停止信号：{STOP_FILE}（守护进程会优雅退出）')


def install_task(headless, stealth=False):
    if os.name != 'nt':
        raise RuntimeError('计划任务仅支持 Windows')
    python = resolve_python()
    script = str(ROOT / 'run_background.py')
    headless_arg = '--headless' if headless else ''
    stealth_arg = '--stealth' if stealth else ''
    start_args = f'start {headless_arg} {stealth_arg}'.strip()
    ps = f'''
$ErrorActionPreference = 'Stop'
$python = "{python}"
$script = "{script}"
$dir = "{ROOT}"
$arg = "-u `"$script`" {start_args}".Trim()
$action = New-ScheduledTaskAction -Execute $python -Argument $arg -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "{TASK_NAME}" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Output "TASK_REGISTERED"
'''
    out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                         capture_output=True, encoding='utf-8', errors='replace')
    if out.returncode != 0:
        raise RuntimeError('计划任务注册失败：' + (out.stderr or out.stdout))
    print('已注册计划任务「' + TASK_NAME + '」：登录时启动、失败每 1 分钟重启（最多 3 次）。')
    print('启动：python run_background.py start  或  schtasks /run /tn ' + TASK_NAME)
    print('停止：python run_background.py stop')


def uninstall_task():
    out = subprocess.run(['schtasks', '/delete', '/tn', TASK_NAME, '/f'],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError('注销计划任务失败：' + (out.stderr or out.stdout))
    print('已注销计划任务「' + TASK_NAME + '」。')


def main():
    cli = argparse.ArgumentParser(description='Takealot 采集器后台守护进程')
    sub = cli.add_subparsers(dest='cmd', required=True)
    sub.add_parser('start').add_argument('--headless', action='store_true')
    sub.add_parser('start').add_argument('--stealth', action='store_true')
    sub.add_parser('stop')
    sub.add_parser('install').add_argument('--headless', action='store_true')
    sub.add_parser('install').add_argument('--stealth', action='store_true')
    sub.add_parser('uninstall')
    args = cli.parse_args()
    stealth = getattr(args, 'stealth', False)
    if args.cmd == 'start':
        supervisor(args.headless, stealth)
    elif args.cmd == 'stop':
        stop()
    elif args.cmd == 'install':
        install_task(args.headless, stealth)
    elif args.cmd == 'uninstall':
        uninstall_task()


if __name__ == '__main__':
    main()
