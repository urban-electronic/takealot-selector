import { useEffect, useRef, useState } from 'react';
import { exportListingReview, getActiveStoreId, getListingDrafts, importListingTable,
  reviewListingDraft, saveListingDraft, type ListingDraft, type ListingPage } from '../remoteApi';
import { beijingDateTime } from '../utils/datetime';
import './ListingWorkbench.css';

const fields = [
  ['english_name', '完整英文品名'], ['title', '主标题（最多75字符）'], ['subtitle', '副标题（最多110字符）'],
  ['major_colour', 'Major Colour（面积最大的主色）'], ['other_colours', 'Other Colours（其他颜色，用 / 分隔）'],
  ['packaged_length_cm', '包装后长（cm）'], ['packaged_width_cm', '包装后宽（cm）'],
  ['packaged_height_cm', '包装后高（cm）'], ['packaged_weight_kg', '包装后重量（kg）'],
  ['sales_unit', '销售单位'], ['country_of_origin', '产地'], ['material', '材质'],
  ['brand', '实际品牌'], ['category_id', '后台类目ID'], ['category_path', '后台类目路径'],
] as const;
const largeFields = [
  ['description', '英文描述（保留 Key Features 小标题）'], ['whats_in_box', "What's in the Box"],
  ['image_urls', '上架图片链接（主图在第一行，随后至少4张详情图）'],
  ['features', '供应商卖点事实'], ['usage', '适用场景'], ['notes', '核对提示 / 备注'],
] as const;
type Edit = Record<string, string>;
const editOf = (draft: ListingDraft): Edit => Object.fromEntries(Object.entries(draft.payload)
  .filter(([key]) => !['raw_name', 'supplier_code', 'colour_candidates', 'import_conflicts'].includes(key))
  .map(([key, value]) => [key, Array.isArray(value) ? value.join(key === 'image_urls' ? '\n' : ' / ') : String(value ?? '')]));

export default function ListingWorkbench() {
  const [store] = useState(getActiveStoreId);
  const [data, setData] = useState<ListingPage | null>(null);
  const [page, setPage] = useState(1), [search, setSearch] = useState(''), [query, setQuery] = useState('');
  const [state, setState] = useState(''), [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [selected, setSelected] = useState<Record<string, number>>({});
  const [opened, setOpened] = useState<ListingDraft | null>(null), [edit, setEdit] = useState<Edit>({});
  const [dirty, setDirty] = useState(false);
  const generation = useRef(0), mounted = useRef(true), lock = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null), editor = useRef<HTMLHeadingElement>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    const mine = ++generation.current;
    setLoading(true); setError('');
    getListingDrafts(store, page, search, state).then(result => {
      if (mine !== generation.current) return;
      const last = Math.max(1, Math.ceil(result.total / result.page_size));
      if (page > last) { setPage(last); return; }
      setData(result);
    }).catch(e => { if (mine === generation.current) setError((e as Error).message); })
      .finally(() => { if (mine === generation.current) setLoading(false); });
    return () => { generation.current++; };
  }, [store, page, search, state, revision]);
  const refresh = () => { generation.current++; setRevision(x => x + 1); };
  const action = async (work: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    try { await work(); }
    catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : '操作失败，请重试'); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  };
  const open = (draft: ListingDraft) => {
    if (dirty && !window.confirm('有未保存的修改，放弃后打开另一款？')) return;
    setOpened(draft); setEdit(editOf(draft)); setDirty(false);
    requestAnimationFrame(() => editor.current?.focus());
  };
  const save = () => action(async () => {
    if (!opened) return;
    const payload: Record<string, unknown> = {...edit};
    for (const name of ['packaged_length_cm', 'packaged_width_cm', 'packaged_height_cm', 'packaged_weight_kg']) {
      const val = edit[name].trim();
      if (val && (!Number.isFinite(Number(val)) || Number(val) <= 0)) {
        document.getElementById(`listing-${name}`)?.focus();
        throw new Error('包装重尺须为大于0的数字；不知道可以留空，不能用0代替');
      }
      payload[name] = val ? Number(val) : null;
    }
    payload.other_colours = edit.other_colours.split(/[/,，、;；\n]+/).map(x => x.trim()).filter(Boolean);
    payload.image_urls = edit.image_urls.split('\n').map(x => x.trim()).filter(Boolean);
    const result = await saveListingDraft(store, opened.id, opened.revision, payload);
    if (!mounted.current) return;
    setOpened(result); setEdit(editOf(result)); setDirty(false); setSelected({});
    setNotice('资料已保存。修改后的资料需要重新审核。'); refresh();
  });
  const review = () => action(async () => {
    if (!opened || dirty) return;
    const result = await reviewListingDraft(store, opened.id, opened.revision);
    if (!mounted.current) return;
    setOpened(result); setNotice('已记录本次人工审核；尚未向平台提交。'); refresh();
  });
  const upload = (files: FileList | null) => action(async () => {
    if (!files?.length) return;
    if (files.length > 20) throw new Error('一次最多选择20份回填表');
    const totals = {created: 0, updated: 0, unchanged: 0, conflicts: 0};
    let completed = 0;
    try {
      for (const file of Array.from(files)) {
        if (!mounted.current) return;
        const result = await importListingTable(store, file);
        for (const key of Object.keys(totals) as (keyof typeof totals)[]) totals[key] += result[key];
        completed++;
        if (mounted.current) setNotice(`已处理 ${completed}/${files.length} 份表…`);
      }
      if (mounted.current) setNotice(`已处理${completed}份表：新增${totals.created}款，补齐${totals.updated}款，未变${totals.unchanged}款，差异待核对${totals.conflicts}款。`);
    } catch (e) {
      throw new Error(`已成功导入${completed}份；后续文件未完成：${(e as Error).message}`);
    } finally {
      if (mounted.current) { setSelected({}); setPage(1); refresh(); if (fileInput.current) fileInput.current.value = ''; }
    }
  });
  const download = () => action(async () => {
    const blob = await exportListingReview(store, selected);
    if (!mounted.current) return;
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = '已审核上架资料.xlsx'; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 30000);
    setNotice('已下载审核资料包；这是内部资料，不是官方批量上架模板，也不代表平台已接收。');
  });
  return <div className="listing-workbench page-container">
    <div className="page-header"><h2>上架工作台</h2><span className="muted">资料准备 · 人工审核</span></div>
    <p className="muted">上架资料独立于定价。运费尚未定稿不影响准备链接；当前入口不改动产品列表、库存或真实售价。</p>
    <div className="operations-note">本期已接入回填表与审核队列。自动图文制作、后台类目匹配和官方提交尚未接通；当前不会把内部导出标成上架成功。</div>
    <div className="card listing-toolbar">
      <label>供应商回填表<input ref={fileInput} type="file" accept=".xlsx" multiple disabled={busy} onChange={e => void upload(e.target.files)} /></label>
      <button type="button" className="btn btn-outline" disabled={busy || loading} onClick={refresh}>刷新草稿</button>
      <button type="button" className="btn btn-primary" disabled={busy || !Object.keys(selected).length} onClick={() => void download()}>下载已审核资料（{Object.keys(selected).length}）</button>
      <small className="muted">支持已发出的无图回填表；每份不超过10MB，最多20份。</small>
    </div>
    {error && <p className="error-message" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <form className="listing-toolbar" onSubmit={e => {e.preventDefault(); setPage(1); setSearch(query.trim()); setSelected({});}}>
      <input aria-label="搜索编号或商品" autoComplete="off" placeholder="编号 / 品名…" value={query} onChange={e => setQuery(e.target.value)} />
      <button type="submit" className="btn btn-outline">搜索</button>
      <label>审核状态<select value={state} onChange={e => {setState(e.target.value); setPage(1); setSelected({});}}><option value="">全部</option><option value="draft">待准备 / 待审核</option><option value="reviewed">已人工审核</option></select></label>
      <span className="muted">{data?.total ?? 0}款</span>
    </form>
    <div className="inventory-table-wrap" aria-busy={loading || busy}>
      <table className="inventory-table listing-table"><thead><tr><th>导出</th><th>编号 / 原货号</th><th>商品</th><th>主色 / 其他颜色</th><th>资料 / 审核</th><th>操作</th></tr></thead>
        <tbody>{data?.items.map(row => <tr key={row.id}>
          <td><input type="checkbox" aria-label={`选择${row.internal_code}`} disabled={busy || row.state !== 'reviewed'} checked={Boolean(selected[row.id])} onChange={e => setSelected(before => {const next = {...before}; if (e.target.checked) next[row.id] = row.revision; else delete next[row.id]; return next;})} /></td>
          <td>{row.internal_code}<small>{row.payload.supplier_code}</small></td>
          <td className="listing-name">{row.payload.english_name || row.payload.raw_name}<small>{row.payload.raw_name}</small></td>
          <td>{row.payload.major_colour || '主色待确认'}<small>{row.payload.other_colours.join(' / ') || '其他颜色未填'}</small></td>
          <td>{row.state === 'reviewed' ? '已人工审核' : row.issues.length ? `${row.issues.length}项待补` : '待人工审核'}<small>{beijingDateTime(row.updated_at)}</small></td>
          <td><button type="button" className="btn btn-outline" disabled={busy} onClick={() => open(row)}>核对资料</button></td>
        </tr>)}</tbody></table>
      {!loading && data?.items.length === 0 && <p className="listing-empty">暂无匹配草稿。选择供应商回填表后即可开始，不要求先填写成本或售价。</p>}
    </div>
    <div className="listing-toolbar listing-pagination">
      <button type="button" className="btn btn-outline" disabled={page <= 1 || loading || busy} onClick={() => setPage(p => p-1)}>上一页</button>
      <span>{page} / {Math.max(1, Math.ceil((data?.total ?? 0)/20))}</span>
      <button type="button" className="btn btn-outline" disabled={loading || busy || page*20 >= (data?.total ?? 0)} onClick={() => setPage(p => p+1)}>下一页</button>
    </div>
    {opened && <section className="card listing-editor" aria-labelledby="listing-editor-heading">
      <div className="listing-toolbar"><h3 id="listing-editor-heading" ref={editor} tabIndex={-1}>{opened.internal_code} · 核对上架资料</h3>
        <button type="button" className="btn btn-outline" disabled={busy} onClick={() => {if (!dirty || window.confirm('放弃未保存的修改？')) {setOpened(null); setDirty(false);}}}>关闭编辑</button></div>
      <p className="muted">{opened.source_file} · 原表第{opened.source_row}行 · {opened.payload.raw_name}</p>
      {!!opened.payload.colour_candidates.length && <p>原资料颜色：{opened.payload.colour_candidates.join(' / ')}。多色时请按实际面积填写Major Colour，剩余颜色放Other Colours。</p>}
      {!!opened.payload.import_conflicts.length && <div role="alert"><strong>回填差异（已有资料未被覆盖）</strong>{opened.payload.import_conflicts.map(c => <p key={c.field}>{c.field}：当前 {JSON.stringify(c.current)}；回填 {JSON.stringify(c.incoming)}</p>)}</div>}
      <div className="listing-fields">
        {fields.map(([key,label]) => <label key={key} htmlFor={`listing-${key}`}>{label}<input id={`listing-${key}`} name={key} autoComplete="off" disabled={busy} inputMode={key.startsWith('packaged_') ? 'decimal' : 'text'} value={edit[key] ?? ''} onChange={e => {setEdit(x => ({...x,[key]:e.target.value}));setDirty(true);}} /></label>)}
        <label htmlFor="listing-brand_mode">商品品牌类型<select id="listing-brand_mode" value={edit.brand_mode} disabled={busy} onChange={e => {setEdit(x => ({...x,brand_mode:e.target.value}));setDirty(true);}}><option value="">待确认</option><option value="branded">品牌商品</option><option value="generic">无品牌商品</option></select></label>
      </div>
      <div className="listing-long-fields">{largeFields.map(([key,label]) => <label key={key} htmlFor={`listing-${key}`}>{label}<textarea id={`listing-${key}`} name={key} rows={key==='description'?6:3} disabled={busy} value={edit[key] ?? ''} onChange={e => {setEdit(x => ({...x,[key]:e.target.value}));setDirty(true);}} /></label>)}</div>
      {!!opened.issues.length && <details open><summary>当前已保存资料有{opened.issues.length}项待补</summary><ul>{opened.issues.map((issue,i) => <li key={`${issue.field}-${i}`}><button type="button" className="listing-issue" onClick={() => document.getElementById(`listing-${issue.field}`)?.focus()}>{issue.message}</button></li>)}</ul></details>}
      <div className="listing-toolbar"><button type="button" className="btn btn-primary" disabled={busy || !dirty} aria-busy={busy} onClick={() => void save()}>保存资料</button><button type="button" className="btn btn-outline" disabled={busy || dirty || opened.issues.length > 0 || opened.state==='reviewed'} onClick={() => void review()}>确认本款已人工审核</button><span className="muted">{dirty?'有未保存修改，请先保存。':'修改后审核自动失效；成本与售价不参与本页放行。'}</span></div>
    </section>}
  </div>;
}
