import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { DataSourceProvider } from './DataSourceContext';
import ApiKeyGate from './ApiKeyGate';
import { collectOnAppOpen, getActiveStoreId, getStores, setActiveStoreId } from './remoteApi';
import type { Store } from './types';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const ProductList = lazy(() => import('./pages/ProductList'));
const ProductCreate = lazy(() => import('./pages/ProductCreate'));
const ProductDetail = lazy(() => import('./pages/ProductDetail'));
const Settings = lazy(() => import('./pages/Settings'));
const Procurement = lazy(() => import('./pages/Procurement'));
const Packing = lazy(() => import('./pages/Packing'));
const Inventory = lazy(() => import('./pages/Inventory'));
const Stores = lazy(() => import('./pages/Stores'));
const Replenishment = lazy(() => import('./pages/Replenishment'));
const AutoPricing = lazy(() => import('./pages/AutoPricing'));

const primaryNavItems = [
  { path: '/', label: '主页' },
  { path: '/products', label: '产品列表' },
  { path: '/inventory', label: '库存' },
  { path: '/auto-pricing', label: 'Buybox与调价' },
  { path: '/replenishment', label: '补货计划' },
  { path: '/packing', label: '装箱单' },
];

const secondaryNavItems = [
  { path: '/products?maintenance=1', label: '资料维护', hint: '核对缺字段与补充商品资料', group: '资料与记录' },
  { path: '/products?archived=1', label: '废品库', hint: '查看与恢复已移除产品', group: '资料与记录' },
  { path: '/procurement', label: '采购记录', hint: '查询和补录历史采购', group: '资料与记录' },
  { path: '/stores', label: '店铺管理', hint: '管理店铺与库存隔离', group: '系统管理' },
  { path: '/settings', label: '系统设置', hint: '费率、接口和基础参数', group: '系统管理' },
];

function WorldClocks() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const format = (zone: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(now);
  return <div className="world-clocks" aria-label="中国与南非时间" title={`中国 ${now.toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })} · 南非 ${now.toLocaleDateString('zh-CN', { timeZone: 'Africa/Johannesburg' })}；中国快 6 小时`}>
    <div><span>中国</span><time dateTime={now.toISOString()}>{format('Asia/Shanghai')}</time></div>
    <div><span>南非</span><time dateTime={now.toISOString()}>{format('Africa/Johannesburg')}</time></div>
  </div>;
}

export default function App() {
  const [stores, setStores] = useState<Store[]>([]);
  const [activeStore, setActiveStore] = useState(getActiveStoreId());
  const [managementOpen, setManagementOpen] = useState(false);
  const managementMenuRef = useRef<HTMLDivElement>(null);
  const collectionRequested = useRef(new Set<string>());
  const [openCollection,setOpenCollection] = useState('');

  useEffect(() => {
    getStores().then(rows => {
      setStores(rows);
      const activeRows = rows.filter(row => row.status === 'active');
      if (activeRows.length && !activeRows.some(row => row.id === activeStore)) {
        setActiveStore(activeRows[0].id);
        setActiveStoreId(activeRows[0].id);
      } else if (activeStore==='default-store' && activeStore===getActiveStoreId() && !collectionRequested.current.has(activeStore)) {
        collectionRequested.current.add(activeStore);
        setOpenCollection('正在提交本次 Buybox 采集…');
        void collectOnAppOpen().then(result=>setOpenCollection(result.queued
          ? `已提交 ${result.queued} 个SKU采集一轮；报价结果请查看Buybox与调价。`
          : '本次没有新增采集任务；已暂停、已排队及冷却中的SKU不会重复提交。'))
          .catch(e=>setOpenCollection(`本次采集未提交：${(e as Error).message}`));
      }
    }).catch(() => setStores([{ id: 'default-store', name: '自营店铺', platform: 'Takealot', status: 'active', owner_name: '', sync_method: 'manual', sync_interval_minutes: 60, external_store_ref: '', allow_negative_inventory_shipments: true, last_synced_at: null }]));
  }, [activeStore]);

  useEffect(() => {
    if (!managementOpen) return;
    const closeOnOutside = (event: MouseEvent) => {
      if (!managementMenuRef.current?.contains(event.target as Node)) setManagementOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setManagementOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [managementOpen]);

  const switchStore = (storeId: string) => {
    setOpenCollection('');
    setActiveStoreId(storeId);
    setActiveStore(storeId);
  };

  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <DataSourceProvider>
        <ApiKeyGate />
        <header className="app-header">
        <div className="app-brand">
          <img src={`${import.meta.env.BASE_URL}yellow-mascot.png`} alt="黄色噜噜" />
          <h1>Takealot 选品与利润测算</h1>
        </div>
        <nav className="primary-nav" aria-label="主要功能">
          {primaryNavItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="header-tools">
          <WorldClocks />
          <div className="store-switcher">
          <span>店铺</span>
          <select value={activeStore} onChange={event => switchStore(event.target.value)}>
            {(stores.filter(store => store.status === 'active').length ? stores.filter(store => store.status === 'active') : [{ id: 'default-store', name: '自营店铺' } as Store]).map(store => <option key={store.id} value={store.id}>{store.name}</option>)}
          </select>
          </div>
          <NavLink to="/create" className="header-create-button">＋ 新建产品</NavLink>
          <div className={`management-menu ${managementOpen ? 'open' : ''}`} ref={managementMenuRef}>
            <button
              type="button"
              className="management-menu-trigger"
              aria-expanded={managementOpen}
              aria-haspopup="menu"
              onClick={() => setManagementOpen(open => !open)}
            >
              <span className="management-menu-icon" aria-hidden="true"><i /><i /><i /><i /></span>
              管理工具
            </button>
            {managementOpen && <div className="management-menu-panel" role="menu">
              {['资料与记录', '系统管理'].map(group => <div className="management-menu-group" key={group}>
                <strong>{group}</strong>
                {secondaryNavItems.filter(item => item.group === group).map(item => (
                  <NavLink key={item.path} to={item.path} role="menuitem" onClick={() => setManagementOpen(false)}>
                    <span>{item.label}</span>
                    <small>{item.hint}</small>
                  </NavLink>
                ))}
              </div>)}
            </div>}
          </div>
        </div>
      </header>

      <main className="container" key={activeStore}>
        {activeStore==='default-store'&&openCollection&&<div className="operations-note" role="status">{openCollection} <NavLink to="/auto-pricing">查看采集</NavLink></div>}
        <Suspense fallback={<div className="page-loading"><span></span>正在加载页面...</div>}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/products" element={<ProductList />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/buybox" element={<Navigate to="/auto-pricing" replace />} />
          <Route path="/auto-pricing" element={<AutoPricing />} />
          <Route path="/replenishment" element={<Replenishment />} />
          <Route path="/create" element={<ProductCreate />} />
          <Route path="/procurement" element={<Procurement />} />
          <Route path="/packing" element={<Packing />} />
          <Route path="/stores" element={<Stores onStoresChanged={setStores} />} />
          <Route path="/products/:id" element={<ProductDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
        </Suspense>
      </main>
      </DataSourceProvider>
    </BrowserRouter>
  );
}
