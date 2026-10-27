// 加载态与空态放在一个文件里，是因为它们回答的是同一个问题：
// 「这里现在没内容，是还没到，还是本来就没有」。
//
// 之前全站一律是居中一行「加载中...」。数据从 Railway 回来要几百毫秒到一秒多，
// 这期间页面高度只有 20px；数据到了表格一次撑开四五百像素，每次切页视线都要跳一下。
// 骨架先按最终形状占位，数据到了只是「填色」，不再重排。
//
// 骨架里不放文字：占位条本身就是「这里有东西」的意思，写上字反而要读两遍。
// 无障碍靠 role="status" + aria-label，读屏会念一次「正在载入…」。

import type { CSSProperties, ReactNode } from 'react';

const box = (w: number | string, h: number): CSSProperties => ({ width: w, height: h });

/** 一根占位条。宽度是布局数据（每行长短不一才像真内容），所以走内联。 */
export function SkLine({ w = '100%', h = 10 }: { w?: number | string; h?: number }) {
  return <span className="sk" style={box(w, h)} />;
}

// 每行宽度错开，避免整列变成一根等宽的灰条——那样看着像加载失败。
const ROW_WIDTHS = ['72%', '86%', '64%', '79%', '90%', '68%'];

export function SkTable({ rows = 8, label }: { rows?: number; label?: string }) {
  return (
    <div
      className="sk-table"
      {...(label ? { role: 'status', 'aria-label': label, 'aria-busy': true } : null)}
    >
      <div className="sk-table-head">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
          <SkLine key={i} w={i < 3 ? 14 : '62%'} h={8} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div className="sk-row" key={r}>
          <SkLine w={14} h={14} />
          <SkLine w={26} h={11} />
          <SkLine w={18} h={11} />
          <SkLine w={ROW_WIDTHS[r % ROW_WIDTHS.length]} h={12} />
          <SkLine w="70%" h={11} />
          <SkLine w="56%" h={11} />
          <SkLine w="48%" h={11} />
          <SkLine w="44%" h={11} />
        </div>
      ))}
    </div>
  );
}

/** 统计卡一类的横排小块。 */
export function SkCards({ count = 3 }: { count?: number }) {
  return (
    <div className="sk-cards">
      {Array.from({ length: count }, (_, i) => (
        <div className="sk-card" key={i}>
          <SkLine w="42%" h={10} />
          <SkLine w="58%" h={22} />
          <SkLine w="76%" h={10} />
        </div>
      ))}
    </div>
  );
}

/** 整页加载：标题 + 副标题 + 一张表。用于页面级 `if (loading) return ...`。 */
export function SkPage({ rows = 8, label = '正在载入页面' }: { rows?: number; label?: string }) {
  return (
    <div className="sk-page" role="status" aria-label={label} aria-busy="true">
      <div className="sk-page-head">
        <SkLine w={190} h={22} />
        <SkLine w={260} h={10} />
      </div>
      <SkTable rows={rows} />
    </div>
  );
}

/** 工作台：三张统计卡 + 一条通栏 + 左宽右窄两块。 */
export function SkDashboard({ label = '正在载入工作台' }: { label?: string }) {
  return (
    <div className="sk-page" role="status" aria-label={label} aria-busy="true">
      <div className="sk-page-head">
        <SkLine w={190} h={22} />
        <SkLine w={300} h={10} />
      </div>
      <SkCards count={3} />
      <div className="sk-panel">
        <SkLine w="28%" h={12} />
        <SkLine w="52%" h={10} />
      </div>
      <div className="sk-columns">
        <div className="sk-panel">
          <SkLine w="26%" h={12} />
          <SkLine w="100%" h={72} />
          <SkLine w="100%" h={72} />
        </div>
        <div className="sk-panel">
          <SkLine w="34%" h={12} />
          <SkLine w="100%" h={132} />
        </div>
      </div>
    </div>
  );
}

/**
 * 空态。跟加载态分开：这里是「确实没有」，所以给一句为什么、一个下一步，
 * 而不是继续晃占位条让人等一个不会来的结果。
 */
export function EmptyState({ title, description, action }: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <p className="empty-state-title">{title}</p>
      {description && <p className="empty-state-desc">{description}</p>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}
