import { auditWsUrl, apiUrl, isApiUrl } from './api-url';
import { mergeRows } from './audit-stream.service';
import { AuditRow } from './models';
import { fmtNum, statusTone } from './util';
import { pivot } from '../pages/experiments/experiments';

function row(id: string, ts: string): AuditRow {
  return {
    id, ts, agent_id: 'a', server: 's', tool: 't', status: 'allowed', reason: null,
    injection_score: null, pii_entities: null, tokens_before: null, tokens_after: null,
    cost_saved_usd: null, latency_total_ms: null, latency_downstream_ms: null, cache_hit: false,
  };
}

describe('api-url', () => {
  it('builds dev and prod URLs', () => {
    expect(apiUrl('/api/stats', 'http://localhost:8000')).toBe('http://localhost:8000/api/stats');
    expect(apiUrl('/api/stats', '')).toBe('/api/stats');
    expect(isApiUrl('http://localhost:8000/api/audit', 'http://localhost:8000')).toBe(true);
    expect(isApiUrl('https://example.com/api/audit', 'http://localhost:8000')).toBe(false);
    expect(isApiUrl('/api/audit', '')).toBe(true);
  });

  it('derives ws/wss for the audit stream', () => {
    expect(auditWsUrl('t k', 'http://localhost:8000')).toBe('ws://localhost:8000/api/ws/audit?token=t%20k');
    expect(auditWsUrl('x', '', { protocol: 'https:', host: 'aegis.example' })).toBe(
      'wss://aegis.example/api/ws/audit?token=x',
    );
    expect(auditWsUrl('x', '', { protocol: 'http:', host: 'localhost:4200' })).toBe(
      'ws://localhost:4200/api/ws/audit?token=x',
    );
  });
});

describe('mergeRows', () => {
  it('dedupes, sorts newest first and caps', () => {
    const a = row('1', '2026-11-19T10:00:00Z');
    const b = row('2', '2026-11-19T10:00:05Z');
    const merged = mergeRows([a], [b, a], 10);
    expect(merged.map((r) => r.id)).toEqual(['2', '1']);
    expect(mergeRows(merged, [row('3', '2026-11-19T10:00:09Z')], 2).map((r) => r.id)).toEqual(['3', '2']);
  });
});

describe('util', () => {
  it('maps statuses to tones', () => {
    expect(statusTone('allowed')).toBe('ok');
    expect(statusTone('blocked')).toBe('bad');
    expect(statusTone('denied')).toBe('bad');
    expect(statusTone('approved')).toBe('info');
    expect(statusTone('timeout')).toBe('warn');
    expect(statusTone('weird')).toBe('muted');
  });

  it('formats numbers', () => {
    expect(fmtNum(0.81234)).toBe('0.812');
    expect(fmtNum(15234.4)).toBe('15,234');
    expect(fmtNum(null)).toBe('–');
  });
});

describe('experiments pivot', () => {
  it('builds metric x profile table', () => {
    const t = pivot({
      tag: 'baseline',
      charts: [],
      summary: [
        { profile: 'raw', metric: 'success', mean: 0.5, std: 0.1, runs: 3 },
        { profile: 'aegis', metric: 'success', mean: 0.9, std: 0.05, runs: 3 },
        { profile: 'aegis', metric: 'tokens', mean: 100, std: 4, runs: 3 },
      ],
    });
    expect(t.profiles).toEqual(['raw', 'aegis']);
    expect(t.rows.map((r) => r.metric)).toEqual(['success', 'tokens']);
    expect(t.rows[1].cells[0]).toBeNull();
    expect(t.rows[1].cells[1]?.mean).toBe(100);
  });
});
