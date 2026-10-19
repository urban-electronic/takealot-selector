"""对比度/控件尺寸审计的真实执行入口：本机 Vite + 隔离 fixture + 真实 Chromium。

为什么需要这个文件：`scripts/ui_contrast_audit.py` 是审计本体，但它此前只被
`scripts/test-ui-audit-safety.py` 用**假浏览器**调用过——那个套件验证的是「审计失败
路径会报错、拦截器不碰生产」，量出来的全是桩数据（`lowContrastCount: 0`）。也就是说
真实页面的对比度从来没在任何门禁里量过一次：脚本放着不跑，和没有这个脚本是一样的，
而报告上仍然写着「视觉一致性通过」。

这里把它接进 selfcheck（scripts/test-*.py 会被自动纳入），用的是审计本体的同一份
判据，没有放宽任何断言：未测到页面、请求被拦、任何一处低对比文字都算失败。

隔离性：只连 127.0.0.1 的 Vite（`ui_contrast_audit.local_origin` 会拒绝其它主机），
`/api/*` 全部由 `test-responsive-layout.py` 的 `DATA` 应答，写请求与非本机请求一律 abort。
报告写进临时目录，不落到仓库里（否则每次自检都会留下一份未跟踪文件）。
"""
import asyncio
import os
import runpy
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent.parent
# 与 test-responsive-layout.py 的 5173 分开：两个套件各自起服务，谁也不依赖执行顺序。
PORT = 5183
BASE = f'http://127.0.0.1:{PORT}'


def wait_ready(server, attempts=200):
    for _ in range(attempts):
        try:
            build_opener(ProxyHandler({})).open(BASE, timeout=1).close()
            return
        except OSError:
            if server.poll() is not None:
                raise RuntimeError('Vite 测试服务启动失败（端口被占用或依赖缺失）')
            time.sleep(0.1)
    raise RuntimeError('Vite 测试服务在 20 秒内未就绪')


def main():
    server = subprocess.Popen(
        ['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(PORT), '--strictPort'],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(server)
        # BASE 在审计模块被 exec 的那一刻从环境变量读，所以必须先设再 run_path。
        os.environ['AUDIT_BASE_URL'] = BASE
        audit = runpy.run_path(str(Path(__file__).with_name('ui_contrast_audit.py')))
        with tempfile.TemporaryDirectory() as out:
            audit['main'].__globals__['OUT'] = out
            asyncio.run(audit['main']())
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
    main()
