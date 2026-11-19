import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { AuditStreamService } from '../../core/audit-stream.service';
import { Stats } from '../../core/models';
import { StatusTone, TONE_COLOR, errorText, poll, statusTone } from '../../core/util';
import { DonutChart, Slice } from '../../shared/donut-chart';
import { KpiCard } from '../../shared/kpi-card';
import { StatusBadge } from '../../shared/status-badge';

const STATUS_ORDER = ['allowed', 'approved', 'denied', 'blocked', 'timeout', 'error'];
const SHADE: Record<string, string> = {
  denied: 'color-mix(in srgb, var(--bad) 60%, var(--panel))',
  error: 'color-mix(in srgb, var(--warn) 60%, var(--panel))',
};

@Component({
  selector: 'app-overview',
  imports: [KpiCard, DonutChart, StatusBadge, DatePipe, DecimalPipe],
  template: `
    <header class="page-head">
      <div>
        <h1>Overview</h1>
        <p>Every tool call through the gateway, live.</p>
      </div>
      @if (error()) {
        <span class="alert bad inline">{{ error() }}</span>
      }
    </header>

    <section class="kpis">
      <app-kpi-card label="Total calls" [value]="fmt(stats()?.calls_total)" tone="accent" />
      <app-kpi-card
        label="Blocked attacks" [value]="fmt(stats()?.blocked_attacks)" tone="bad"
        [hint]="stats() ? fmt(stats()!.security_events_total) + ' security events' : null" />
      <app-kpi-card label="PII masked" [value]="fmt(stats()?.pii_entities)" tone="info" hint="entities redacted" />
      <app-kpi-card
        label="Tokens saved" [value]="fmt(stats()?.tokens_saved)" tone="ok"
        [hint]="savedPct() !== null ? (savedPct()! | number: '1.0-1') + '% of ' + fmt(stats()?.tokens_before) : null" />
      <app-kpi-card label="Cost saved" [value]="usd(stats()?.cost_saved_usd)" tone="ok" hint="USD (LLM input tokens)" />
      <app-kpi-card
        label="Cache hits" [value]="fmt(stats()?.cache_hits)" tone="warn"
        [hint]="cacheRate() !== null ? (cacheRate()! | number: '1.0-1') + '% of calls' : null" />
    </section>

    <section class="grid">
      <div class="card pad">
        <h2>Status breakdown</h2>
        <app-donut-chart [slices]="slices()" caption="calls" />
      </div>

      <div class="card table-card">
        <div class="table-head">
          <h2>Live audit</h2>
          <span class="muted small">{{ stream.rows().length }} recent calls · {{ stream.state() }}</span>
        </div>
        <div class="table-scroll">
          <table class="table">
            <thead>
              <tr>
                <th>Time</th><th>Agent</th><th>Tool</th><th>Status</th><th>Reason</th>
                <th class="num">Inj.</th><th class="num">PII</th><th class="num">Tokens</th>
                <th class="num">Latency</th>
              </tr>
            </thead>
            <tbody>
              @for (r of stream.rows(); track r.id) {
                <tr [class.fresh]="stream.liveIds.has(r.id)" [class]="'tone-' + tone(r.status)">
                  <td class="nowrap mono">{{ r.ts | date: 'HH:mm:ss' }}</td>
                  <td>{{ r.agent_id }}</td>
                  <td class="mono"><span class="muted">{{ r.server }}__</span>{{ r.tool }}</td>
                  <td><app-status-badge [status]="r.status" /></td>
                  <td class="reason" [title]="r.reason ?? ''">{{ r.reason ?? '' }}</td>
                  <td class="num">{{ r.injection_score === null ? '–' : (r.injection_score | number: '1.2-2') }}</td>
                  <td class="num">{{ r.pii_entities ?? '–' }}</td>
                  <td class="num nowrap">
                    @if (r.tokens_before !== null) {
                      {{ r.tokens_before | number }} → {{ r.tokens_after ?? '–' }}
                    } @else { – }
                    @if (r.cache_hit) { <span class="chip tiny">cache</span> }
                  </td>
                  <td class="num nowrap">{{ r.latency_total_ms === null ? '–' : (r.latency_total_ms | number: '1.0-0') + ' ms' }}</td>
                </tr>
              } @empty {
                <tr><td colspan="9" class="empty">No calls yet. New calls appear here in real time.</td></tr>
              }
            </tbody>
          </table>
        </div>
      </div>
    </section>
  `,
  styles: `
    .kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin-bottom: 18px; }
    .grid { display: grid; grid-template-columns: minmax(280px, 340px) 1fr; gap: 18px; align-items: start; }
    .pad { padding: 18px; }
    .reason { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-2); }
    tr.tone-bad td:first-child { box-shadow: inset 3px 0 0 var(--bad); }
    tr.tone-warn td:first-child { box-shadow: inset 3px 0 0 var(--warn); }
    tr.tone-info td:first-child { box-shadow: inset 3px 0 0 var(--info); }
    tr.tone-ok td:first-child { box-shadow: inset 3px 0 0 var(--ok); }
    tr.fresh { animation: flash 2.4s ease-out; }
    @keyframes flash { from { background: var(--accent-soft); } to { background: transparent; } }
    @media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
  `,
})
export class OverviewPage {
  private readonly api = inject(ApiService);
  protected readonly stream = inject(AuditStreamService);

  protected readonly stats = signal<Stats | null>(null);
  protected readonly error = signal<string | null>(null);

  protected readonly savedPct = computed(() => {
    const s = this.stats();
    return s && s.tokens_before > 0 ? (s.tokens_saved / s.tokens_before) * 100 : null;
  });

  protected readonly cacheRate = computed(() => {
    const s = this.stats();
    return s && s.calls_total > 0 ? (s.cache_hits / s.calls_total) * 100 : null;
  });

  protected readonly slices = computed<Slice[]>(() => {
    const by = this.stats()?.by_status ?? {};
    const keys = [...STATUS_ORDER.filter((k) => k in by), ...Object.keys(by).filter((k) => !STATUS_ORDER.includes(k))];
    return keys.map((k) => ({ label: k, value: by[k] ?? 0, color: SHADE[k] ?? TONE_COLOR[statusTone(k)] }));
  });

  constructor() {
    poll(inject(DestroyRef), 3000, () => this.load());
  }

  private load(): void {
    this.api.stats().subscribe({
      next: (s) => {
        this.stats.set(s);
        this.error.set(null);
      },
      error: (e) => this.error.set(errorText(e)),
    });
  }

  protected tone(status: string): StatusTone {
    return statusTone(status);
  }

  protected fmt(v: number | null | undefined): string {
    return v === null || v === undefined ? '–' : v.toLocaleString('en-US');
  }

  protected usd(v: number | null | undefined): string {
    return v === null || v === undefined
      ? '–'
      : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: v < 1 ? 4 : 2 });
  }
}
