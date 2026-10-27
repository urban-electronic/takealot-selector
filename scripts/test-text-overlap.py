"""文字重叠回归测试：所有页面 × 所有常见窗口宽度，逐对检查可见文字矩形。

为什么单独一个套件：`test-responsive-layout.py` 查的是「整页有没有横向溢出、
导航会不会换行、弹窗会不会跑出屏幕」，它不看文字之间是否互相压住。而线上真
发生过一次——调价列表的「同价·卖家未核实」胶囊压到价格上，页面既不横滚也
不报错，只是难看且读不出来。

判定口径：两个元素的**可见**文字矩形相交超过 2px×2px。
「可见」= 自身矩形 ∩ 所有裁剪祖先（overflow 非 visible）：
  - getBoundingClientRect 不管裁剪，长标题被父级 overflow:hidden 裁掉之后
    仍然报全宽，直接比会造出「压到隔壁列」的假重叠；
  - 表格横向滚动区里滚出视野的单元格，与滚动区求交后为空，应当跳过。
另外排除：祖先/后代、fixed 覆盖层、absolute + z-index 的浮层、折叠 <details>
的内容（Chrome 用 content-visibility 藏，但布局矩形还在）、透明/隐藏元素。

顺带查两类「分配不合理」：
  - clipped：容器把文字裁了却没给省略号，用户看不出还有内容
  - spills：overflow 可见而内容比盒子宽，文字会压到邻居身上

弹窗/菜单单独扫一遍（扫描根限定在容器内），因为重叠最容易藏在那里。
弹窗**打不开要判失败**，不能静默跳过——否则改个按钮名就等于悄悄失去覆盖。
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_server import free_port  # noqa: E402  （必须先插 sys.path）

# 端口不再写死（原来固定 5199，本机同时有两份工作副本跑 selfcheck 时会互相探到对方的
# 服务、再被对方的 teardown 杀掉，表现为随机 ERR_CONNECTION_REFUSED）。main() 里现挑。
PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
WIDTHS = [1920, 1440, 1280, 1024, 900, 768, 640, 480, 390, 320]

SCAN = r"""
(rootSel) => {
  const root = rootSel ? document.querySelector(rootSel) : document.body;
  if (!root) return { missing: rootSel };
  const path = (el) => {
    const bits = [];
    let node = el;
    for (let i = 0; i < 4 && node && node.nodeType === 1; i++) {
      let s = node.tagName.toLowerCase();
      if (node.className && typeof node.className === 'string') {
        s += '.' + node.className.trim().split(/\s+/).slice(0, 2).join('.');
      }
      bits.unshift(s);
      node = node.parentElement;
    }
    return bits.join('>');
  };
  const isOverlay = (el) => {
    let node = el;
    for (let i = 0; i < 8 && node && node.nodeType === 1; i++) {
      const cs = getComputedStyle(node);
      if (cs.position === 'fixed') return true;
      if (cs.position === 'absolute' && cs.zIndex !== 'auto') return true;
      node = node.parentElement;
    }
    return false;
  };
  const clip = (el, r) => {
    let node = el.parentElement;
    while (node && node !== document.documentElement) {
      const cs = getComputedStyle(node);
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const nr = node.getBoundingClientRect();
        r = { left: Math.max(r.left, nr.left), top: Math.max(r.top, nr.top),
              right: Math.min(r.right, nr.right), bottom: Math.min(r.bottom, nr.bottom) };
        if (r.right <= r.left || r.bottom <= r.top) return null;
      }
      node = node.parentElement;
    }
    return r;
  };
  const texts = [];
  for (const el of root.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (parseFloat(cs.opacity) === 0) continue;
    if (el.closest('[aria-hidden=true]')) continue;
    const closed = el.closest('details:not([open])');
    if (closed && el !== closed.querySelector(':scope > summary')) continue;
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
    let label = '';
    if (el.matches('select')) {
      label = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
    } else if (el.matches('input, textarea')) {
      label = el.value || el.placeholder || '';
    } else {
      label = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    }
    if (!label) continue;
    const r0 = el.getBoundingClientRect();
    if (r0.width < 2 || r0.height < 2) continue;
    const r = clip(el, { left: r0.left, top: r0.top, right: r0.right, bottom: r0.bottom });
    if (!r) continue;
    texts.push({ el, r, label: label.slice(0, 40) });
  }
  const overlaps = [];
  for (let i = 0; i < texts.length; i++) {
    for (let j = i + 1; j < texts.length; j++) {
      const a = texts[i], b = texts[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      if (isOverlay(a.el) || isOverlay(b.el)) continue;
      const ix = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
      const iy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
      if (ix > 2 && iy > 2) {
        overlaps.push({ a: path(a.el), at: a.label, b: path(b.el), bt: b.label,
                        ix: Math.round(ix), iy: Math.round(iy) });
      }
    }
  }
  const clipped = [], spills = [];
  for (const el of root.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (!el.scrollWidth || el.scrollWidth <= el.clientWidth + 1) continue;
    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
    const label = (el.textContent || '').trim().slice(0, 30);
    if (!label) continue;
    const rec = { path: path(el), label, scroll: el.scrollWidth, client: el.clientWidth,
                  overflowX: cs.overflowX, ellipsis: cs.textOverflow };
    if (cs.overflowX === 'hidden') {
      if (cs.textOverflow !== 'ellipsis') clipped.push(rec);
    } else {
      spills.push(rec);
    }
  }
  return { overlaps, clipped, spills,
           hScroll: document.documentElement.scrollWidth - document.documentElement.clientWidth };
}
"""

# (打开按钮, 扫描根)。扫描根必须真的存在，打不开算失败。
DIALOGS = {
    '/products': [('同步卖家后台', '.seller-assist-dialog')],
    '/packing': [('编辑', '[role=dialog]')],
    '/procurement': [('新建记录', 'form')],
    '/replenishment': [('设置', '[role=dialog]')],
    '/': [('管理工具', '.management-menu-panel')],
}


def load_fixture():
    """复用版式审计里的 fixture 数据，避免两套假数据互相漂。"""
    spec = importlib.util.spec_from_file_location(
        'layout_audit', Path(__file__).resolve().parent / 'test-responsive-layout.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DATA, mod.PAGES


def describe(res, width, path, scope):
    lines = []
    for o in res['overlaps']:
        lines.append(f"{width} {path} [{scope}] 重叠 {o['ix']}x{o['iy']}px："
                     f"{o['at']!r} 压住 {o['bt']!r}\n    {o['a']}\n    {o['b']}")
    for c in res['clipped']:
        lines.append(f"{width} {path} [{scope}] 文字被裁且无省略号：{c['label']!r} "
                     f"{c['scroll']}>{c['client']} {c['path']}")
    for s in res['spills']:
        lines.append(f"{width} {path} [{scope}] 内容比盒子宽：{s['label']!r} "
                     f"{s['scroll']}>{s['client']} {s['path']}")
    if res['hScroll'] > 2:
        lines.append(f"{width} {path} [{scope}] 整页横向滚动 {res['hScroll']}px")
    return lines


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--widths', default=','.join(str(w) for w in WIDTHS))
    cli.add_argument('--out', default='')
    args = cli.parse_args()
    widths = [int(w) for w in args.widths.split(',') if w]
    DATA, PAGES = load_fixture()

    from playwright.sync_api import sync_playwright
    # 端口现挑 + --strictPort：抢不到就直接失败，不会静默换端口去测别人的构建。
    global PORT, BASE
    PORT = free_port()
    BASE = f'http://127.0.0.1:{PORT}'
    server = subprocess.Popen(
        ['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(PORT), '--strictPort'],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    problems, report, surfaces = [], [], 0
    # 未声明的接口请求要记账。以前这里直接 404 掉就算了，页面会静默降级成
    # 空列表/默认值，版式照样"通过"——等于拿假数据给自己发了张合格证。
    undeclared = []
    started = time.time()
    try:
        for _ in range(120):
            try:
                build_opener(ProxyHandler({})).open(BASE, timeout=1).close()
                break
            except OSError:
                if server.poll() is not None:
                    raise SystemExit('Vite 测试服务起不来')
                time.sleep(0.1)
        else:
            raise SystemExit('Vite 测试服务没有就绪')

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_init_script(f"localStorage.setItem('api_base_url', '{BASE}')")

            def intercept(route):
                from urllib.parse import urlsplit
                url = urlsplit(route.request.url)
                if url.path.startswith('/api/'):
                    if url.path in DATA:
                        route.fulfill(json=DATA[url.path])
                    else:
                        undeclared.append(f'{route.request.method} {url.path}')
                        route.fulfill(status=404, json={'detail': 'fixture missing'})
                    return
                if url.netloc == f'127.0.0.1:{PORT}':
                    route.continue_()
                else:
                    route.abort()

            context.route('**/*', intercept)
            page = context.new_page()
            for width in widths:
                page.set_viewport_size({'width': width, 'height': 900})
                for path, title in PAGES:
                    page.goto(BASE + path)
                    page.get_by_role('heading', name=title, exact=False).first.wait_for(timeout=10000)
                    try:
                        page.locator('.loading').wait_for(state='hidden', timeout=5000)
                    except Exception:
                        pass
                    res = page.evaluate(SCAN, None)
                    surfaces += 1
                    report.append(dict(res, width=width, path=path, scope='page'))
                    problems += describe(res, width, path, 'page')

                    for opener, root_sel in DIALOGS.get(path, []):
                        try:
                            page.get_by_role('button', name=opener, exact=False).first.click(timeout=5000)
                            page.locator(root_sel).first.wait_for(timeout=5000)
                        except Exception:
                            problems.append(f'{width} {path}: 弹窗「{opener}」打不开（{root_sel}）'
                                            '——覆盖静默失效，检查按钮文案或根选择器是否变了')
                            continue
                        try:
                            res = page.evaluate(SCAN, root_sel)
                            surfaces += 1
                            report.append(dict(res, width=width, path=path, scope=root_sel))
                            problems += describe(res, width, path, root_sel)
                        finally:
                            page.keyboard.press('Escape')
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')

    # 接口没声明就等于这一屏没被真正测到：静默降级的页面不会报版式问题。
    for entry in sorted(set(undeclared)):
        problems.append(f'审计 fixture 未声明接口：{entry}——请求被 404 掉，页面会静默降级，'
                        '这一屏的结论不算数（补到 test-responsive-layout.py 的 DATA 里）')

    elapsed = time.time() - started
    if problems:
        print('\n'.join(problems[:60]))
        if len(problems) > 60:
            print(f'……另有 {len(problems) - 60} 处')
        print(f'\n{len(problems)} 处版式问题，扫描 {surfaces} 个面，用时 {elapsed:.0f}s')
        return 1
    print(f'文字重叠 0 处，扫描 {surfaces} 个面（{len(widths)} 个宽度 × 页面与弹窗），用时 {elapsed:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
