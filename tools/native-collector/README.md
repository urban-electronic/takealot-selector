# 原生 PricePilot 网页接入采集器

## Windows独立启动，不需要WorkBuddy

电脑需安装Python 3.10以上。解压后双击 `start.cmd`：首次自动安装独立运行环境和Chromium，之后打开“Takealot采集”操作界面。界面内可选择原 `browser_profile`、填写网站API Key、点击“登录/配送设置”“开始采集”“暂停采集”“打开系统”。无需在命令窗口操作。登录完成后点“完成登录并保存”，再点“开始采集”。

依赖、浏览器档案和所选档案路径保存在当前用户的 `TakealotCollector` 本机目录。勾选“加密记住连接配置”时，服务密钥经Windows当前账户DPAPI加密后保存，只需首次填写；取消勾选后不保存。服务密钥不会写入源码或下载包。非Windows不支持该加密保存选项。无需WorkBuddy，不依赖聊天会话。网页仍用于SKU采集调度和结果展示。关闭操作窗口会先停止并释放未执行任务，再退出。

安装修正版使用Playwright 1.64.0与greenlet 3.5.6的预编译包，避免旧greenlet在较新Python上触发Visual C++源码编译。按Python版本建立独立运行环境，保留原浏览器档案和选定路径；旧版安装失败时，重新下载本包并双击start.cmd重试即可，无需安装C++编译工具。不支持的系统会停止安装，不能自动回退源码编译。

此包直接包含仓库 `backend/services/pricepilot_collector.py` 的解析核心，不另写简化解析器。保留原颜色/尺寸 URL 参数、精确变体点击与确认、双读/三读报价一致性、Buybox 卡和其他卖家读取。Windows/macOS 使用同一 Python 入口。仅回传行情，没有售价写入端点。

1. 安装 Python 3.10 或以上，在解压目录执行：
   `python -m pip install -r requirements.txt`
   `python -m playwright install chromium`
2. 停止原 PricePilot 采集器，保留原目录的 `browser_profile`。用它的绝对路径运行：
   `python collector.py --profile "原browser_profile的绝对路径"`
3. 提示时输入选品系统的服务访问密钥（不是 Takealot 商家 API 密钥）；输入不回显、不保存。
4. 在网页“Buybox与调价”中对需要的 SKU 点“立即采集”或开启常态采集。采集器每30秒领取到期任务，逐商品回传，调价开关独立。采集器运行可持续后台采集，不依赖系统网页一直打开；网页扩展则仍是打开网页时采集。

没有原档案或会话失效时，先执行 `python collector.py --login --profile "档案绝对路径"`，在可见浏览器里完成验证、登录及南非配送地址设置，按回车保存后再启动采集。不复制Cookie，不自动解决验证。不要同时使用原采集器和新采集器打开同一档案。

所有未执行任务会释放租约；单商品最多180秒，整轮最多600秒；失败不作为行情成功，不开启真实调价。相同价格不再被当作已核实卖家归属。断连后由服务器租约回收任务。

本包验证范围是原解析核心打包一致性、任务回传/释放和模拟页面测试。实际旧机器会话是否仍有效、真实报价与变体仍需在那台电脑运行核对；下载上线不代表真实采集成功。
