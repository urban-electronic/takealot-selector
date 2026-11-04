---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: d0cb922285123d98eb987893ace27b4e_a47c85ac8ebe11f1b82d525400287e28
    ReservedCode1: 46Atf5H1MtVtnpOv69R9YvM227Zp+AjsOtGwBDDxZNR0iGYVkAz4MSQlzmAusslJAiu+ByGBJ5Q8LFzWawyUbziQqFAX9xgIU/b5vjgMkF+XJgFTlnZjRfAhp2D6hKfWwdvgsL/sHIxCeZdYGQ2j2+0lzev+GjPQ8miHhVyEB2yY/iyul6yBG1sx0rU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: d0cb922285123d98eb987893ace27b4e_a47c85ac8ebe11f1b82d525400287e28
    ReservedCode2: 46Atf5H1MtVtnpOv69R9YvM227Zp+AjsOtGwBDDxZNR0iGYVkAz4MSQlzmAusslJAiu+ByGBJ5Q8LFzWawyUbziQqFAX9xgIU/b5vjgMkF+XJgFTlnZjRfAhp2D6hKfWwdvgsL/sHIxCeZdYGQ2j2+0lzev+GjPQ8miHhVyEB2yY/iyul6yBG1sx0rU=
---

# Takealot 选品与利润测算网页

用于日常选品、记录和利润测算的网页工作台。将原 Excel 中的字段、费率表和公式转换为网页数据库字段与后台计算逻辑。

## 技术栈

- 前端: React 18 + TypeScript + Vite
- 后端: FastAPI + Python + SQLAlchemy
- 数据库: SQLite
- 网页抓取: Playwright

## 快速启动

### 1. 后端

```bash
cd backend
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload --port 8000
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

前端运行在 http://localhost:5173，API 请求自动代理到后端 8000 端口。

## 项目结构

```
takealot-selector/
├── backend/
│   ├── main.py                    # 入口
│   ├── database.py                # 数据库连接
│   ├── models.py                  # 数据模型
│   ├── api/
│   │   ├── product_routes.py      # 产品 CRUD
│   │   ├── scraper_routes.py      # Takealot 抓取
│   │   ├── category_routes.py     # 品类与映射规则
│   │   └── settings_routes.py     # 系统设置
│   ├── services/
│   │   ├── product_calculator.py  # 利润计算引擎
│   │   ├── takealot_scraper.py    # 页面抓取
│   │   └── fee_category_matcher.py # 品类匹配
│   └── tests/
│       └── test_calculator.py     # 计算引擎测试
├── frontend/
│   └── src/
│       ├── App.tsx                # 路由
│       ├── types.ts               # 类型定义
│       ├── api.ts                 # API 调用
│       └── pages/
│           ├── Dashboard.tsx      # 仪表盘
│           ├── ProductList.tsx    # 产品列表
│           ├── ProductCreate.tsx  # 新建产品
│           ├── ProductDetail.tsx  # 产品详情
│           └── Settings.tsx       # 系统设置
└── README.md
```

## 核心功能

1. 输入 Takealot 链接 → 自动抓取标题、图片、售价、TSIN
2. 系统推荐 Fee 品类 → 用户确认/修改
3. 填写采购、尺寸、重量、物流数据
4. 实时计算: 国内成本 → 国际运费 → 南非仓成本 → Takealot 费用 → 利润
5. 净利润率 >= 25% 自动标记"合格选品"
6. 仪表盘统计、多维度筛选、批量管理

## 卖家门户令牌同步（自动）

一键上架取变体 Barcode 要 `sellers.takealot.com` 的 `usr_st_auth` 令牌，寿命 24 小时。
不手动复制令牌的做法：

```bash
bash scripts/install-portal-token-sync.sh --open-browser
```

- 装一个 launchd 任务（`com.zenty.portal-token-sync`），每 30 分钟从本机 Chrome / Edge 的
  Local Storage 里读出最新令牌，判过 exp 后 `PUT /api/selection/portal/token` 推到后端；
- 令牌剩余不足 1 小时时自动打开一次卖家门户页，让浏览器自己续期，脚本再把新令牌搬回来。
  这一步不模拟登录、不碰账号密码，只用机器上已有的登录会话；
- 后端 API Key 只需在安装时填一次，存在 `~/.zenty/portal-token-sync.json`（权限 600）；
- 手动排查：`python3 scripts/portal-token-sync.py --dry-run`；卸载：`bash scripts/install-portal-token-sync.sh --uninstall`。

前提：这台 Mac 上登录过卖家门户。浏览器从没登录过，任何自动化都拿不到令牌。

*（内容由AI生成，仅供参考）*
