"""拦截暂停状态的持久化（WB-103-R6 / WB-103-R7）。

采集器把被验证页/403 拦截的 SKU 计数写入磁盘，重启后继续暂停这些 SKU，
避免重启后立刻重新触发同一风控页。恢复必须是显式的：删除持久化文件，
或由 GUI「恢复被拦截商品」写入恢复标志（运行中的采集器在下一轮生效）。

设计红线（WB-103-R7 第2、3条）：
- 写盘失败/损坏**绝不冒充“无暂停”**：load 损坏抛 ChallengeStateError，
  save 返回 bool，失败不静默成功；调用方保留内存暂停并提示人工核验。
- 恢复标志按 profile 隔离，避免一键误放开其他 profile 的暂停。
- 任何错误都只输出受控提示，**不输出文件路径或敏感内容**。

纯函数、无网络、无 Tk，便于单测与 CI。
"""
import hashlib
import json
import os
from pathlib import Path

RECOVERY_FLAG = 'clear_challenge.flag'


class ChallengeStateError(Exception):
    """拦截暂停状态的读/写不可信；严禁冒充“无暂停”或清空内存暂停。"""


def default_path():
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'TakealotCollector'
    return base / 'challenge_state.json'


def recovery_flag_path(profile=None, state_path=None):
    """恢复标志路径。

    - 无 profile：兼容旧全局标志名 clear_challenge.flag（单 profile 场景）。
    - 有 profile：按 profile 路径稳定哈希命名，避免一键误放开其他 profile 的暂停
      （WB-103-R7 第3条）。哈希只用路径，不落任何敏感内容。
    """
    base = (Path(state_path).parent if state_path else default_path().parent)
    if not profile:
        return base / RECOVERY_FLAG
    slug = hashlib.sha1(str(Path(profile).resolve()).encode('utf-8')).hexdigest()[:12]
    return base / f'clear_challenge.{slug}.flag'


def challenge_state_path(profile=None, state_path=None):
    """拦截暂停状态文件路径（WB-103-R8 第2条：按 profile 隔离）。

    - 无 profile：全局 challenge_state.json（单 profile 场景，兼容旧默认）。
    - 有 profile：按 profile 路径稳定哈希命名 challenge_state.<slug>.json，
      与 recovery_flag_path 共用同一 slug/base，确保「A 的恢复只能清 A 的状态，
      不会误删 B 的状态」。哈希只用路径，不落任何敏感内容。
    """
    base = (Path(state_path).parent if state_path else default_path().parent)
    if not profile:
        return base / 'challenge_state.json'
    slug = hashlib.sha1(str(Path(profile).resolve()).encode('utf-8')).hexdigest()[:12]
    return base / f'challenge_state.{slug}.json'


def load_challenge_state(path=None, profile=None):
    """读取持久化的拦截计数。

    缺失文件 = 从未暂停，安全返回空 dict。文件存在但**损坏/格式非法**则抛
    ChallengeStateError（绝不静默回 {} 冒充“无暂停”；调用方据此保留内存暂停
    并要求人工核验，WB-103-R7 第2条）。只接受合法的 str->非负int 计数。
    """
    path = Path(path) if path else challenge_state_path(profile)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        raise ChallengeStateError(
            '拦截暂停状态文件损坏，无法确认历史暂停；保留内存暂停并需人工核验')
    if not isinstance(data, dict):
        raise ChallengeStateError(
            '拦截暂停状态格式非法，无法确认历史暂停；保留内存暂停并需人工核验')
    clean = {}
    for k, v in data.items():
        # 只接受合法的 str->int 计数（bool 不是 int 计数；负计数无意义须剔除）
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            clean[str(k)] = v
    return clean


def save_challenge_state(state, path=None, profile=None):
    """原子写（tmp + replace），避免读到半截文件。

    成功返回 True；失败（含父路径本身是文件、权限/IO 错误）返回 False，
    **绝不冒充成功**。调用方据返回值保留内存暂停并提示人工核验（WB-103-R7 第2条）。
    """
    path = Path(path) if path else challenge_state_path(profile)
    try:
        parent = path.parent
        # 父路径本身是文件：无法建目录/写盘，明确失败（旧实现静默返回 None）
        if parent.exists() and parent.is_file():
            return False
        parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def clear_challenge_state(path=None, profile=None):
    """显式人工恢复：删除持久化暂停状态。成功返回 True，失败（含不存在/删除异常）返回 False。"""
    path = Path(path) if path else challenge_state_path(profile)
    try:
        if not path.exists():
            return True  # 本就为空，视作已清空成功
        path.unlink()
        return True
    except Exception:
        return False


def write_recovery_flag(profile=None, state_path=None):
    """GUI「恢复被拦截商品」写入恢复标志，供运行中的采集器下一轮消费。

    返回标志路径（创建成功）或 None（创建失败）。调用方据返回值判断是否成功，
    不再吞异常一律报“已请求恢复”（WB-103-R7 第3条）。
    """
    flag = recovery_flag_path(profile, state_path)
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        return flag
    except Exception:
        return None


def consume_recovery_flag(state_path=None, profile=None):
    """消费恢复标志：只有“标志确实存在且被成功删除、且持久化暂停已成功清空”
    才返回 True；任一失败返回 False 并保持暂停（WB-103-R7 第3条 / WB-103-R8 第3条）。

    调用方据此决定是否清空内存暂停——失败时必须保留暂停，不误放开。

    保守顺序（WB-103-R8 第3条）：**先删除恢复标志，再清空持久化暂停**。
    若标志删除失败，直接返回 False 且磁盘状态原样不动（不丢暂停）；
    若标志已删但状态清空失败，返回 False，磁盘状态仍在，重启后 load 仍读回暂停
    （不丢暂停），仅本次恢复未生效，可重试——绝不接受“标志删除失败却已清空状态”的丢暂停。
    """
    state_path = Path(state_path) if state_path else challenge_state_path(profile)
    flag = recovery_flag_path(profile, state_path)
    if not flag.exists():
        return False
    # 先删除恢复标志；失败则保持暂停（磁盘状态原样）并返回 False
    try:
        flag.unlink()
    except Exception:
        return False
    # 标志已删除；清空持久化暂停。失败也返回 False，但磁盘状态仍在，重启后读回暂停。
    if not clear_challenge_state(state_path):
        return False
    return True
