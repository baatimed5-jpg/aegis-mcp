import { DestroyRef } from '@angular/core';

/** Colour family for an audit status. */
export type StatusTone = 'ok' | 'bad' | 'info' | 'warn' | 'muted';

export function statusTone(status: string | null | undefined): StatusTone {
  switch ((status ?? '').toLowerCase()) {
    case 'allowed':
      return 'ok';
    case 'denied':
    case 'blocked':
      return 'bad';
    case 'approved':
      return 'info';
    case 'timeout':
    case 'error':
      return 'warn';
    default:
      return 'muted';
  }
}

export const TONE_COLOR: Record<StatusTone, string> = {
  ok: 'var(--ok)',
  bad: 'var(--bad)',
  info: 'var(--info)',
  warn: 'var(--warn)',
  muted: 'var(--muted)',
};

/** Runs `fn` now and every `ms` until the owning component is destroyed. */
export function poll(destroyRef: DestroyRef, ms: number, fn: () => void): void {
  fn();
  const id = setInterval(fn, ms);
  destroyRef.onDestroy(() => clearInterval(id));
}

/** Human-readable message for an HttpErrorResponse-like error. */
export function errorText(err: unknown): string {
  const e = err as { status?: number; error?: { detail?: unknown }; message?: string } | null;
  if (e?.status === 0) return 'Gateway unreachable.';
  const detail = e?.error?.detail;
  if (typeof detail === 'string') return `HTTP ${e?.status}: ${detail}`;
  if (e?.status) return `Request failed (HTTP ${e.status}).`;
  return e?.message ?? 'Request failed.';
}

/** Compact number formatting for metric tables (e.g. 0.8123 -> 0.812, 15234 -> 15,234). */
export function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  const a = Math.abs(v);
  const digits = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : 3;
  return v.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}
