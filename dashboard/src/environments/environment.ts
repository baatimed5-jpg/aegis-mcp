/**
 * Production / docker build: the dashboard is served by nginx, which proxies
 * `/api/` (REST + WebSocket) to the gateway. An empty apiBase means
 * "same origin", so all calls go to relative `/api/...` URLs.
 */
export const environment = {
  production: true,
  apiBase: '',
};
