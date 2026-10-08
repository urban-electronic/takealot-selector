import { lazy, Suspense, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { DataSourceProvider } from './DataSourceContext';
import { getActiveStoreId, getStores, setActiveStoreId } from './remoteApi';
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

const primaryNavItems = [
  { path: '/', label: '主页' },
  { path: '/products', label: '产品列表' },
  { path: '/inventory', label: '库存' },
  { path: '/packing', label: '装箱单' },
];

const secondaryNavItems = [
  { path: '/products?archived=1', label: '废品库', hint: '查看与恢复已移除产品' },
  { path: '/stores', label: '店铺管理' },
  { path: '/procurement', label: '采购记录' },
  { path: '/settings', label: '系统设置' },
];

export default function App() {
  const [stores, setStores] = useState<Store[]>([]);
  const [activeStore, setActiveStore] = useState(getActiveStoreId());

  useEffect(() => {
    getStores().then(rows => {
      setStores(rows);
      const activeRows = rows.filter(row => row.status === 'active');
      if (activeRows.length && !activeRows.some(row => row.id === activeStore)) {
        setActiveStore(activeRows[0].id);
        setActiveStoreId(activeRows[0].id);
      }
    }).catch(() => setStores([{ id: 'default-store', name: '自营店铺', platform: 'Takealot', status: 'active', owner_name: '', sync_method: 'manual', sync_interval_minutes: 60, external_store_ref: '', allow_negative_inventory_shipments: true, last_synced_at: null }]));
  }, [activeStore]);

  const switchStore = (storeId: string) => {
    setActiveStoreId(storeId);
    setActiveStore(storeId);
  };

  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <DataSourceProvider>
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
          <div className="store-switcher">
          <span>店铺</span>
          <select value={activeStore} onChange={event => switchStore(event.target.value)}>
            {(stores.filter(store => store.status === 'active').length ? stores.filter(store => store.status === 'active') : [{ id: 'default-store', name: '自营店铺' } as Store]).map(store => <option key={store.id} value={store.id}>{store.name}</option>)}
          </select>
          </div>
          <NavLink to="/create" className="header-create-button">＋ 新建产品</NavLink>
          <details className="management-menu">
            <summary>更多管理 <span>⌄</span></summary>
            <div className="management-menu-panel">
              <strong>管理工具</strong>
              {secondaryNavItems.map(item => (
                <NavLink key={item.path} to={item.path}>
                  <span>{item.label}</span>
                  {item.hint && <small>{item.hint}</small>}
                </NavLink>
              ))}
            </div>
          </details>
        </div>
      </header>

      <div className="container" key={activeStore}>
        <Suspense fallback={<div className="page-loading"><span></span>正在加载页面...</div>}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/products" element={<ProductList />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/create" element={<ProductCreate />} />
          <Route path="/procurement" element={<Procurement />} />
          <Route path="/packing" element={<Packing />} />
          <Route path="/stores" element={<Stores onStoresChanged={setStores} />} />
          <Route path="/products/:id" element={<ProductDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
        </Suspense>
      </div>
      </DataSourceProvider>
    </BrowserRouter>
  );
}
