"""长标题布局 + 焦点圈对比度审计的真实执行入口：本机 Vite + 隔离 fixture + 真实 Chromium。

为什么需要这个文件：`scripts/ui_longtitle_focus_audit.py` 是审计本体——12 个
「路由 × 宽度」组合，每组 Tab 最多 60 次逐个量 document.activeElement 的焦点圈
颜色与背景，按 WCAG 2.1 算比值，<3:1 判失败；同时按**绘制矩形**判文字互相重叠与
表格单元格越界。但它此前只被 `scripts/test-longtitle-audit-safety.py` 用**假浏览器**
调用过：那个套件验证的是「失败路径会不会判失败」，喂进去的 `overlaps` 恒为空、
`ringRatio` 恒为 3.1。也就是说真实页面上的长标题重叠和焦点圈对比度，从来没在任何
门禁里量过一次——脚本放着不跑，和没有这个脚本是一样的，而报告上仍然写着
「视觉一致性通过」。

这里把它接进 selfcheck（`scripts/test-*.py` 会被自动纳入），用的是审计本体的同一份
判据，没有放宽任何断言：未测到的页面、被拦下的请求、任何一处重叠/越界/焦点圈不足
3:1 都算失败。

隔离性：只连 127.0.0.1 的 Vite（`ui_contrast_audit.local_origin` 会拒绝其它主机），
`/api/*` 由审计本体从 `test-responsive-layout.py` 的 `DATA` 派生，写请求与非本机请求
一律 abort。截图与 json 写进临时目录，不落到仓库里（否则每次自检都会留下一堆未跟踪
文件）。
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

# 端口不写死：本机可能同时有另一份工作副本在跑 selfcheck，固定端口会互相探到/杀掉
# 对方的服务（表现为随机 ERR_CONNECTION_REFUSED 或「元素找不到」）。见 _audit_server.py。


def main():
    with vite_server(ROOT) as base:
        # BASE 在审计模块被 exec 的那一刻从环境变量读（它 import 了 ui_contrast_audit），
        # 所以必须先设环境变量再 run_path。
        os.environ['AUDIT_BASE_URL'] = base
        audit = runpy.run_path(str(Path(__file__).with_name('ui_longtitle_focus_audit.py')))
        with tempfile.TemporaryDirectory() as out:
            # OUT 是模块级常量：截图与 json 都落在它下面，改到临时目录才不会污染仓库。
            audit['run'].__globals__['OUT'] = Path(out) / 'longtitle'
            status = asyncio.run(audit['run']())
    if status != 0:
        raise RuntimeError('长标题/焦点圈审计未通过；未成功测量的页面不得视为零问题')


if __name__ == '__main__':
    main()
