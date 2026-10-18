import { useEffect, useRef, useState } from 'react';

/**
 * 网页版首次访问的密钥引导。
 *
 * 后端配了 API_KEY 时，网页版一打开所有接口都是 401，而界面只会显示
 * 「加载失败：无效或缺失 API Key」。用户不可能自己想到要去「系统设置」页
 * 找一个输入框——尤其第一次打开时连设置页在哪都不知道。
 *
 * 这里监听 remoteApi 抛出的 api-unauthorized 信号，浮一条横幅直接填、
 * 直接存、直接重载，把「打开网页 → 能干活」之间那一步补齐。
 */
export default function ApiKeyGate() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [wrong, setWrong] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onUnauthorized = () => {
      // 本机已经存过 key 还 401，说明上次填的是错的，要换个说法，
      // 否则用户会以为「我明明填过」。
      let saved = '';
      try { saved = (localStorage.getItem('api_key') || '').trim(); } catch { saved = ''; }
      setWrong(!!saved);
      setOpen(true);
    };
    window.addEventListener('api-unauthorized', onUnauthorized);
    return () => window.removeEventListener('api-unauthorized', onUnauthorized);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const save = () => {
    const key = value.trim();
    if (!key) return;
    try { localStorage.setItem('api_key', key); } catch { /* 隐私模式下存不住，刷新后仍需重填 */ }
    window.location.reload();
  };

  return (
    <div className="api-key-gate">
      <div className="api-key-gate-body" role="region" aria-label="访问密钥">
        <strong>{wrong ? '访问密钥不对，再试一次' : '请输入访问密钥'}</strong>
        <p>这个系统只对持有密钥的人开放。填一次就记在这台设备上，之后不用再填。</p>
        <div className="api-key-gate-row">
          <input
            ref={inputRef}
            type="password"
            value={value}
            onChange={event => setValue(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') save(); }}
            placeholder="粘贴后端 API_KEY"
            aria-label="访问密钥"
            autoComplete="off"
          />
          <button type="button" onClick={save} disabled={!value.trim()}>进入</button>
        </div>
      </div>
    </div>
  );
}
