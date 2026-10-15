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
import threading
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


def load_state(path=None):
    """读取状态 JSON；path 缺省时用模块级 STATE_FILE。独立态文件（自检/测试）可传 path。"""
    target = path or STATE_FILE
    try:
        with open(target, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state, path=None):
    """原子写：先写临时文件再替换，失败不谎报成功。path 缺省时用 STATE_FILE。"""
    target = path or STATE_FILE
    fd, tmp = tempfile.mkstemp(dir=str(Path(target).parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return True
    except Exception as exc:
        print("WARN 保存 state 失败:", repr(exc)[:200])
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False


# --- 跨进程互斥：避免并发 ACK / fetch / reply 在「读-改-写」之间丢失更新 ---
try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None
try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None

_STATE_LOCK = STATE_FILE.with_name(STATE_FILE.name + ".lock")
# 进程内互斥：桥接脚本始终单进程运行，threading.Lock 足以串行化本进程内的
# ACK/fetch/reply；文件锁再叠加一层跨进程互斥（best-effort）。
_PROC_LOCK = threading.Lock()


def _open_lock():
    return open(str(_STATE_LOCK), "a+")


def _acquire_lock(lockf, timeout=10.0, step=0.05):
    """获取跨进程文件锁，返回 (acquired, backend)。

    - backend 为 None 表示两个平台锁后端（msvcrt / fcntl）均不可用，
      此时无法保证跨进程串行化 → 必须 fail closed，调用方不得写文件。
    - 获取失败（被占用超时 / 后端异常）返回 (False, backend)。
    使用非阻塞锁 + 有限重试实现超时，避免永久阻塞事件循环（原 LK_LOCK 会无限阻塞）。
    """
    if _msvcrt is not None:
        try:
            lockf.seek(0)
            deadline = time.time() + timeout
            while True:
                try:
                    _msvcrt.locking(lockf.fileno(), _msvcrt.LK_NBLCK, 1)
                    return True, "msvcrt"
                except OSError:
                    if time.time() >= deadline:
                        return False, "msvcrt"
                    time.sleep(step)
        except Exception:
            return False, "msvcrt"
    if _fcntl is not None:
        try:
            deadline = time.time() + timeout
            while True:
                try:
                    _fcntl.flock(lockf.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    return True, "fcntl"
                except OSError:
                    if time.time() >= deadline:
                        return False, "fcntl"
                    time.sleep(step)
        except Exception:
            return False, "fcntl"
    # 两个平台锁后端均不可用：无法保证跨进程串行化 → fail closed
    return False, None


def _release_lock(lockf):
    try:
        if _msvcrt is not None:
            lockf.seek(0)
            _msvcrt.locking(lockf.fileno(), _msvcrt.LK_UNLCK, 1)
        elif _fcntl is not None:
            _fcntl.flock(lockf.fileno(), _fcntl.LOCK_UN)
    except Exception:
        pass


def atomic_state_update(mutator):
    """Load → mutate → persist 串行化：进程内 threading.Lock + 跨进程文件锁。

    **失败即关闭（fail closed）**：若跨进程文件锁不可用（后端缺失或获取超时/失败），
    直接返回 False 且**绝不写文件**、绝不谎称「串行化成功」。调用方据此判定状态未
    持久化（如游标未推进），不得误当作已处理。原子替换(tmp+replace)只能避免半文件，
    不能防「读-改-写」交错；这里用双重锁把整段读改写串行化，杜绝并发丢更新
    （PR103 桥接并发状态修复；WB-103-R5 第 1 条）。"""
    with _PROC_LOCK:
        lockf = _open_lock()
        acquired, backend = _acquire_lock(lockf)
        if not acquired:
            try:
                lockf.close()
            except Exception:
                pass
            print("WARN: 跨进程文件锁不可用(%s)，atomic_state_update fail closed（未写文件）"
                  % ("无可用锁后端" if backend is None else backend))
            return False
        try:
            state = load_state()
            mutator(state)
            return save_state(state)
        finally:
            _release_lock(lockf)
            try:
                lockf.close()
            except Exception:
                pass


class StateStore:
    """带路径的状态存储，支持独立 state 文件（如自检临时态 / 跨进程测试）。

    与模块级全局 STATE_FILE 解耦：probe 与测试用独立 StateStore 实例读写自己的文件，
    **绝不重新赋值全局 STATE_FILE**，因此同进程并发 ACK/record_poll（写真实游标）与
    probe（写临时态）互不干扰，真实游标不会因 probe 中途被删临时文件而丢更新
    （WB-103-R5 第 2 条）。锁逻辑与 atomic_state_update 一致（进程内 Lock + 跨进程
    文件锁，fail closed）。
    """

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.lock_file = self.path.with_name(self.path.name + ".lock")

    def load(self):
        return load_state(self.path)

    def save(self, state):
        return save_state(state, self.path)

    def atomic_update(self, mutator, timeout=10.0):
        """返回 (saved: bool, fail_reason: str|None)。锁不可用则 (False, reason)。"""
        with self.lock:
            lockf = open(str(self.lock_file), "a+")
            acquired, backend = _acquire_lock(lockf, timeout=timeout)
            if not acquired:
                try:
                    lockf.close()
                except Exception:
                    pass
                return False, ("no_lock_backend" if backend is None else backend)
            try:
                state = self.load()
                mutator(state)
                return self.save(state), None
            finally:
                _release_lock(lockf)
                try:
                    lockf.close()
                except Exception:
                    pass


def state_rw_probe():
    """自检「state 原子写」：用独立 StateStore（临时态文件）往返，不触碰真实协作游标、不修改全局。

    真实协作游标由模块级原子写持有；probe 使用完全独立的临时文件 + 独立锁，
    即便本进程此刻有并发 ACK/record_poll 在写真实游标，也不互相干扰、不丢更新。"""
    import tempfile as _tf
    tmp = _tf.NamedTemporaryFile("w+", suffix=".json", delete=False, dir=str(STATE_FILE.parent))
    tmp.close()
    try:
        store = StateStore(Path(tmp.name))
        ok1 = store.save({"__seed__": 1})
        ok2 = store.save({"__seed__": 1, "__probe__": "1"})
        back = store.load()
        ok = bool(ok1) and bool(ok2) and back.get("__probe__") == "1"
        detail = "原子写(tmp+replace)往返正常（独立临时态文件，不触碰真实游标）"
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        try:
            os.unlink(tmp.name + ".lock")
        except Exception:
            pass
    return ok, detail


def record_poll(pr):
    """记录本次轮询时刻（心跳）。只写轮询时间，**不推进任何游标**——
    游标推进仍须显式 --ack，保持「先执行、后标记」的协议。"""
    def _set(s):
        s["poll"] = {
            "last_poll_at": datetime.now(timezone.utc).isoformat(),
            "pr": pr,
            "interval_minutes": EXPECTED_INTERVAL_MINUTES,
        }
    if not atomic_state_update(_set):
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
    record_poll(pr)
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
    updated = {}

    def _mut(s):
        for token in tokens:
            kind, _, value = token.partition(":")
            if not value:
                kind, value = "issue", kind
            if kind not in dict(SOURCES):
                print(f"SKIP 未知 kind: {kind}")
                continue
            set_cursor(s, pr, kind, int(value))
            updated[kind] = cursor_of(s, pr, kind)
    if atomic_state_update(_mut):
        print(f"STATE_UPDATED: PR #{pr} 游标 -> {updated}")
    else:
        print("STATE_NOT_SAVED: 游标未持久化，请勿视为已处理")
    return 0


def reply(pr, body):
    res = api("POST", f"/issues/{pr}/comments", {"body": body + "\n" + MARKER})

    def _mut(s):
        posted = set(s.get("posted", []))
        posted.add(res.get("id"))
        s["posted"] = sorted(posted)[-500:]
    if not atomic_state_update(_mut):
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
        return state_rw_probe()
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
