# 装箱产品库与发货单号按店铺隔离

## 是什么

接入代理品牌（代运营 CK）之后，同一个后台里有两个店铺。装箱产品库
（`packing_products`）原来以 `sku` 为**全局主键**，发货单（`shipments`）的
`shipment_no` 原来**全局唯一**。这两条约束在多店铺下都是错的：

| 现象 | 原因 |
| --- | --- |
| A 店改某 SKU 的重量/材质/图片，B 店同名 SKU 跟着变 | 一个 SKU 只有一行，两个店铺共用 |
| A 店删一个 SKU，B 店的同名 SKU 一起消失 | 同上 |
| 两个店铺同一天都想用 `PK-20261021-001` | `shipment_no` 全局唯一，后到的被别店占号 |

两者都不报错，属于**静默数据污染**——比报错更难发现。

## 结构

- `packing_products`：主键 `(store_id, sku)`。同一个 SKU 在两个店铺是两条互不影响的记录。
- `shipments`：唯一约束 `(store_id, shipment_no)`；`draft_key` 保持全局唯一（它本身已带
  store_id 前缀，例如 `{store_id}:reconcile:{stamp}:{index}`）。

`main._ensure_columns()` 里挂了两段启动期迁移：

- `_migrate_packing_products_store_scope()`：缺 `store_id` 列或主键不是
  `(store_id, sku)` 时，建临时表 → 拷数据（`store_id` 统一填 `DEFAULT_STORE_ID`）→
  删旧表 → 改名。重建时**去掉外键**：临时表不在原 `MetaData` 里，SQLite 建表会报
  找不到被引用表。
- `_migrate_shipments_store_scope()`：只有在存在「`shipment_no` 单列唯一约束」时才重建，
  重建后补 `(store_id, shipment_no)` 与 `draft_key` 两个唯一约束，并把 `store_id` /
  `shipment_no` / `draft_key` 三个索引建回来（重建表会丢掉原索引）。

两段都是幂等的：形态已经是新的就直接返回，不会每次启动都重建。

## 边界

**所有按 SKU 取装箱产品的地方都必须带 `store_id`**，漏一处就会读到别店的记录：

- `packing_routes._find(db, store_id, sku)`（`_upsert` / 改名 / 迁移包导入都走它）
- `list_products` / `_sync_from_products` / 补图队列 `image_file == ''`
- `shipment_reconciliation.preview()` 的「SKU 不在装箱产品库中」校验
- `shipment_reconciliation.apply()` 落库时取 `name_zh` / `name_en`

反例（已修）：`preview()` 与 `apply()` 原来只按 `sku` 查，A 店的补登会用 B 店的
中英文名写进发货行。

## 前端：只能选在用店铺

`get_store_id` 对非 `active` 店铺一律 400「店铺不存在或已停用」。所以任何
「选一个店铺然后发请求」的下拉都只能列 `active` 店铺：

- React 端 `App.tsx` 的 `store-switcher` 一直是
  `stores.filter(store => store.status === 'active')`。
- 静态装箱工作台（`backend/packing_ui/index.html`）后加店铺下拉时漏了这层过滤，
  直接取 `/api/stores` 全量：选中停用店铺后产品库、导出、上传图片全部 400。
  现在改请求 `/api/stores?active=1`。

`/api/stores` 默认**仍返回全部**：店铺管理页（`Stores.tsx`）要能看到停用店铺并重新启用，
不能默认过滤。

### 当前店铺失效时不许自动跳店

「只列 `active`」还不够：当前生效的店铺（`localStorage` 里那个，每个请求带的
`X-Store-ID`）可能刚被停用或已从列表删除。这时既不能把它从下拉里拿掉（浏览器会把
`select` 显示成空白，而请求仍带着它），也不能自动切到第一家运营店——后者更糟，
界面和请求一起换店，用户会在不知情时对另一家店装箱、改库存。现在的做法是保留当前
店铺并标注「（已暂停）」/「（未在店铺列表中）」，由用户明确选择后再切。
`scripts/test-store-switcher.cjs` 对 `App.tsx` 做源码级断言，防止
`setActiveStore(activeRows[0].id)` 这类自动跳店再被加回来。

## 验证

`backend/tests/test_store_isolation_packing.py`：

- 同 SKU 跨店两条记录、互不覆盖；改 A 店不影响 B 店；按店删除只删本店。
- 发货单号按店唯一：A、B 两店同号可以共存，同店重复插入仍被约束拦住。
- 老库迁移：单列主键 / 单列唯一两种形态迁移后形态正确、老数据归入默认店铺。
- 店铺列表：默认返回全部、`active=1` 只返回在用；静态工作台的下拉必须走 `active=1`。
