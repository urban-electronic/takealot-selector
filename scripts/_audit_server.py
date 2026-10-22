"""浏览器审计脚本共用的 vite 测试服务器。

为什么单独抽出来：四个审计脚本原来各自 `Popen` 一个 vite，端口写死
（5173 / 5183 / 5199）。这个仓库同时可能有两份工作副本在跑 selfcheck
（并行 agent、或本机另一个会话），两边探到的是**对方**起的服务：
先结束的那一方 teardown 时把对方正在用的服务杀掉，对方就在半路拿到
`ERR_CONNECTION_REFUSED`；vite 在端口被占用时又会**静默**换一个端口，
于是断言打在另一份构建上、结论却写着「通过」。

所以这里做两件事：
  1. 每次现挑一个空闲端口，两份工作副本不会再撞；
  2. `--strictPort`：万一端口还是被抢，vite 直接失败，不会偷偷换端口，
     探针也就不会把别人的服务当成自己的。

用法：
    from _audit_server import vite_server
    with vite_server(ROOT) as base:
        ...  # base 形如 http://127.0.0.1:52341
"""
import contextlib
import socket
import subprocess
import time
from urllib.request import ProxyHandler, build_opener


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def vite_server(root, ready_timeout: float = 15.0):
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    server = subprocess.Popen(
        ['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1',
         '--port', str(port), '--strictPort'],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        opener = build_opener(ProxyHandler({}))
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if server.poll() is not None:
                raise RuntimeError(f'Vite 测试服务起不来（端口 {port} 被占用或 vite 报错）')
            try:
                opener.open(base, timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f'Vite 测试服务 {ready_timeout:.0f}s 内没有就绪（端口 {port}）')
        yield base
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
