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
    libcurl4-openssl-dev build-essential xvfb tini \
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

# 必须有一个真正的 init 当 PID 1。
#
# 2026-10-21 线上故障的根因就在这里：容器里 PID 1 是 uvicorn，而 Playwright 每次
# 启动/停止浏览器都会产生一批 chromium 子进程；它们被 reparent 到 PID 1 后退出，
# 却没有人 wait()，于是全变成僵尸进程。僵尸不占内存，但**占 pid**——
# `/api/diag/runtime` 实测 45 分钟内僵尸从 10 涨到 45，chrome + chrome_crashpad
# 同步堆积；攒到 cgroup 的 pids.max（1000）之后，连 DNS 解析要起个线程都会
# `RuntimeError: can't start new thread`，表现就是 `/api/image-proxy` 全线 500。
#
# tini 作为 PID 1 负责回收所有孤儿进程，僵尸不再累积。它同时转发信号，
# 让 Railway 的 SIGTERM 能正常传到 uvicorn（原来直接发给了 shell 层的子进程）。
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
