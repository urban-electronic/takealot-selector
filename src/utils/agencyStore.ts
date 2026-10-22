/**
 * 代理店 vs 自营店的区分。
 *
 * 约定（用户口述，2026-10-22）：店铺名字里会写「代理XX店铺」，写了的才是代理店；
 * 没写的（默认那家「自营店铺」）是自营。
 *
 * 两者差别：代理店（CK 这类）才有「官方售价」概念 —— 新加坡官网价、国内原价、
 * 淘宝到手价这套多市场参考价，用来给代理品定价。自营是我们自己从 Takealot 选品、
 * 1688 采购，根本没有官方售价，整套参考价对它没有意义，不该占界面。
 *
 * 本文件是叶子模块（除类型外不 import 任何相对路径），
 * ProductList 会被 jsdom 测试直接编译，嵌套相对导入会 MODULE_NOT_FOUND。
 */

/** 店名里带「代理」→ 代理店 */
export function isAgencyStoreName(name: string | null | undefined): boolean {
  return /代理/.test(name || '');
}
