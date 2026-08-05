import React, { createContext, useContext, useState, useMemo, useCallback, type ReactNode } from 'react';

import * as localApi from './api';
import * as remoteApi from './remoteApi';

export type DataSource = 'local' | 'remote';

interface DataSourceContextValue {
  dataSource: DataSource;
  setDataSource: (ds: DataSource) => void;
}

const DataSourceContext = createContext<DataSourceContextValue | null>(null);

const getStoredDataSource = (): DataSource => {
  try {
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
