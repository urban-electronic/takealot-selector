# 装箱页：逐箱清单不再空、日期取做单那天、运输方式改选、唛头同入库单号（2026-10-23）

用户报的问题：点了「选择产品」下拉是空的，逐箱清单加不进东西；装箱单日期跑到导出那天；
运输方式是自由文本；唛头要跟入库单号一致。参照的是鲲鹏那份 `KPLDJ-9件海运清单.xlsx`。

## 模板长什么样（先对齐事实）

`backend/packing_template.xlsx` 与用户给的样例一致：

| 位置 | 内容 |
| --- | --- |
| A1 | 鲲鹏物流-装箱单模板 |
| A2 / B2 | **客户唛头**（样例写 `KPLDJ`） |
| F2 / G2 | **头程（空/海）**（样例写 `海运`） |
| I2 / J2 | 头程收货地址 |
| A3..M3 | 箱唛 / SKU / 中英品名 / 箱数 / 内件数量 / 单位 / 重量 / 材质 / 牌子 / 电池 / 图片 / 带电 / 带磁 |

**表里没有日期单元格。** 所以「装箱单日期」影响的是两处：文件名 `装箱单_YYYY-MM-DD.xlsx`
和单号 `PK-YYYYMMDD-001`。这两处都对不上货，才是最要命的。

## 1. 下拉为空：只列「已勾选产品」

`逐箱清单` 里每行的产品下拉，选项原来是 `selectedRows`（本次装箱单已勾选的产品）。
草稿恢复时如果 `cartonsManual` 是 true、但 `selected` 丢了，就变成一个**只有占位符的空下拉** ——
加不进产品，也没法自愈。老草稿（`cartons` 有值、`cartonsManual` 由迁移推成 true）正好踩这个坑。

两处修：

**a. 空行列全产品库。** 还没选产品的行，下拉多一个 `产品库其他（N）` 分组；
选过之后只列本单的（一行一个 400 项的巨长下拉，翻起来比重新选还慢）。
选中不在本单的 SKU 会**自动加进本单**（`pickCartonSku`），否则导出时后端会判
「箱里有不在本次装箱单中的 SKU」。

**b. 加载后从逐箱清单回填勾选。** `products` 一到位，把箱子里出现的 SKU 补回 `selected`
（`cartonBackfillDone` 保证只跑一次）。这样「逐箱清单」反过来成为本单的来源，
而不是只能单向由勾选推出来。

顺带：`products` 为空时在逐箱清单里直说「产品库还没加载出来」，不再让人对着空下拉猜。

## 2. 日期改成「做单那天」

草稿新增 `createdOn`，在草稿第一次建立时写死，之后一直跟着草稿走：

```ts
date: saved.date || savedDraft.createdOn || todayStr()
```

- 用户手改过日期 → 用改的（`exportForm.date` 本来就随草稿持久化）；
- 没改过 → 用做单那天，而不是打开/导出那天；
- 导出成功后 `setCreatedOn(todayStr())`，下一票重新起算 —— 不归零会沿用上一票的日期。

## 3. 运输方式改下拉（空运 / 海运）

模板 G2 的列名是「头程（空/海）」，只有这两类。原来是自由文本，手滑写个「快递」就印在单子上，
货代按错的渠道走。改成 `<select>`，并且**不选不许导出**：

```ts
if (!exportForm.shipping) { setError('请选择运输方式（空运 / 海运）后再导出'); return; }
```

老草稿里存过「空」「海」「快递」这类历史值的，原样保留成 `（历史值）` 选项，
打开页面时不悄悄改掉用户的数据。

## 4. 唛头同入库单号

三层兜底，后端 `export_excel` 里一次算清：

```python
mark = data.mark.strip() or data.inbound_no.strip() or 'KPLDJ'
```

- 手填的唛头优先级最高；
- 留空就用 OMS 入库单号（`ExportIn` 新增 `inbound_no` 字段），这样 B2「客户唛头」和
  A 列「箱唛」是同一套编号，两张纸能对上；
- 都没有才退回 `KPLDJ`。

前端同时做跟随：改入库单号时，唛头为空或仍等于上一个自动值才同步 —— 手改过的不会被覆盖。
`ExportIn.mark` 的默认值从 `'KPLDJ'` 改成 `''`，否则「留空」和「明确要 KPLDJ」分不出来，
用户填的入库单号永远用不上。

## 验证

- `scripts/test-packing-oms.py`：9 组断言（原 8 组 + 日期/唛头）。新增
  「空行下拉不能是空的」「唛头自动同入库单号」「日期默认做单那天」三条，
  320px 不横向溢出仍然通过。
- `backend/tests/test_packing_export_workflow.py` 新增 `test_mark_falls_back_to_inbound_no_then_kpl_dj`：
  断言 `INH1501522` → `KPLDJ` → 手填 `CK-01` 的优先级，并核对**留档的唛头和印上去的一致**。
- 后端装箱相关 pytest 全过；`tsc --noEmit` 通过。

## 踩到的坑

测试里改 localStorage 草稿时，键是 `packingDraftV1:<店铺ID>`。写到裸键 `packingDraftV1`
上没用 —— `readStoreStorage` 先读店铺键，店铺键已存在时根本不看裸键。
必须 `Object.keys(localStorage).find(k => k.includes('packingDraftV1'))` 找到真键再写。
