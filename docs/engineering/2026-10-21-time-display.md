# 时间口径：谁负责时区（2026-10-21）

这套系统里同时存在三种时间，混用一次就会在界面上差 8 小时或差一天。
下面是当前实现的分工，以及每个位置为什么这样写。

## 1. 存储：一律 UTC，且不带时区标记

`models.py` 里的 `created_at` / `updated_at` 都是 `Column(DateTime, default=datetime.utcnow)`，
SQLite 里存的是 naive UTC；Pydantic 序列化成 `2026-10-21T12:00:00`，**没有 `Z`**。

不要试图把列改成 timezone-aware：那是一次数据迁移，历史行全是 naive，
改完要么读出报错，要么把老数据当成 UTC+0 之外的东西解释。

## 2. 展示：前端负责换算，固定 Asia/Shanghai

`src/utils/datetime.ts` 提供两个函数：

- `parseServerTime(value)` —— 没有 `Z` / `±hh:mm` 的一律**当 UTC**（全库没有 naive 本地时间，
  唯一的 `datetime.now()` 只用来拼采购单号）；纯日期补 `T00:00:00Z`；空格分隔先换 `T`。
  解析不了返回 `null`，不抛。
- `beijingDateTime(value, empty)` —— 用 `Intl` 固定 `Asia/Shanghai` 格式化，坏值返回占位符。

**为什么不能直接 `new Date(str).toLocaleString('zh-CN')`**：JS 把不带时区的串按*运行机器*
的本地时区解析。东八区浏览器会把 UTC 12:00 显示成 12:00（实际 20:00），
UTC 16:00 之后创建的记录连日期都是前一天。`toLocaleString` 不给 `timeZone` 时同理——
它跟随浏览器时区，出差或改了系统时区就变。

守卫：`scripts/test-datetime.cjs` 用源码断言挡住「`toLocaleString('zh-CN')` 不给 `timeZone`」。

## 3. 业务日期：只有 `todayLocal()` 一份实现

「今天是几号」属于业务口径，必须是中国时间，不跟随机器：

```ts
import { todayLocal } from '../utils/datetime';   // YYYY-MM-DD，Asia/Shanghai
```

采购默认日期、库存调整默认日期、补货计划默认日期、装箱单导出日期与文件名都走它。
页面里不要再写 `getFullYear()/getMonth()/getDate()` 拼日期——`Packing.tsx` 曾经有一份私有实现，
非东八区机器上装箱单日期与其它页差一天。

## 4. 服务端要落「用户看得见的日期」时，显式写 UTC+8

`procurement_routes._now_str()` 是唯一一处：`recorded_at` 是给用户看的采购日期，
前端取前 10 位当日期显示，库存对账还拿它做字符串比较。

```python
datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
```

容器时区是 UTC，裸 `datetime.now()` 会让北京时间 00:00–08:00 的记录写成前一天。
`backend/tests/test_procurement_recorded_at.py` 同时钉住「偏差 < 60 秒」和
「函数体里不许出现裸 `datetime.now()`」——开发机本身是东八区，只看时间差抓不到。

## 5. 南非时间只用于调价

`AutoPricing.tsx` 的「今天已改价几次」按 `Africa/Johannesburg` 取日期，因为 Takealot 的
业务日是南非时间。这是调价语义，不属于展示口径，不要顺手改成北京时间。
