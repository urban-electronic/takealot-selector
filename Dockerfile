# ---------- 阶段 1：构建前端 ----------
# 网页版由后端同源提供（见 main.py 末尾的静态托管），所以构建产物必须进镜像。
# 这样用户访问的是 Railway 域名，不再依赖 GitHub Pages。
FROM node:20-slim AS web

WORKDIR /web

# 先只拷清单，装依赖这一步才能吃到 Docker 层缓存。
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
COPY public ./public

# 千万不要设 GH_PAGES：那个开关会把 base 改成 '/takealot-selector/'（给 GitHub
# Pages 的子路径用）。这里挂在域名根路径，设了会导致所有资源 404。
#
# VITE_API_SAME_ORIGIN=1：前端改用相对路径请求接口，跟页面同源。不设的话它会
# 用硬编码的 Railway 域名，换域名就全挂。
RUN VITE_API_SAME_ORIGIN=1 npm run build

# ---------- 阶段 2：运行时 ----------
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# curl_cffi build dependencies
RUN rm -rf /var/lib/apt/lists/* && apt-get update && apt-get install -y --no-install-recommends \
    libcurl4-openssl-dev build-essential xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# 前端构建产物：main.py 检测到 dist/index.html 存在才会开启网页版托管，
# 不存在时行为与之前完全一致（只提供 API）。
COPY --from=web /web/dist ./dist
# 404.html 是 GitHub Pages 专用的 SPA 回退文件；这里的回退由 main.py 处理，
# 留着只会让 /404.html 返回一份过期副本。
RUN rm -f dist/404.html

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
