import { DatePipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { SecurityEvent, SecurityKind, Stats } from '../../core/models';
import { errorText, poll } from '../../core/util';
import { KpiCard } from '../../shared/kpi-card';
import { ScoreBar } from '../../shared/score-bar';

type KindFilter = 'all' | SecurityKind;

@Component({
  selector: 'app-security',
  imports: [KpiCard, ScoreBar, DatePipe],
  template: `
    <header class="page-head">
      <div>
        <h1>Security</h1>
        <p>Prompt-injection detections on tool descriptions, arguments and responses.</p>
      </div>
      @if (error()) {
        <span class="alert bad inline">{{ error() }}</span>
      }
    </header>

    <section class="kpis">
      <app-kpi-card label="Blocked attacks" [value]="n(stats()?.blocked_attacks)" tone="bad" />
      <app-kpi-card label="Security events" [value]="n(stats()?.security_events_total)" tone="warn" />
      <app-kpi-card label="Tool descriptions" [value]="n(byKind('description'))" hint="poisoned tool metadata" />
      <app-kpi-card label="Arguments" [value]="n(byKind('argument'))" hint="malicious call arguments" />
      <app-kpi-card label="Responses" [value]="n(byKind('response'))" hint="indirect injection in results" />
    </section>

    <div class="card table-card">
      <div class="table-head">
        <h2>Events</h2>
        <div class="filters">
          @for (k of kinds; track k) {
            <button type="button" class="chip" [class.active]="filter() === k" (click)="filter.set(k)">{{ k }}</button>
          }
        </div>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Time</th><th>Kind</th><th>Tool</th><th>Detector</th><th>Score</th><th>Action</th><th>Excerpt</th>
            </tr>
          </thead>
          <tbody>
            @for (e of filtered(); track e.id) {
              <tr>
                <td class="nowrap mono">{{ e.ts | date: 'MMM d, HH:mm:ss' }}</td>
                <td><span class="chip tiny">{{ e.kind }}</span></td>
                <td class="mono"><span class="muted">{{ e.server }}__</span>{{ e.tool }}</td>
                <td>{{ e.detector }}</td>
                <td><app-score-bar [value]="e.score" /></td>
                <td>
                  <span class="badge" [class]="'badge ' + actionTone(e.action)">{{ e.action }}</span>
                </td>
                <td class="excerpt"><code [title]="e.excerpt ?? ''">{{ e.excerpt ?? '–' }}</code></td>
              </tr>
            } @empty {
              <tr><td colspan="7" class="empty">{{ loaded() ? 'No security events.' : 'Loading…' }}</td></tr>
            }
          </tbody>
        </table>
      </div>
    </div>
  `,
  styles: `
    .kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 14px; margin-bottom: 18px; }
    .filters { display: flex; gap: 6px; flex-wrap: wrap; }
    .filters .chip { cursor: pointer; text-transform: capitalize; }
    .excerpt { max-width: 420px; }
    .excerpt code {
      display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      font-size: 12px; background: var(--hover); padding: 3px 6px; border-radius: 4px;
    }
  `,
})
export class SecurityPage {
  private readonly api = inject(ApiService);

  protected readonly kinds: KindFilter[] = ['all', 'description', 'argument', 'response'];
  protected readonly events = signal<SecurityEvent[]>([]);
  protected readonly stats = signal<Stats | null>(null);
  protected readonly filter = signal<KindFilter>('all');
  protected readonly loaded = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly filtered = computed(() => {
    const f = this.filter();
    return f === 'all' ? this.events() : this.events().filter((e) => e.kind === f);
  });

  constructor() {
    poll(inject(DestroyRef), 5000, () => this.load());
  }

  private load(): void {
    this.api.securityEvents(100).subscribe({
      next: (rows) => {
        this.events.set(rows ?? []);
        this.loaded.set(true);
        this.error.set(null);
      },
      error: (e) => this.error.set(errorText(e)),
    });
    this.api.stats().subscribe({ next: (s) => this.stats.set(s), error: () => undefined });
  }

  protected byKind(kind: SecurityKind): number | undefined {
    return this.stats()?.security_events_by_kind?.[kind];
  }

  protected n(v: number | null | undefined): string {
    return v === null || v === undefined ? '–' : v.toLocaleString('en-US');
  }

  protected actionTone(action: string): string {
    const a = action.toLowerCase();
    if (a.includes('block') || a.includes('deny') || a.includes('quarantin')) return 'bad';
    if (a.includes('redact') || a.includes('sanitiz') || a.includes('strip') || a.includes('mask')) return 'warn';
    if (a.includes('flag') || a.includes('log')) return 'info';
    return 'muted';
  }
}
