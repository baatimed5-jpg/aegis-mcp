import { environment } from '../../environments/environment';

/** Absolute (dev) or relative (prod) URL for an API path such as `/api/stats`. */
export function apiUrl(path: string, base: string = environment.apiBase): string {
  return `${base}${path}`;
}

/** True when the request URL targets the gateway API (so it gets the token). */
export function isApiUrl(url: string, base: string = environment.apiBase): boolean {
  return url.startsWith(`${base}/api/`);
}

/** WebSocket URL for the live audit stream; ws/wss derived from the API base or the page. */
export function auditWsUrl(
  token: string,
  base: string = environment.apiBase,
  loc: Pick<Location, 'protocol' | 'host'> = window.location,
): string {
  const query = `?token=${encodeURIComponent(token)}`;
  const origin = base ? new URL(base) : loc;
  const proto = origin.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${origin.host}/api/ws/audit${query}`;
}
