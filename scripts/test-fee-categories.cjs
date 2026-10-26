// 费率品类读取失败必须说出来（src/utils/useFeeCategories.ts）。
//
// 钉死的原因：/api/fee-categories 读失败时，新建产品页和详情页原来都是
// `.catch(() => {})`，下拉只剩「-- 请选择 --」。用户看到的是「没有品类可选」，
// 不是「这次没读到」——于是他会去别处找品类、或者干脆不填品类就保存。
// 同一个接口在设置页是会报错的（Settings.tsx 用 Promise.all + setError），
// 同一份数据在两个地方给出两种结论。
//
// 这里只测 hook 的状态机（成功/失败/同步抛错/重试/重试失败不清空已得数据），
// 不启浏览器、不连后端。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { JSDOM } = require('jsdom');
const ts = require('typescript');

const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'https://example.invalid' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');

const file = path.resolve(__dirname, '../src/utils/useFeeCategories.ts');
const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiled = new Module(file, module);
compiled.filename = file;
compiled.paths = Module._nodeModulePaths(path.dirname(file));
compiled._compile(output, file);
const { useFeeCategories, FEE_CATEGORY_LOAD_ERROR } = compiled.exports;

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

const ROWS = [
  { id: 1, name: 'Home', fee_rate_range: '4-9%', success_fee_rate: 0.08 },
  { id: 2, name: 'Toys', fee_rate_range: '4-9%', success_fee_rate: 0.075 },
];

/** 渲染一个只暴露 hook 状态的探针组件。 */
function probe(source) {
  const state = { current: null };
  function Probe() {
    state.current = useFeeCategories(source);
    return null;
  }
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  act(() => root.render(React.createElement(Probe)));
  return { state, unmount: () => act(() => root.unmount()) };
}

/** 让 effect 里的 promise 链跑完（微任务 + 一次宏任务）。 */
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

(async () => {
  // ---- 1) 读取成功：列表就位、无错误 -------------------------------------
  {
    const { state, unmount } = probe({ getFeeCategories: () => Promise.resolve(ROWS) });
    await flush();
    check(state.current.categories.length === 2, '读取成功后两个品类都应在列表里');
    check(state.current.error === '', '读取成功时不应有错误文案');
    check(state.current.loading === false, '读取结束后 loading 必须落回 false');
    unmount();
  }

  // ---- 2) 读取失败：必须给出可显示的结论，而不是空数组 -------------------
  {
    const { state, unmount } = probe({ getFeeCategories: () => Promise.reject(new Error('HTTP 500')) });
    await flush();
    check(state.current.categories.length === 0, '失败时列表为空（不兜造数据）');
    check(state.current.error === FEE_CATEGORY_LOAD_ERROR,
      '失败必须设置错误文案：空下拉 + 无提示 = 用户以为没有品类可选');
    check(/不是没有品类/.test(state.current.error), '文案要说清「不是没有品类」，否则用户仍会误解');
    check(state.current.loading === false, '失败后 loading 也必须落回 false，不能一直转');
    unmount();
  }

  // ---- 3) 同步抛错：也要落进错误状态，不能从 effect 冒出去 --------------
  {
    const { state, unmount } = probe({ getFeeCategories: () => { throw new Error('boom'); } });
    await flush();
    check(state.current.error === FEE_CATEGORY_LOAD_ERROR, '读取函数同步抛错时同样要显示错误');
    unmount();
  }

  // ---- 4) 重试：失败后 reload 能重新读取并清掉错误 -----------------------
  {
    let calls = 0;
    const source = {
      getFeeCategories: () => {
        calls += 1;
        return calls === 1 ? Promise.reject(new Error('offline')) : Promise.resolve(ROWS);
      },
    };
    const { state, unmount } = probe(source);
    await flush();
    check(calls === 1 && state.current.error !== '', '首轮失败');
    act(() => state.current.reload());
    await flush();
    check(calls === 2, 'reload 必须真的再请求一次（不能只是清掉错误文案）');
    check(state.current.categories.length === 2, '重试成功后列表要填上');
    check(state.current.error === '', '重试成功后错误文案要清掉');
    unmount();
  }

  // ---- 5) 成功后再失败：保留已拿到的列表，只加错误 -----------------------
  {
    let calls = 0;
    const source = {
      getFeeCategories: () => {
        calls += 1;
        return calls === 1 ? Promise.resolve(ROWS) : Promise.reject(new Error('offline'));
      },
    };
    const { state, unmount } = probe(source);
    await flush();
    act(() => state.current.reload());
    await flush();
    check(state.current.categories.length === 2, '重试失败不该把上一次成功的列表清空');
    check(state.current.error !== '', '重试失败仍要提示');
    unmount();
  }

  // ---- 6) 新建产品页里另外两条「读失败」也不许静默 ---------------------------
  // 同一个页面上还有两处辅助能力靠接口读结果：
  //   ① /api/fee-mapping-rules —— 页面导入后按分类路径自动匹配 Fee 品类；
  //   ② /api/products           —— 找「上一件商品」以便沿用成本。
  // 两处原来都是 `.catch(() => {})`：前者让「系统推荐」写「无推荐」（和「规则里没有
  // 匹配项」长得一模一样），后者让按钮整块消失（用户以为没建过商品）。这里做源码级
  // 守卫：只允许「读失败 → 置提示状态」，不允许吞掉。
  {
    const page = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductCreate.tsx'), 'utf8');
    // 唯一允许静默的是自动翻译：中文品名是选填项，译不出来用户自己填，弹提示反而是噪音。
    const silent = page.split('\n')
      .filter(line => !line.trim().startsWith('//') && !line.trim().startsWith('*'))
      .filter(line => line.includes('.catch(() => {})'));
    check(silent.every(line => line.includes('translateProductName')),
      `新建产品页不许再有静默吞掉的读失败（除自动翻译）：${silent.join(' | ')}`);
    check(page.includes('setFeeMatchError('), '费率映射规则读失败必须落成可见提示');
    check(page.includes('setCostSourceError('), '已有商品列表读失败必须落成可见提示');
    check(page.includes('不代表分类路径不匹配'), '「无推荐」要说明它不是「分类不匹配」的结论');
  }

  // 设置页同一次操作的成败不许同时挂在屏幕上：成功提示与错误提示是两块独立
  // 状态，原来「汇率已更新」成功后紧接着一个保存失败，屏幕上会并排出现
  // 绿条和红条，用户不知道这次到底成没成（采购页 PROC-ERR-STALE-01 同一判据）。
  // 守卫按「处理函数体」逐个看：允许清空（setError('')），不允许直接写文案。
  {
    const settings = fs.readFileSync(path.resolve(__dirname, '../src/pages/Settings.tsx'), 'utf8');
    const bodyOf = (name) => {
      const start = settings.indexOf(`const ${name} = `);
      if (start < 0) return '';
      const end = settings.indexOf('\n  };', start);
      return end < 0 ? '' : settings.slice(start, end);
    };
    check(settings.includes('const notice = '), '设置页必须提供 notice()：成功时清掉旧错误');
    check(settings.includes('const fail = '), '设置页必须提供 fail()：失败时清掉旧成功提示');
    for (const name of ['handleSaveExchange', 'handleToggleCategory', 'handleUpdateFeeRate',
                        'handleAddMappingRule', 'handleSaveApiUrl', 'handleExportToCloud']) {
      const body = bodyOf(name);
      check(body.length > 0, `${name} 必须仍然存在`);
      const direct = body.match(/(?:setError|setMessage)\((?!'')/g) || [];
      check(direct.length === 0,
        `${name} 不得直接写横幅（${direct.join('、')}），必须走 notice()/fail()，否则成败会同时显示`);
    }
  }

  console.log(`test-fee-categories: ${checks} checks passed`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
