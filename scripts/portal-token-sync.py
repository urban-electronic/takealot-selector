#!/usr/bin/env python3
"""把卖家门户令牌从本机 Chrome 自动同步到线上后端。

为什么需要它
------------
一键上架要经卖家门户目录接口（seller-api.takealot.com/1/catalogue/mpv）取
Barcode、主图、长宽高，而那个接口只认买家后台登录时下发的 JWT
（localStorage 里的 `usr_st_auth` → `api_key`）。这个令牌 24 小时过期，门户
没有提供刷新接口——以前的流程是「过期了 → F12 复制 → 回页面粘贴」，每天
一次，忘一次就整批上架失败。

可是令牌本来就躺在 Chrome 的 LevelDB 里：只要这台机器上登录过
sellers.takealot.com，浏览器每次续期都会把新令牌写回 localStorage。所以
"手取"这一步完全可以省掉——本脚本读 LevelDB、比到期时间、比线上新就推上去。

为什么不放后端做
----------------
令牌来自**这台机器的浏览器登录态**，而线上后端（Railway）在容器里，看不到
本机 Chrome 的数据目录。所以同步动作必须发生在 Mac 这一侧：由 launchd 每
30 分钟跑一次本脚本（见 scripts/install-portal-token-sync.sh）。

什么时候仍然需要人动手
----------------------
浏览器里的令牌本身就是最新的一份，脚本只能"搬运"不能"续期"。如果 Chrome
里那份也已过期（连续 24 小时没打开过卖家门户），脚本会以退出码 2 结束并
（加 --notify 时）弹系统通知，提示打开一次 sellers.takealot.com 即可。

用法
----
    ZENTY_API_KEY=xxx python3 scripts/portal-token-sync.py            # 正常同步
    python3 scripts/portal-token-sync.py --dry-run                    # 只看本地令牌与决策
    python3 scripts/portal-token-sync.py --save-key xxx               # 把 API Key 存在本机配置里
    python3 scripts/portal-token-sync.py --notify                     # 失败时弹 macOS 通知

退出码：0 成功或无需更新 / 2 需要人打开一次门户 / 3 缺 API Key / 4 网络或接口错误
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_API_BASE = "https://takealot-selector-production.up.railway.app"
PORTAL_ORIGIN = "sellers.takealot.com"
CONFIG_PATH = Path.home() / ".zenty" / "portal-token-sync.json"

# 实测 Chrome 的 LocalStorage 用 LevelDB 存：key 是
#   `_https://sellers.takealot.com\x00\x01usr_st_auth`
# value 是 `{varint 长度}{1 字节编码标记}{内容}`。编码标记 0x01 = Latin-1，
# 0x00 = UTF-16LE。这里两条路都走：先按 WriteBatch 结构解析，失败再退化成
# 直接在明文里找 `"api_key":"eyJ..."`——LevelDB 的 .log 未压缩，肉眼可见。
JWT_RE = re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{5,}")


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def fingerprint(token: str) -> str:
    """令牌指纹。日志里只出现这个，绝不打印令牌本身。"""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def jwt_payload(token: str) -> Dict[str, object]:
    """只解载荷、不验签——这里只需要 exp 做比较。"""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(body))
    except Exception:
        return {}


def token_exp(token: str) -> Optional[int]:
    exp = jwt_payload(token).get("exp")
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def read_varint(buf: bytes, index: int) -> Tuple[Optional[int], int]:
    shift = 0
    value = 0
    while index < len(buf):
        byte = buf[index]
        value |= (byte & 0x7F) << shift
        index += 1
        if not byte & 0x80:
            return value, index
        shift += 7
        if shift > 35:
            break
    return None, index


def decode_cell(buf: bytes, index: int) -> Optional[str]:
    """按 LocalStorage 的编码标记解一格值。"""
    if index >= len(buf):
        return None
    flag = buf[index]
    if flag == 0x01:  # Latin-1 / UTF-8 兼容
        end = buf.find(b"\x00", index + 1)
        if end == -1:
            end = min(len(buf), index + 1 + 4096)
        return buf[index + 1:end].decode("utf-8", "replace")
    if flag == 0x00:  # UTF-16LE
        end = index + 1
        while end + 1 < len(buf) and not (buf[end] == 0 and buf[end + 1] == 0):
            end += 2
        return buf[index + 1:end].decode("utf-16-le", "replace")
    return None


def leveldb_files(profile_dir: Path) -> Iterable[Path]:
    storage = profile_dir / "Local Storage" / "leveldb"
    if not storage.is_dir():
        return []
    files = [f for f in storage.iterdir() if f.is_file() and f.suffix in (".log", ".ldb")]
    # .log 是新写入的增量，最新令牌几乎总在这里；按修改时间倒序能更早命中。
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def tokens_from_file(path: Path) -> List[str]:
    """从一个 LevelDB 文件里捞 portal 令牌。

    同一条链路上会同时存在结构性解析与明文兜底两种结果，去重后一起返回——
    .log 里可能留着好几代令牌（旧值不会被物理删除），调用方取 exp 最大的那份。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    # 注意：不能因为文件里没有明文的 usr_st_auth / api_key 就跳过——LevelDB 的
    # .ldb 是 snappy 按块压缩的，压缩块里那几个键名根本看不见，但 JWT 本身可能
    # 落在同一块的未压缩字面量里。之前这道门槛会把最新的那份令牌整个漏掉。
    if b"eyJ" not in data and b"usr_st_auth" not in data and b"api_key" not in data:
        return []

    found: List[str] = []

    # 路径一：按 WriteBatch 结构 (key \x00\x01 usr_st_auth) 精确定位。
    for match in re.finditer(rb"usr_st_auth", data):
        index = match.end()
        length, after = read_varint(data, index)
        if length and 0 < length < 200_000:
            cell = decode_cell(data, after)
            if cell and cell.lstrip().startswith("{"):
                try:
                    api_key = json.loads(cell).get("api_key")
                except Exception:
                    api_key = None
                if isinstance(api_key, str) and api_key.count(".") == 2:
                    found.append(api_key)

    # 路径二：明文兜底。结构解析碰不上时（跨块、被截断），直接找 JWT 串。
    for match in JWT_RE.finditer(data):
        candidate = match.group().decode("utf-8", "replace")
        payload = jwt_payload(candidate)
        # 门户令牌带 sub（卖家 id）；这条限制用来排除同文件里别家的 JWT。
        if payload.get("sub") and candidate not in found:
            found.append(candidate)

    return found


def chrome_profiles(chrome_dir: Path) -> List[Path]:
    if not chrome_dir.is_dir():
        return []
    profiles = []
    for child in sorted(chrome_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name == "Default" or child.name.startswith("Profile "):
            profiles.append(child)
    return profiles


def chrome_dirs(explicit: Optional[str]) -> List[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    home = Path.home()
    candidates = [
        home / "Library/Application Support/Google/Chrome",
        home / "Library/Application Support/Google/Chrome Beta",
        home / "Library/Application Support/Microsoft Edge",
        home / "Library/Application Support/Chromium",
        home / "Library/Application Support/BraveSoftware/Brave-Browser",
    ]
    return [c for c in candidates if c.is_dir()]


def newest_local_token(dirs: List[Path]) -> Tuple[Optional[str], Optional[Path]]:
    """在所有浏览器/所有 profile 里找 exp 最大的那份令牌。"""
    best: Tuple[int, str, Path] = (0, "", Path())
    seen: set = set()
    for root in dirs:
        for profile in chrome_profiles(root):
            for path in leveldb_files(profile):
                for token in tokens_from_file(path):
                    if token in seen:
                        continue
                    seen.add(token)
                    exp = token_exp(token) or 0
                    if exp > best[0]:
                        best = (exp, token, path)
    if not best[1]:
        return None, None
    return best[1], best[2]


def load_config() -> Dict[str, str]:
    try:
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    except Exception:
        return {}


def save_config(config: Dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), "utf-8")
    # 里面有 API Key，只给本人读写。
    os.chmod(CONFIG_PATH, 0o600)


def resolve_api_key(cli_key: Optional[str], config: Dict[str, str]) -> str:
    for candidate in (
        cli_key,
        os.environ.get("ZENTY_API_KEY"),
        os.environ.get("TAKEALOT_SELECTOR_API_KEY"),
        config.get("api_key"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return ""


def api_call(base: str, path: str, key: str, payload: Optional[Dict[str, str]] = None,
             timeout: float = 30.0) -> Tuple[int, Dict[str, object]]:
    url = base.rstrip("/") + path
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method="PUT" if payload is not None else "GET",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": key,
            "X-Store-ID": "default-store",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            data = {"detail": raw[:200].decode("utf-8", "replace")}
        return error.code, data


def notify(title: str, message: str) -> None:
    """失败时给一次系统通知——launchd 没有前台，日志没人看。"""
    if platform.system() != "Darwin":
        return
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def fmt_time(epoch: Optional[int]) -> str:
    if not epoch:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def refresh_via_browser(dirs: List[Path], stale_before: int, timeout: float = 60.0) -> Optional[str]:
    """令牌快过期时，让浏览器自己去续一次，再从 localStorage 读回来。

    为什么是"打开真浏览器"而不是无头浏览器：令牌是门户登录态下发的，无头浏览器
    没有那份登录 cookie，只会被踢回登录页。而用户自己的 Chrome 里登录态是热的，
    打开页面就等于完成续期——这一步不模拟登录、不填任何凭据，只是把用户本来会
    手动做的事交给定时任务触发。

    代价是会弹出一个标签页，所以默认不启用，要 --open-browser 或配置里打开。
    """
    if platform.system() != "Darwin":
        return None
    url = f"https://{PORTAL_ORIGIN}/"
    try:
        # -g：在后台打开，不把 Chrome 抢到前台，免得定时任务在你干活时切走焦点。
        subprocess.run(["open", "-g", "-a", "Google Chrome", url], check=False, timeout=20)
    except Exception as error:
        log(f"打开浏览器失败：{error}")
        return None
    log("已让 Chrome 打开卖家门户续期，等待 localStorage 更新…")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(4)
        token, _ = newest_local_token(dirs)
        if token:
            exp = token_exp(token) or 0
            if exp > stale_before:
                log(f"浏览器已续出新令牌，到期 {fmt_time(exp)}")
                return token
    log("等待超时，浏览器里还没出现更新的令牌（可能需要重新登录）")
    return None


def sync_one(base: str, key: str, token: str, exp: int, force: bool, notify_on_error: bool) -> int:
    """把某个目标后端的令牌更新到本机这份。返回退出码语义的整数。"""
    label = base or "(同源)"
    status, state = api_call(base, "/api/selection/portal/token", key)
    if status == 401:
        log(f"[{label}] 后端拒绝了 API Key（401）。确认它和部署端的 API_KEY 环境变量一致。")
        if notify_on_error:
            notify("门户令牌自动同步", f"{label} 拒绝了 API Key")
        return 3
    if status != 200:
        log(f"[{label}] 读令牌状态失败：HTTP {status} {state.get('detail', '')}")
        if notify_on_error:
            notify("门户令牌自动同步", f"{label} 读状态失败（HTTP {status}）")
        return 4

    remote_exp = state.get("expires_at")
    remote_exp = int(remote_exp) if isinstance(remote_exp, (int, float)) else None
    log(f"[{label}] 线上当前：{'已配置' if state.get('configured') else '未配置'}，到期 {fmt_time(remote_exp)}")

    # 线上不比本机旧就不写：脚本每 30 分钟跑一次，绝大多数时候空转，没必要
    # 每次都覆盖一遍加密字段。
    if not force and remote_exp and remote_exp >= exp - 60:
        log(f"[{label}] 不比本机旧，无需同步。")
        return 0

    status, saved = api_call(base, "/api/selection/portal/token", key, {"token": token})
    if status != 200:
        log(f"[{label}] 写入失败：HTTP {status} {saved.get('detail', '')}")
        if notify_on_error:
            notify("门户令牌自动同步", f"{label} 写入失败（HTTP {status}）")
        return 4

    log(f"[{label}] 已同步：到期 {fmt_time(int(saved.get('expires_at') or 0))}，剩余 {saved.get('days_left')} 天")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="自动同步卖家门户令牌（Chrome → 后端）")
    parser.add_argument("--api-base", default=os.environ.get("ZENTY_API_BASE") or None,
                        help=f"后端地址，默认 {DEFAULT_API_BASE}；多次传可同步到多个后端")
    parser.add_argument("--api-key", default=None, help="API Key（也可用 ZENTY_API_KEY 环境变量或 --save-key 存本机）")
    parser.add_argument("--save-key", default=None, help="把 API Key 写入 ~/.zenty/portal-token-sync.json 后退出")
    parser.add_argument("--chrome-dir", default=None, help="指定浏览器用户数据目录（默认自动找 Chrome/Edge/Chromium）")
    parser.add_argument("--dry-run", action="store_true", help="只读本机令牌并打印决策，不请求、不写入")
    parser.add_argument("--notify", action="store_true", help="需要人工介入时弹 macOS 通知")
    parser.add_argument("--open-browser", action="store_true",
                        help="令牌快过期时让 Chrome 打开卖家门户自动续期（会弹出标签页）")
    parser.add_argument("--force", action="store_true", help="即使线上令牌不旧也重新写入")
    args = parser.parse_args()

    config = load_config()
    if args.save_key:
        config["api_key"] = args.save_key.strip()
        save_config(config)
        log(f"API Key 已保存到 {CONFIG_PATH}（权限 600）")
        return 0

    # 目标后端：网页版走 Railway，桌面版另有一套本机后端；两者可以都同步。
    targets: List[str] = []
    for candidate in ([args.api_base] if args.api_base else []) + list(config.get("api_bases") or []):
        if candidate and candidate not in targets:
            targets.append(candidate)
    if not targets:
        targets = [DEFAULT_API_BASE]

    dirs = chrome_dirs(args.chrome_dir)
    if not dirs:
        log("没找到 Chrome/Edge/Chromium 的用户数据目录，换一台机器或者用 --chrome-dir 指定")
        return 2

    token, source = newest_local_token(dirs)
    exp = token_exp(token) if token else None
    now = int(time.time())
    # 剩余不到 1 小时就当作"该续了"：留一小时余量，免得正好卡在上架途中过期。
    stale = (token is None) or (exp or 0) - now < 3600
    auto_open = args.open_browser or bool(config.get("auto_open_browser"))

    if stale and auto_open and not args.dry_run:
        refreshed = refresh_via_browser(dirs, exp or 0)
        if refreshed:
            token, exp = refreshed, token_exp(refreshed)
            source = None
            stale = False

    if not token:
        log("本机浏览器里没有 sellers.takealot.com 的 usr_st_auth —— 需要在这台机器上登录一次卖家门户")
        if args.notify:
            notify("门户令牌自动同步", "本机浏览器里找不到门户登录态，请打开 sellers.takealot.com 登录一次")
        return 2

    where = f"（来自 {source.parent.parent.parent.name}/{source.name}）" if source else ""
    log(f"本机最新令牌 {fingerprint(token)} {where}，到期 {fmt_time(exp)}")

    if exp and exp <= now:
        log("本机这份令牌也已过期 —— 打开一次 sellers.takealot.com 让浏览器续期，脚本下次就能自动搬到线上")
        if not auto_open:
            log("（想让它自己续期：加 --open-browser，或在 ~/.zenty/portal-token-sync.json 里设 \"auto_open_browser\": true）")
        if args.notify:
            notify("门户令牌自动同步", "浏览器里的令牌已过期，请打开 sellers.takealot.com 登录一次")
        return 2

    if args.dry_run:
        log("--dry-run：不请求接口、不写库。")
        return 0

    key = resolve_api_key(args.api_key, config)
    if not key:
        log("缺少 API Key。先存一次即可（之后长期有效）：")
        log("    python3 scripts/portal-token-sync.py --save-key <你的 API Key>")
        log("也可以用环境变量 ZENTY_API_KEY 传入。")
        return 3

    status_codes = [sync_one(base, key, token, exp or 0, args.force, args.notify) for base in targets]
    # 全部成功返回 0；有一个失败就把最严重的那个退出码带出去，方便 launchd 日志排查。
    for code in (4, 3):
        if code in status_codes:
            return code
    return 0 if all(code == 0 for code in status_codes) else 4


if __name__ == "__main__":
    sys.exit(main())
