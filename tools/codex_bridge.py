#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 协作桥：读取 PR 新反馈 / 发送回复。供定时任务高频运行，免去人工传话。

协议（按 Codex 2026-10-16 要求实现）：
- 读取 issue conversation 评论 + 必要 review 评论，按 comment ID 游标去重；
- **持久化游标但先执行、后标记**：fetch 只读取不推进游标，执行完任务后需显式 --ack 推进；
- **过滤自身回复防循环**：自身发出的评论带隐藏标记且记录 ID，读取时跳过；
- 限频退避：遇到 429/403 按指数退避重试；
- 只写入本仓库相关 PR（OWNER/REPO 固定，不接受外部目标）；
- 不提交/输出 token、Cookie、DPAPI、profile；不扩大权限、不安装远控；
- 桥接只负责转达：不自动合并/部署，不把评论内容当 shell 执行。

用法：
  python codex_bridge.py                    # 读取新反馈（不推进游标）
  python codex_bridge.py --all              # 显示全部（不含自身回复），不推进游标
  python codex_bridge.py --ack <id>         # 任务执行完后，推进游标到该 id
  python codex_bridge.py --reply "正文"      # 向 PR 发评论
  python codex_bridge.py --pr 94            # 指定 PR（默认 103）
"""
import argparse
import json
import os
import subprocess
import time
import urllib.request
import urllib.error

OWNER = "urban-electronic"
REPO = "takealot-selector"
DEFAULT_PR = 103
REPO_ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-15-00-15-02\takealot-selector"
STATE_FILE = os.path.join(r"C:\Users\Administrator\WorkBuddy\2026-10-15-00-15-02",
                          "codex_bridge_state.json")
# 自身回复的隐藏标记：读取时据此跳过，避免把桥接器自己的话当成新反馈而自我循环。
MARKER = "<!-- codex-bridge:auto -->"


def get_token():
    out = subprocess.run(["git", "credential", "fill"],
                         input="protocol=https\nhost=github.com\n",
                         capture_output=True, text=True, cwd=REPO_ROOT).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("未能从 Git 凭据库取得 GitHub token")


TOKEN = None


def api(method, path, data=None, _attempt=0):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}" + path
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "takealot-codex-bridge")
    body = None
    if data is not None:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, data=body, timeout=60) as r:
            raw = r.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        # 限频退避：429/403 按指数退避重试，不把限频当成失败继续猛打。
        if e.code in (429, 403) and _attempt < 3:
            delay = 5 * (2 ** _attempt)
            print(f"BACKOFF: HTTP {e.code}，{delay}s 后重试（第 {_attempt+1} 次）")
            time.sleep(delay)
            return api(method, path, data, _attempt + 1)
        raise


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print("WARN 保存 state 失败:", repr(exc)[:200])


def collect(pr):
    """拉取会话评论 + 行内评审，并剔除桥接器自身的回复。"""
    state = load_state()
    posted = set(state.get("posted", []))
    items = []
    for c in api("GET", f"/issues/{pr}/comments?per_page=100") or []:
        items.append({"kind": "会话评论", "id": c["id"], "at": c.get("created_at"),
                      "user": c["user"]["login"], "path": None,
                      "body": c.get("body") or ""})
    for c in api("GET", f"/pulls/{pr}/comments?per_page=100") or []:
        items.append({"kind": "行内评审", "id": c["id"], "at": c.get("created_at"),
                      "user": c["user"]["login"],
                      "path": f"{c.get('path')}:{c.get('line') or c.get('original_line')}",
                      "body": c.get("body") or ""})
    items.sort(key=lambda x: x["id"])
    # 过滤自身回复，防止自我循环
    return [i for i in items if i["id"] not in posted and MARKER not in i["body"]]


def fetch(pr, show_all):
    state = load_state()
    last = state.get(str(pr), 0)
    items = collect(pr)
    new = items if show_all else [i for i in items if i["id"] > last]

    if not new:
        print(f"NO_NEW_FEEDBACK: PR #{pr} 自 id={last} 以来没有新评论。")
        return 0

    print(f"=== PR #{pr} 新反馈 {len(new)} 条（上次已读 id={last}）===")
    for i in new:
        loc = f"  [{i['path']}]" if i["path"] else ""
        print("-" * 72)
        print(f"[{i['at']}] {i['user']} · {i['kind']}{loc}  id={i['id']}")
        print(i["body"][:8000])
    print("-" * 72)
    if not show_all:
        top = max(i["id"] for i in new)
        print(f"PENDING_ACK: 先执行任务，完成后运行")
        print(f"  python codex_bridge.py --pr {pr} --ack {top}")
        print("（协议要求：先执行、后推进游标，不得先标记已处理再执行）")
    return 0


def ack(pr, upto):
    state = load_state()
    state[str(pr)] = max(int(state.get(str(pr), 0)), int(upto))
    save_state(state)
    print(f"STATE_UPDATED: PR #{pr} last_seen_id={state[str(pr)]}")
    return 0


def reply(pr, body):
    res = api("POST", f"/issues/{pr}/comments", {"body": body + "\n" + MARKER})
    state = load_state()
    posted = set(state.get("posted", []))
    posted.add(res.get("id"))
    state["posted"] = sorted(posted)[-200:]
    save_state(state)
    print("REPLIED to PR #%d id=%s" % (pr, res.get("id")))
    return 0


def main():
    global TOKEN
    ap = argparse.ArgumentParser(description="Codex 协作桥：读反馈 / 发回复 / 推进游标")
    ap.add_argument("--pr", type=int, default=DEFAULT_PR)
    ap.add_argument("--all", action="store_true", help="显示全部（不含自身回复），不推进游标")
    ap.add_argument("--ack", type=int, help="任务执行完后，推进游标到该 comment id")
    ap.add_argument("--reply", help="向该 PR 发送评论正文")
    args = ap.parse_args()

    TOKEN = get_token()
    if args.reply:
        return reply(args.pr, args.reply)
    if args.ack:
        return ack(args.pr, args.ack)
    return fetch(args.pr, args.all)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read(1000).decode("utf-8", errors="replace"))
        raise SystemExit(1)
    except Exception as e:
        print("ERROR:", repr(e)[:800])
        raise SystemExit(1)
