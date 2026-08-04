import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getDashboard } from '../api';
import type { DashboardStats } from '../types';
import { formatPercent } from '../types';

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    getDashboard()
      .then(setStats)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="loading">加载中...</div>;
  if (error) return <div className="alert alert-error">{error}</div>;
  if (!stats) return <div className="loading">无数据</div>;

  return (
    <div>
      <h2 style={{ marginBottom: 20 }}>仪表盘</h2>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-value">{stats.total}</div>
          <div className="stat-label">总产品数</div>
        </div>
        <div className="stat-card qualified">
          <div className="stat-value">{stats.qualified}</div>
          <div className="stat-label">合格选品 (&ge;25%)</div>
        </div>
        <div className="stat-card not-recommended">
          <div className="stat-value">{stats.not_recommended}</div>
          <div className="stat-label">不建议选品</div>
        </div>
        <div className="stat-card pending">
          <div className="stat-value">{stats.data_incomplete}</div>
          <div className="stat-label">数据待补充</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{stats.category_pending}</div>
          <div className="stat-label">待确认品类</div>
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <div className="card-title">利润率概览</div>
          <div style={{ fontSize: 36, fontWeight: 700, color: 'var(--color-primary)' }}>
            {formatPercent(stats.avg_profit_margin)}
          </div>
          <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginTop: 4 }}>
            平均利润率
          </div>
        </div>

        <div className="card">
          <div className="card-title">最佳产品</div>
          {stats.top_product_name ? (
            <>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{stats.top_product_name}</div>
              <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-success)', marginTop: 8 }}>
                {formatPercent(stats.top_profit_margin)}
              </div>
            </>
          ) : (
            <div style={{ color: 'var(--color-text-secondary)' }}>暂无已计算利润率的产品</div>
          )}
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <Link to="/products" className="btn btn-primary">查看全部产品</Link>
        <Link to="/create" className="btn btn-outline" style={{ marginLeft: 12 }}>新建产品</Link>
      </div>
    </div>
  );
}
