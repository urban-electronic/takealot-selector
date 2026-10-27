// 列表图片必须走缩略图。
//
// 原来列表一行只显示 68×68，却按原图下载：一页 20 张实测合计 10.8MB，单张最大
// 1.3MB，跨境链路上要 20 多秒才能全部显示——这是「打开程序巨慢」的直接原因。
// 这个守卫盯住整条链路：页面传宽度 → 远程 API 拼参数 → 代理缩放 → 容器装了 Pillow。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = (file) => fs.readFileSync(path.resolve(__dirname, '..', file), 'utf8');

const page = read('src/pages/ProductList.tsx');
const remote = read('src/remoteApi.ts');
const local = read('src/api.ts');
const proxy = read('backend/api/image_proxy.py');
const requirements = read('backend/requirements.txt');

const checks = [
  [page, /getImageUrl\(p\.product_image_url,\s*160\)/, '列表图片列必须请求 160px 缩略图'],
  [page, /getImageUrl\(p\.product_image_url \|\| p\.product_image_path \|\| '',\s*160\)/, '变体小图同样要缩略图'],
  [remote, /export const getImageUrl = \(url: string, width\?: number\)/, '远程 getImageUrl 要接受宽度'],
  [remote, /&w=\$\{Math\.round\(width\)\}/, '宽度要真的拼进代理 URL'],
  [local, /export const getImageUrl = \(url: string, _width\?: number\)/, '本地版签名要与远程一致，调用点才不用分支'],
  [proxy, /w: int = Query\(0, ge=0, le=MAX_THUMB_WIDTH/, '代理要接受 w 参数并限幅'],
  [proxy, /def _resize_image\(/, '要有缩放实现'],
  [proxy, /THUMB_TTL_SECONDS = 3600/, '缩略图缓存要按小时级，不能沿用原图 30 秒'],
  [requirements, /^pillow/m, '容器要装 pillow，否则线上静默退化成原图'],
];

for (const [source, pattern, why] of checks) {
  assert.match(source, pattern, why);
}

// 突变自测：删掉关键行后，守卫必须变红——否则它只是装饰。
const mutants = [
  ['列表图宽度', page, /getImageUrl\(p\.product_image_url,\s*160\)/,
    (s) => s.replace('getImageUrl(p.product_image_url, 160)', 'getImageUrl(p.product_image_url)')],
  ['代理 URL 参数', remote, /&w=\$\{Math\.round\(width\)\}/,
    (s) => s.replace('&w=${Math.round(width)}', '')],
  ['缩放实现', proxy, /def _resize_image\(/,
    (s) => s.replace('def _resize_image(', 'def _resize_image_disabled(')],
];

for (const [name, source, pattern, mutate] of mutants) {
  const mutated = mutate(source);
  assert.notEqual(mutated, source, `突变自测没生效：${name} 的删除规则没匹配到内容`);
  assert.doesNotMatch(mutated, pattern, `突变自测失败：${name} 删掉后守卫仍通过，说明它没在守这一行`);
}

console.log(`PASS 列表缩略图链路：页面传宽度、代理缩放、小时级缓存、容器依赖（含 ${mutants.length} 条突变自测）`);
