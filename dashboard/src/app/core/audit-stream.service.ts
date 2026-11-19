import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { auditWsUrl } from './api-url';
import { AuditRow } from './models';

export type StreamState = 'idle' | 'connecting' | 'open' | 'reconnecting';

const MAX_ROWS = 200;
const MAX_BACKOFF_MS = 30_000;

/** Merges rows: de-duplicated by id, newest first, capped. */
export function mergeRows(existing: AuditRow[], incoming: AuditRow[], max = MAX_ROWS): AuditRow[] {
  const seen = new Set<string>();
  const out: AuditRow[] = [];
  for (const r of [...incoming, ...existing]) {
    if (!r || seen.has(r.id)) continue;
    seen.add(r.id);
    out.push(r);
  }
  out.sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts));
  return out.slice(0, max);
}

/**
 * Live audit feed: seeds from GET /api/audit, then listens on the
 * /api/ws/audit WebSocket. Reconnects with exponential backoff and
 * re-seeds after a reconnect to fill any gap.
 */
@Injectable({ providedIn: 'root' })
export class AuditStreamService {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  readonly rows = signal<AuditRow[]>([]);
  readonly state = signal<StreamState>('idle');
  /** Ids received over the socket (used to highlight fresh rows). */
  readonly liveIds = new Set<string>();

  private ws: WebSocket | null = null;
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private running = false;

  start(): void {
    if (this.running) return;
    this.running = true;
    this.seed();
    this.connect();
  }

  stop(): void {
    this.running = false;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const ws = this.ws;
    this.ws = null;
    ws?.close();
    this.attempt = 0;
    this.state.set('idle');
    this.rows.set([]);
    this.liveIds.clear();
  }

  private seed(): void {
    this.api.audit(100).subscribe({
      next: (rows) => this.rows.update((cur) => mergeRows(cur, rows ?? [])),
      error: () => {
        /* REST errors are non-fatal for the live feed */
      },
    });
  }

  private connect(): void {
    const token = this.auth.token();
    if (!this.running || !token) return;
    this.state.set(this.attempt === 0 ? 'connecting' : 'reconnecting');
    let ws: WebSocket;
    try {
      ws = new WebSocket(auditWsUrl(token));
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (this.attempt > 0) this.seed();
      this.attempt = 0;
      this.state.set('open');
    };
    ws.onmessage = (ev: MessageEvent) => {
      try {
        const row = JSON.parse(String(ev.data)) as AuditRow;
        if (row && typeof row.id === 'string') {
          this.liveIds.add(row.id);
          this.rows.update((cur) => mergeRows(cur, [row]));
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (!this.running) return;
    this.state.set('reconnecting');
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.attempt) + Math.random() * 300;
    this.attempt++;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, delay);
  }
}
