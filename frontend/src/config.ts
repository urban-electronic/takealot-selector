const isDev = import.meta.env.DEV;
export const API_BASE = isDev ? '' : 'https://YOUR_RAILWAY_URL';
