# 原 PricePilot 当轮结果同步

状态：接收适配及本地测试已完成，尚未接入原 Mac 的正在运行进程，没有真实回传成功记录。此方式仍依赖原采集机器，不满足原机器关机后的独立采集要求。

此模块不访问 Takealot，不启动浏览器、不导出会话、不修改原数据库、不调用调价接口。它把现有采集进程已经取得的当轮结果转发到选品系统；原进程仍只运行一个实例。用户和员工电脑无需安装程序。

## 为什么不能直接同步 Product 表

用户提供版本的 `server/src/routes/collector.ts` 在失败上报时仍设置 `offersFetchedAt=now`，同时保留旧的 `buyboxPrice`。因此“时间更新且价格非空”不足以证明当轮成功。不要用这两个数据库字段构造新报价，也不要复制对方的价格相等即持有判断。

## 原机器维护者接入（现有源码，无新增依赖）

1. 将本目录 `selector_relay.py` 放到现有 `collector.py` 同一目录。
2. 在现有守护进程的私有环境配置中提供 `SELECTOR_COLLECTOR_KEY`，使用选品系统已有的采集凭据。不能使用原 PricePilot 的另一套凭据；不要将值发在聊天中或提交 Git。模块未配置时直接返回 disabled。
3. 在 `async def run_once_async(...)` 函数开头、`products = fetch_products(...)` 前加：

```python
    selector_round_started = time.time()
```

4. 在该函数的 `if report_items:` 内、原本的上报重试逻辑前加：

```python
        import asyncio
        from selector_relay import forward_round
        selector_result = await asyncio.to_thread(
            forward_round, report_items, selector_round_started
        )
        log(f"选品同步: {selector_result}")
```

5. 由现有服务管理器重启这个守护进程，保留原单实例锁；不要另起 `collector.py --once`。选品网页中需对目标 SKU 开启采集或请求一次采集，实际调价保持关闭。没有待处理任务时 processed=0 是正常的，不能算真实同步成功。

## 行为与验收

模块只使用选品端现有 products/offers/release 鉴权接口。按 SKU 与 PLID 双重匹配当轮结果，并把目标端租约 ID 写入上报；重复来源、错误、变体切换失败或无价格均不导入。使用本轮开始时间作为保守报价时间，不把传输时间当采集时间。超过10分钟、时间缺失或未来时间均拒绝。租约维持现有暂停、库存过滤和超时保护，传输失败只影响同步，不中断原 PricePilot 上报。

先验收一个真实 SKU：日志 processed=1，网页的价格/时间对应本轮，source 为 pricepilot_relay，holding_buybox 保持未知。再观察连续三轮，以及暂停后晚到结果被拒绝、过期后实际调价保持现价。模拟测试不替代以上验收。

已知边界：原程序自己的10分钟轮次决定数据更新速度，网页设置5分钟不会让原程序加速。没有自动解决原 Mac 关机、网络断连、原网站验证或原程序错误。新接收代码没有开启任何实际调价规则。
