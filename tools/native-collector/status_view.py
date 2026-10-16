"""采集器状态 -> 可展示视图的纯逻辑。

独立成模块（不 import tkinter），好处：
- 可在无 Tk / 无显示的环境下单元测试与 CI 运行；
- GUI 只负责渲染，判定逻辑可独立回归（WB-103-R2 第 3 条：陈旧状态隔离）。
"""
from datetime import datetime, timezone

# state -> (文案, 颜色)。running 为轮次「在途」阶段，与上一轮汇总区分。
STATE_VIEW = {
    'running':       ('执行中…', '#0066cc'),
    'idle':          ('待机（暂无到期任务）', '#888888'),
    'collecting_ok': ('采集中', '#007a00'),
    'challenged':    ('遇风控，已退避等待', '#b06000'),
    'failed':        ('本轮未取得有效报价', '#b06000'),
    'challenge_state_unreliable': ('拦截状态不可信，需人工核验', '#b00000'),
}
STARTING = ('启动中…', '#0066cc')
WAITING = ('等待本轮结果…', '#888888')
STOPPED = ('已停止', '#888888')
# 状态超过该时长未更新即视为陈旧，不再当作「当前」展示。
STALE_AFTER_SECONDS = 300
# 允许的时钟前移容差：来自未来的时间戳同样不可信。
FUTURE_SKEW_SECONDS = 60


def classify_state(data, pid, last_round=0, last_run_id='', now=None):
    """把采集器状态文件判定为可展示的 (文本, 颜色, round, run_id)。

    校验链（任一不满足即返回中性「等待本轮结果」，绝不保留绿色旧结果）：
    1. pid 与当前子进程一致；
    2. run_id 与本次启动绑定（已知 run_id 后，不同 run_id = 上一次运行的残留）；
    3. 时间戳既不过期也不来自未来；
    4. round 必须是非负整数，且不倒退。
    全程不抛异常：非法 schema 只降级为中性，避免打断 GUI 的 poll 循环。
    """
    start = (STARTING[0], STARTING[1], last_round, last_run_id)
    stale = (WAITING[0], WAITING[1], last_round, last_run_id)

    if not isinstance(data, dict) or data.get('pid') != pid:
        return start
    if last_run_id and data.get('run_id') != last_run_id:
        return stale
    try:
        when = datetime.fromisoformat(data.get('ts') or '')
        if when.tzinfo is None:
            raise ValueError('naive ts')
    except Exception:
        return start
    now = now or datetime.now(timezone.utc)
    delta = (now - when).total_seconds()
    if delta > STALE_AFTER_SECONDS or delta < -FUTURE_SKEW_SECONDS:
        return stale
    rnd = data.get('round')
    # round='oops' / None / 负数 / bool 一律不信任，且不得抛 TypeError
    if isinstance(rnd, bool) or not isinstance(rnd, int) or rnd < 0:
        return stale
    if rnd < last_round:
        return stale
    state = data.get('state') or ''
    text, color = STATE_VIEW.get(state, STARTING)
    claimed = data.get('claimed')
    if isinstance(claimed, int) and claimed > 0 and state != 'running':
        text = f"{text} · 本轮领取 {claimed} / 成功 {data.get('successful', 0)} / 失败 {data.get('failed', 0)}"
    return (text, color, max(rnd, last_round), data.get('run_id') or last_run_id)
