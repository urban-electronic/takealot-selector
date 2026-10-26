# 界面反馈：缺失值判色与成败横幅（2026-10-27）

两类问题在自检全绿时完全看不见，只有「数据缺一半」或「上一步刚成功、这一步失败」时才暴露。
共同点是：界面在没有任何真实依据的情况下，替用户下了一个看起来有依据的结论。

## 1. 缺失值不许被染成成功色

`src/utils/number.ts`：

```ts
toneOf(value, isGood) → 'neutral' | 'good' | 'bad'
```

判色前必须先问「这个数存在吗」：`toNumber` 为 `null`（缺失 / 空串 / `NaN` / 非法字符串）
一律 `'neutral'`，由调用方给中性色；存在才谈好坏。

**为什么不能用 `(value ?? 0) >= 0`**：`?? 0` 把「没算出来」变成 0，而 0 恰好落在
「好」的那一侧。产品详情页的利润行原来就是这么写的，缺成本的产品显示绿色「-」，
读起来像「利润是正的、只是没显示」。更糟的是同一张表里利润率行已经做了 null 判断（灰色），
两行对同一个「没有数据」给出相反的颜色。

同一页的两行必须共用同一个判据（`PROFIT_TONE[toneOf(...)]`），只修一行会立刻再分叉。

判色是**展示层**的事，不要顺手把它塞进 `formatPrice` / `formatPercent`：
那两个函数只负责「有没有值、值是多少」，颜色是调用方的语义。

## 2. 同一次操作的成败不许同时挂在屏幕上

成功提示（`message`）与错误提示（`error`）是两个独立 `useState`，
页面上也通常是两个独立的横幅。如果每个动作只写自己那一条、不清另一条，
就会出现「汇率已更新」（绿）+「品类费率保存失败」（红）并排显示——
用户看到的是两句互相打脸的话，而他真正想知道的「这次成没成」反而没有答案。

约定：页面里提供一对函数，**写一条就清另一条**。

```ts
const notice = (text: string) => { setMessage(text); setError(''); };
const fail = (e: unknown) => { setError(e instanceof Error ? e.message : String(e)); setMessage(''); };
```

- 允许直接清空（`setError('')` / `setMessage('')`），不允许绕过这一对直接写文案。
- 守卫按「处理函数体」逐个检查（`test-fee-categories.cjs` / `test-store-switcher.cjs`），
  正则放行清空、拦住非空写入，所以重构文案不会误报，只有新写一条裸横幅才会红。
- `fail` 同时收口 `e.message`：`catch (e: any) { setError(e.message) }` 在抛出字符串或
  `null` 时会渲染出「undefined」。

**唯一的例外是列表读取失败**：`load()` 自己失败时仍单独报错。
「保存成功、但列表没读回来」是真的两件事，用 `fail()` 把它们合并会把一半信息吃掉。

## 3. 成功动作必须说话

顺带修的一处：店铺管理页的「暂停 / 恢复」成功后只重载列表、不给任何提示，
屏幕上留着的是上一条**别的动作**的成功文案。动作成功但不说话，等于把上一条
（可能是十分钟前的）当成本次的结果。凡是会改变列表状态的动作，成功也要 `notice()`。

## 已覆盖范围

| 页面 | 状态 |
| --- | --- |
| `Settings.tsx` | 6 个动作全部走 `notice()` / `fail()`（守卫逐个函数体检查） |
| `Stores.tsx` | `save` / `toggleStatus` 走同一对；`load()` 保留独立报错 |
| `Packing.tsx` | **未做**：5 处 `setError` 都不清 `msg`，同类问题，待后续轮次 |
| `Dashboard.tsx` / `Inventory.tsx` | 一次性加载或已各自清空，未纳入守卫 |

## 相关守卫

- `scripts/test-value-coercion.cjs`：`toneOf` 三态 + 详情页两行必须共用判据。
- `scripts/test-fee-categories.cjs`：设置页 6 个处理函数不得直接写横幅。
- `scripts/test-store-switcher.cjs`：店铺页同上，并要求暂停/恢复给出结果提示。
