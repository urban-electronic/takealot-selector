"""拦截暂停状态的持久化（WB-103-R6）。

采集器把被验证页/403 拦截的 SKU 计数写入磁盘，重启后继续暂停这些 SKU，
避免重启后立刻重新触发同一风控页。恢复必须是显式的：删除持久化文件，
或由 GUI「恢复被拦截商品」写入恢复标志（运行中的采集器在下一轮生效）。

纯函数、无网络、无 Tk，便于单测与 CI。
"""
import json
import os
from pathlib import Path

RECOVERY_FLAG = 'clear_challenge.flag'


def default_path():
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'TakealotCollector'
    return base / 'challenge_state.json'


def load_challenge_state(path=None):
    """读取持久化的拦截计数；缺失/损坏返回空 dict（降级不抛异常）。"""
    path = Path(path) if path else default_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {}
    for k, v in data.items():
        # 只接受合法的 str->int 计数（bool 不是 int 计数）
        if isinstance(v, int) and not isinstance(v, bool):
            clean[str(k)] = v
    return clean


def save_challenge_state(state, path=None):
    """原子写（tmp + replace），避免读到半截文件。"""
    path = Path(path) if path else default_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)
    except Exception:
        pass


def clear_challenge_state(path=None):
    """显式人工恢复：删除持久化暂停状态。"""
    path = Path(path) if path else default_path()
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def consume_recovery_flag(path=None):
    """存在恢复标志即清空持久化暂停并返回 True（显式人工恢复，运行中的采集器下一轮生效）。"""
    base = (Path(path) if path else default_path()).parent
    flag = base / RECOVERY_FLAG
    if flag.exists():
        try:
            flag.unlink(missing_ok=True)
        except Exception:
            pass
        clear_challenge_state(path)
        return True
    return False
