import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import ProductList from './pages/ProductList';
import ProductCreate from './pages/ProductCreate';
import ProductDetail from './pages/ProductDetail';
import Settings from './pages/Settings';

const navItems = [
  { path: '/', label: '仪表盘' },
  { path: '/products', label: '产品列表' },
  { path: '/create', label: '新建产品' },
  { path: '/settings', label: '系统设置' },
];

export default function App() {
  return (
    <BrowserRouter basename="/takealot-selector">
      <header className="app-header">
        <h1>Takealot 选品测算</h1>
        <nav>
          {navItems.map((item) => (
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
      </header>

      <div className="container">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/products" element={<ProductList />} />
          <Route path="/create" element={<ProductCreate />} />
          <Route path="/products/:id" element={<ProductDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
