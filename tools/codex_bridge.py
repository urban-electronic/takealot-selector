#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 协作桥：读取 PR 新反馈 / 发送回复。

协议要点（按 Codex WB-103-R3 第 5 条修正）：
- **按 kind 分别维护游标**：GitHub 的 issue 评论、行内评审、评审正文是**三套独立 ID 序列**，
  单游标会把 ID 较小的行内评审误判为「已读」而漏掉（原实现缺陷）；
- **分页拉取**：每类按 per_page=100 逐页拉完，避免长期运行后漏评论；
- 纳入 `pull-request-review` 正文事件；
- state 保存**原子化**（tmp + os.replace），失败不谎报成功；
- 路径可通过环境变量配置，不再硬编码本机绝对路径；
- **读全正文不截断**（原截断 8000 后再 ACK 会漏掉后半段要求）。

用法：
  python codex_bridge.py                       # 读新反馈（不推进游标）
  python codex_bridge.py --all                 # 显示全部（不含自身回复）
  python codex_bridge.py --ack issue:123 review:45   # 执行完后按 kind 推进游标
  python codex_bridge.py --reply "正文"
  python codex_bridge.py --pr 94               # 默认 103
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

OWNER = "urban-electronic"
REPO = "takealot-selector"
DEFAULT_PR = 103
BRANCH = "fix/native-collector-loop-status"
MARKER = "<!-- codex-bridge:auto -->"
HERE = Path(__file__).resolve().parent


def workspace_dir():
    env = os.environ.get("TAKEALOT_WORKSPACE")
    return Path(env) if env else HERE


def repo_root():
    env = os.environ.get("TAKEALOT_REPO")
    if env:
        return Path(env)
    if (HERE.parent / "backend").is_dir():
        return HERE.parent
    cand = HERE / "takealot-selector"
    return cand if cand.is_dir() else HERE


STATE_FILE = workspace_dir() / "codex_bridge_state.json"
# 与定时任务触发频率一致；自检据此判断轮询是否停滞（超过 3 倍间隔即告警）。
EXPECTED_INTERVAL_MINUTES = 15


def get_token():
    out = subprocess.run(["git", "credential", "fill"],
                         input="protocol=https\nhost=github.com\n",
                         capture_output=True, text=True, cwd=str(repo_root())).stdout
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
        if e.code in (429, 403) and _attempt < 3:
            time.sleep(5 * (2 ** _attempt))
            return api(method, path, data, _attempt + 1)
        raise


def api_paged(path):
    """分页拉完某一类评论，避免只取前 100 条而漏评论。"""
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        batch = api("GET", f"{path}{sep}per_page=100&page={page}") or []
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1
        if page > 50:
            return out


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    """原子写：先写临时文件再替换，失败不谎报成功。"""
    fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as exc:
        print("WARN 保存 state 失败:", repr(exc)[:200])
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False


def record_poll(state, pr):
    """记录本次轮询时刻（心跳）。只写轮询时间，**不推进任何游标**——
    游标推进仍须显式 --ack，保持「先执行、后标记」的协议。"""
    state["poll"] = {
        "last_poll_at": datetime.now(timezone.utc).isoformat(),
        "pr": pr,
        "interval_minutes": EXPECTED_INTERVAL_MINUTES,
    }
    if not save_state(state):
        print("WARN: 轮询心跳未持久化，自检将无法判断频率")


def cursor_of(state, pr, kind):
    raw = state.get(str(pr))
    if isinstance(raw, dict):
        return int(raw.get(kind, 0) or 0)
    if isinstance(raw, int) and kind == "issue":
        return raw
    return 0


def set_cursor(state, pr, kind, value):
    cur = state.get(str(pr))
    if not isinstance(cur, dict):
        cur = {"issue": cur if isinstance(cur, int) else 0}
    cur[kind] = max(int(cur.get(kind, 0) or 0), int(value))
    state[str(pr)] = cur


SOURCES = [
    ("issue", lambda pr: f"/issues/{pr}/comments"),
    ("review", lambda pr: f"/pulls/{pr}/comments"),
    ("review_body", lambda pr: f"/pulls/{pr}/reviews"),
]


def collect(pr):
    """按 kind 分别拉取并过滤自身回复；返回 {kind: [items]}。"""
    state = load_state()
    posted = set(state.get("posted", []))
    buckets = {}
    for kind, path_fn in SOURCES:
        items = []
        for c in api_paged(path_fn(pr)):
            body = c.get("body") or ""
            if c.get("id") in posted or MARKER in body:
                continue
            if kind == "review_body" and not body.strip():
                continue
            items.append({
                "kind": kind, "id": c["id"], "at": c.get("created_at"),
                "user": (c.get("user") or {}).get("login", "?"),
                "path": f"{c.get('path')}:{c.get('line') or c.get('original_line')}" if kind == "review" else None,
                "state": c.get("state") if kind == "review_body" else None,
                "body": body,
            })
        items.sort(key=lambda x: x["id"])
        buckets[kind] = items
    return state, buckets


def fetch(pr, show_all):
    state, buckets = collect(pr)
    record_poll(state, pr)
    printed = 0
    tops = {}
    for kind, items in buckets.items():
        last = cursor_of(state, pr, kind)
        new = items if show_all else [i for i in items if i["id"] > last]
        for i in new:
            printed += 1
            loc = f"  [{i['path']}]" if i["path"] else ""
            extra = f"  state={i['state']}" if i["state"] else ""
            print("-" * 72)
            print(f"[{i['at']}] {i['user']} · {kind}{loc}{extra}  id={i['id']}")
            print(i["body"])
        if items:
            tops[kind] = max(i["id"] for i in items)

    if not printed:
        cursors = {k: cursor_of(state, pr, k) for k, _ in SOURCES}
        print(f"NO_NEW_FEEDBACK: PR #{pr} 各类游标 {cursors} 以来均无新评论。")
        return 0

    print("-" * 72)
    if not show_all:
        ack_args = " ".join(f"{k}:{v}" for k, v in tops.items() if v > cursor_of(state, pr, k))
        print("PENDING_ACK: 先执行任务，完成后按 kind 分别推进游标：")
        print(f"  python codex_bridge.py --pr {pr} --ack {ack_args}")
        print("（协议：先执行、后推进；不得先标记已处理再执行）")
    return 0


def ack(pr, tokens):
    state = load_state()
    for token in tokens:
        kind, _, value = token.partition(":")
        if not value:
            kind, value = "issue", kind
        if kind not in dict(SOURCES):
            print(f"SKIP 未知 kind: {kind}")
            continue
        set_cursor(state, pr, kind, int(value))
    if save_state(state):
        print(f"STATE_UPDATED: PR #{pr} 游标 -> {state.get(str(pr))}")
    else:
        print("STATE_NOT_SAVED: 游标未持久化，请勿视为已处理")
    return 0


def reply(pr, body):
    res = api("POST", f"/issues/{pr}/comments", {"body": body + "\n" + MARKER})
    state = load_state()
    posted = set(state.get("posted", []))
    posted.add(res.get("id"))
    state["posted"] = sorted(posted)[-500:]
    if not save_state(state):
        print("WARN: posted 记录未持久化，下次可能把自身回复当新反馈")
    print("REPLIED to PR #%d id=%s" % (pr, res.get("id")))
    return 0


def selftest(pr, expected_minutes=None):
    """自我校验：逐项验证协作链路健康度。任一 FAIL → 退出码 1（供定时任务判定）。"""
    global TOKEN
    expected = int(expected_minutes or EXPECTED_INTERVAL_MINUTES)
    results = []
    head = {"sha": "?"}

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, repr(exc)[:200]
        results.append((bool(ok), name, str(detail)))

    def _token():
        return bool(get_token()), "已从本机凭据库取得（不打印、不落盘）"
    check("凭据 token", _token)

    def _repo():
        r = api("GET", "")
        return r.get("full_name") == f"{OWNER}/{REPO}", r.get("full_name", "?")
    check("仓库可达", _repo)

    def _branch():
        ref = api("GET", f"/git/ref/heads/{BRANCH}")
        head["sha"] = ref["object"]["sha"][:10]
        return True, head["sha"]
    check("分支 head", _branch)

    def _state_rw():
        original = load_state()
        probe = dict(original)
        probe["__probe__"] = "1"
        try:
            wrote = save_state(probe)
            back = load_state()
        finally:
            cleaned = dict(original)
            cleaned.pop("__probe__", None)
            save_state(cleaned)
        return wrote and back.get("__probe__") == "1", "原子写(tmp+replace)往返正常"
    check("state 原子写", _state_rw)

    def _poll():
        poll = (load_state().get("poll") or {})
        ts = poll.get("last_poll_at")
        if not ts:
            return True, "尚无轮询记录（首次运行后自动生成）"
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 60
        limit = expected * 3
        if age > limit:
            return False, f"轮询停滞 {age:.0f} 分钟（阈值 {limit}）"
        return True, f"最近轮询 {age:.0f} 分钟前（间隔 {expected} 分钟）"
    check("轮询心跳", _poll)

    def _cursors():
        raw = load_state().get(str(pr))
        if raw is None:
            return True, "无游标（尚未读过）"
        if isinstance(raw, dict):
            bad = {k: v for k, v in raw.items() if not isinstance(v, int)}
            return (not bad), (f"{raw}" if not bad else f"非法游标值 {bad}")
        if isinstance(raw, int):
            return True, f"旧格式单游标 {raw}（读取时自动按 kind 迁移）"
        return False, f"未知游标格式 {type(raw).__name__}"
    check("游标 schema", _cursors)

    def _kinds():
        counts = {k: len(api_paged(fn(pr))) for k, fn in SOURCES}
        return True, ", ".join(f"{k}={v}" for k, v in counts.items())
    check("三类评论可拉取", _kinds)

    def _posted():
        n = len(load_state().get("posted", []))
        return True, f"已记录自身回复 {n} 条（用于防循环）"
    check("自身回复过滤", _posted)

    print("=== codex_bridge 自检 ===")
    fails = sum(1 for ok, _, _ in results if not ok)
    for ok, name, detail in results:
        print(f"{'OK  ' if ok else 'FAIL'} {name}: {detail}")
    print("-" * 60)
    print(f"结果：{'全部通过' if not fails else str(fails) + ' 项失败'}  (PR #{pr}, head {head['sha']})")
    return 1 if fails else 0


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, default=DEFAULT_PR)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ack", nargs="*", help="kind:id，如 issue:123 review:45")
    ap.add_argument("--reply")
    ap.add_argument("--selftest", action="store_true", help="自我校验协作链路健康度")
    ap.add_argument("--interval", type=int, default=None, help="期望轮询间隔（分钟），供心跳校验")
    args = ap.parse_args()

    TOKEN = get_token()
    if args.selftest:
        return selftest(args.pr, args.interval)
    if args.reply:
        return reply(args.pr, args.reply)
    if args.ack is not None:
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
