import React, { createContext, useContext, useState, useMemo, useCallback, type ReactNode } from 'react';

import * as localApi from './api';
import * as remoteApi from './remoteApi';

export type DataSource = 'local' | 'remote';

interface DataSourceContextValue {
  dataSource: DataSource;
  setDataSource: (ds: DataSource) => void;
}

const DataSourceContext = createContext<DataSourceContextValue | null>(null);

/** 是否运行在 Tauri WebView（存在 Tauri 桥接对象） */
const isTauri = (): boolean =>
  typeof window !== 'undefined' &&
  !!(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;

const getStoredDataSource = (): DataSource => {
  try {
    // 纯浏览器（vite dev 页面）无 Tauri invoke，local 数据源必然失败
    // → 强制 remote 并落盘，避免列表空/无图
    if (!isTauri()) {
      localStorage.setItem('data_source', 'remote');
      return 'remote';
    }
    const stored = localStorage.getItem('data_source');
    if (stored === 'remote') return 'remote';
  } catch {
    // ignore
  }
  return 'local';
};

export const DataSourceProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [dataSource, setDataSourceState] = useState<DataSource>(getStoredDataSource);

  const setDataSource = useCallback((ds: DataSource) => {
    setDataSourceState(ds);
    try {
      localStorage.setItem('data_source', ds);
    } catch {
      // ignore
    }
  }, []);

  const value: DataSourceContextValue = useMemo(
    () => ({ dataSource, setDataSource }),
    [dataSource, setDataSource],
  );

  return React.createElement(DataSourceContext.Provider, { value }, children);
};

export function useApi() {
  const ctx = useContext(DataSourceContext);
  if (!ctx) {
    throw new Error('useApi must be used within a DataSourceProvider');
  }
  return ctx.dataSource === 'remote' ? remoteApi : localApi;
}

export function useDataSource(): { dataSource: DataSource; setDataSource: (ds: DataSource) => void } {
  const ctx = useContext(DataSourceContext);
  if (!ctx) {
    throw new Error('useDataSource must be used within a DataSourceProvider');
  }
  return { dataSource: ctx.dataSource, setDataSource: ctx.setDataSource };
}
