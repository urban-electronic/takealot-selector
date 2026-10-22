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
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_server import vite_server  # noqa: E402  （必须先插 sys.path）

# 端口不再写死：本机可能同时有另一份工作副本在跑 selfcheck，固定端口会互相探到/杀掉
# 对方的服务（表现为随机 ERR_CONNECTION_REFUSED 或「元素找不到」）。见 _audit_server.py。


def main():
    with vite_server(ROOT) as base:
        # BASE 在审计模块被 exec 的那一刻从环境变量读，所以必须先设再 run_path。
        os.environ['AUDIT_BASE_URL'] = base
        audit = runpy.run_path(str(Path(__file__).with_name('ui_contrast_audit.py')))
        with tempfile.TemporaryDirectory() as out:
            audit['main'].__globals__['OUT'] = out
            asyncio.run(audit['main']())


if __name__ == '__main__':
    main()
