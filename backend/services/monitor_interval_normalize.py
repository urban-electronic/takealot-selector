"""把库里存量的采集间隔抬到云端最小间隔（30 分钟）。

为什么需要单独一段启动归一，而不是只改代码里的下限：

`interval_minutes` 在库里是一份**历史快照**——用户当年在界面上选的是 5 分钟，
这一行就一直写着 5。代码里的下限（`effective_interval_minutes`）能保证「实际按
30 分钟排期」，但库里那个 5 仍会被三处当成事实读出去：

  ① 界面 `loop.interval_minutes`（取启用记录的众数）直接显示「每 5 分钟一轮」，
     和实际节奏不符，用户以为降频没生效；
  ② 「本轮耗时 vs 设定间隔」的告警会拿 5 分钟比较，把健康的一轮报成严重逾期；
  ③ 下次有人在界面上点保存，前端把读到的 5 原样提交回来，脏值继续传播。

所以这里在启动时做一次**幂等**归一：只把 `< 30` 改成 `30`，`>= 30` 一个字不碰
（用户主动选的 60/120 是有效配置，不能被抹平），其它字段（`next_due`、
`status`、`last_*`、`revision` 等）一律不动——改排期字段会让所有 SKU 立刻到期，
等于又一次全量采集，正是本次要消除的行为。

重复执行零副作用：只有值真的发生变化时才写回、才 commit。
"""
import json

from models import SystemSettings
from database import SessionLocal
from services.buybox_monitor import MIN_INTERVAL_MINUTES, PREFIX


def normalize_monitor_intervals(db=None, floor=MIN_INTERVAL_MINUTES, commit=True):
    """把 `_pricing_monitor:*` 里 `interval_minutes < floor` 的记录改成 floor。

    返回统计字典，供启动日志与测试断言使用：
      scanned  扫描到的 monitor 记录数
      changed  本次真正改写的条数（幂等：第二次跑必为 0）
      already  已经是 >= floor 的条数（含用户主动选的 60/120）
      unset    `interval_minutes` 缺失/null 的条数（读路径按默认值兜，不改库）
      invalid  JSON 解析失败或不是对象的条数（坏数据不猜、不覆盖）
      samples  被改写的样例 `[{'key','from','to'}]`（最多 5 条，仅用于日志取证）
    """
    own_session = db is None
    session = SessionLocal() if own_session else db
    stats = {'scanned': 0, 'changed': 0, 'already': 0, 'unset': 0, 'invalid': 0, 'samples': []}
    try:
        rows = session.query(SystemSettings).filter(SystemSettings.key.startswith(PREFIX)).all()
        for row in rows:
            stats['scanned'] += 1
            try:
                value = json.loads(row.value)
            except (TypeError, ValueError):
                stats['invalid'] += 1
                continue
            if not isinstance(value, dict):
                stats['invalid'] += 1
                continue
            current = value.get('interval_minutes')
            if isinstance(current, bool) or not isinstance(current, int):
                # null / 字符串 / 缺失：不写库。读路径的 normalize_interval_minutes
                # 会按 30 兜底，这里改了反而把「用户没设置」写成「用户选了 30」。
                stats['unset'] += 1
                continue
            if current >= floor:
                stats['already'] += 1
                continue
            value['interval_minutes'] = floor
            row.value = json.dumps(value, ensure_ascii=False)
            stats['changed'] += 1
            if len(stats['samples']) < 5:
                stats['samples'].append({'key': row.key, 'from': current, 'to': floor})
        if commit and stats['changed']:
            session.commit()
    except Exception:
        if commit:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()
    return stats


def main():
    """手工执行入口：`python -m services.monitor_interval_normalize`。"""
    stats = normalize_monitor_intervals()
    print(f"[normalize] 采集间隔归一 扫描 {stats['scanned']} 条，改写 {stats['changed']} 条，"
          f"已是 {MIN_INTERVAL_MINUTES} 分钟以上 {stats['already']} 条，"
          f"未设置 {stats['unset']} 条，坏数据 {stats['invalid']} 条", flush=True)
    for sample in stats['samples']:
        print(f"[normalize] {sample['key']} {sample['from']} → {sample['to']} 分钟", flush=True)


if __name__ == '__main__':
    main()
