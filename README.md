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
*（内容由AI生成，仅供参考）*
