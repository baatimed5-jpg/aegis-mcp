import { Component, DestroyRef, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { ExperimentSummaryRow, ExperimentTag } from '../../core/models';
import { errorText, fmtNum } from '../../core/util';

interface MetricTable {
  tag: string;
  profiles: string[];
  rows: { metric: string; cells: (ExperimentSummaryRow | null)[] }[];
  charts: string[];
}

/** Pivot the flat summary into metric (rows) × profile (columns). */
export function pivot(tag: ExperimentTag): MetricTable {
  const profiles = [...new Set(tag.summary.map((s) => s.profile))];
  const metrics = [...new Set(tag.summary.map((s) => s.metric))];
  const index = new Map(tag.summary.map((s) => [`${s.metric}\u0000${s.profile}`, s]));
  return {
    tag: tag.tag,
    profiles,
    rows: metrics.map((metric) => ({
      metric,
      cells: profiles.map((p) => index.get(`${metric}\u0000${p}`) ?? null),
    })),
    charts: tag.charts ?? [],
  };
}

@Component({
  selector: 'app-experiments',
  template: `
    <header class="page-head">
      <div>
        <h1>Experiments</h1>
        <p>Evaluation runs per profile: mean ± std over repeated runs.</p>
      </div>
      <button class="btn ghost" type="button" (click)="load()">Reload</button>
    </header>

    @if (error()) {
      <div class="alert bad">{{ error() }}</div>
    }

    @for (t of tables(); track t.tag) {
      <section class="card exp">
        <h2>{{ t.tag }}</h2>
        @if (t.rows.length) {
          <div class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th>Metric</th>
                  @for (p of t.profiles; track p) {
                    <th class="num">{{ p }}</th>
                  }
                </tr>
              </thead>
              <tbody>
                @for (r of t.rows; track r.metric) {
                  <tr>
                    <td class="mono">{{ r.metric }}</td>
                    @for (c of r.cells; track $index) {
                      <td class="num nowrap">
                        @if (c) {
                          <strong>{{ fmt(c.mean) }}</strong>
                          <span class="muted"> ± {{ fmt(c.std) }}</span>
                          <span class="runs">n={{ c.runs }}</span>
                        } @else { – }
                      </td>
                    }
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <p class="muted">No summary rows.</p>
        }
        @if (t.charts.length) {
          <div class="charts">
            @for (c of t.charts; track c) {
              <figure>
                @if (images()[c]; as src) {
                  @if (src === 'error') {
                    <div class="img-placeholder">Chart failed to load</div>
                  } @else {
                    <a [href]="src" target="_blank" rel="noopener"><img [src]="src" [alt]="caption(c)" loading="lazy" /></a>
                  }
                } @else {
                  <div class="img-placeholder">Loading…</div>
                }
                <figcaption>{{ caption(c) }}</figcaption>
              </figure>
            }
          </div>
        }
      </section>
    } @empty {
      <div class="card pad muted">{{ loaded() ? 'No experiment results yet.' : 'Loading…' }}</div>
    }
  `,
  styles: `
    .exp { padding: 18px; margin-bottom: 18px; }
    .exp h2 { margin-top: 0; }
    .pad { padding: 18px; }
    .runs { margin-left: 6px; font-size: 11px; color: var(--muted); }
    .charts { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; margin-top: 18px; }
    figure { margin: 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: #fff; }
    img { display: block; width: 100%; height: auto; }
    figcaption { padding: 8px 12px; font-size: 12px; color: var(--muted); background: var(--panel); border-top: 1px solid var(--border); }
    .img-placeholder { height: 220px; display: grid; place-items: center; color: var(--muted); background: var(--hover); }
  `,
})
export class ExperimentsPage {
  private readonly api = inject(ApiService);

  protected readonly tables = signal<MetricTable[]>([]);
  /** chart path → object URL (or 'error'). */
  protected readonly images = signal<Record<string, string>>({});
  protected readonly loaded = signal(false);
  protected readonly error = signal<string | null>(null);
  private objectUrls: string[] = [];

  constructor() {
    inject(DestroyRef).onDestroy(() => this.revokeAll());
    this.load();
  }

  protected load(): void {
    this.api.experiments().subscribe({
      next: (res) => {
        const tables = (res?.tags ?? []).map(pivot);
        this.tables.set(tables);
        this.loaded.set(true);
        this.error.set(null);
        this.revokeAll();
        this.images.set({});
        for (const path of new Set(tables.flatMap((t) => t.charts))) {
          this.fetchChart(path);
        }
      },
      error: (e) => {
        this.loaded.set(true);
        this.error.set(errorText(e));
      },
    });
  }

  private fetchChart(path: string): void {
    this.api.chartBlob(path).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        this.objectUrls.push(url);
        this.images.update((m) => ({ ...m, [path]: url }));
      },
      error: () => this.images.update((m) => ({ ...m, [path]: 'error' })),
    });
  }

  private revokeAll(): void {
    for (const u of this.objectUrls) URL.revokeObjectURL(u);
    this.objectUrls = [];
  }

  protected fmt(v: number): string {
    return fmtNum(v);
  }

  protected caption(path: string): string {
    const file = path.split('/').pop() ?? path;
    return file.replace(/\.[a-z0-9]+$/i, '').replace(/[_-]+/g, ' ');
  }
}
