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
}
STARTING = ('启动中…', '#0066cc')
WAITING = ('等待本轮结果…', '#888888')
STOPPED = ('已停止', '#888888')
# 状态超过该时长未更新即视为陈旧，不再当作「当前」展示。
STALE_AFTER_SECONDS = 300


def classify_state(data, pid, last_round=0, last_run_id='', now=None):
    """把采集器状态文件判定为可展示的 (文本, 颜色, round, run_id)。

    校验链：pid 一致 → 时间戳不陈旧 → 轮次不倒退。任一不满足即返回中性的
    「等待本轮结果」，从而不会把上一轮（或上一次运行）的绿色成功结果当成现在。
    """
    if not isinstance(data, dict) or data.get('pid') != pid:
        return (STARTING[0], STARTING[1], last_round, last_run_id)
    try:
        when = datetime.fromisoformat(data.get('ts') or '')
        if when.tzinfo is None:
            raise ValueError('naive ts')
    except Exception:
        return (STARTING[0], STARTING[1], last_round, last_run_id)
    now = now or datetime.now(timezone.utc)
    if (now - when).total_seconds() > STALE_AFTER_SECONDS:
        return (WAITING[0], WAITING[1], last_round, last_run_id)
    rnd = data.get('round') or 0
    if rnd < last_round:
        return (WAITING[0], WAITING[1], last_round, last_run_id)
    state = data.get('state') or ''
    text, color = STATE_VIEW.get(state, STARTING)
    claimed = data.get('claimed')
    if isinstance(claimed, int) and claimed > 0 and state != 'running':
        text = f"{text} · 本轮领取 {claimed} / 成功 {data.get('successful', 0)} / 失败 {data.get('failed', 0)}"
    return (text, color, max(rnd, last_round), data.get('run_id') or last_run_id)
