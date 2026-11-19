import { Component, computed, input } from '@angular/core';

export interface Slice {
  label: string;
  value: number;
  color: string;
}

interface Arc extends Slice {
  dash: string;
  offset: number;
  pct: number;
}

const R = 42;
const C = 2 * Math.PI * R;

/** Hand-made SVG donut with legend (no chart library). */
@Component({
  selector: 'app-donut-chart',
  template: `
    <div class="donut">
      <svg viewBox="0 0 120 120" role="img" [attr.aria-label]="ariaLabel()">
        <circle cx="60" cy="60" [attr.r]="r" fill="none" stroke="var(--hover)" stroke-width="14" />
        @for (a of arcs(); track a.label) {
          <circle
            cx="60" cy="60" [attr.r]="r" fill="none" [attr.stroke]="a.color" stroke-width="14"
            [attr.stroke-dasharray]="a.dash" [attr.stroke-dashoffset]="a.offset"
            transform="rotate(-90 60 60)">
            <title>{{ a.label }}: {{ a.value }}</title>
          </circle>
        }
        <text x="60" y="58" text-anchor="middle" class="total">{{ total() }}</text>
        <text x="60" y="74" text-anchor="middle" class="cap">{{ caption() }}</text>
      </svg>
      <ul>
        @for (a of arcs(); track a.label) {
          <li>
            <i [style.background]="a.color"></i>
            <span class="lbl">{{ a.label }}</span>
            <span class="val">{{ a.value }}</span>
            <span class="pct">{{ a.pct.toFixed(0) }}%</span>
          </li>
        } @empty {
          <li class="empty">No data yet</li>
        }
      </ul>
    </div>
  `,
  styles: `
    .donut { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
    svg { width: 150px; height: 150px; flex: none; }
    .total { font-size: 20px; font-weight: 700; fill: var(--text); }
    .cap { font-size: 9px; fill: var(--muted); text-transform: uppercase; letter-spacing: .6px; }
    ul { list-style: none; margin: 0; padding: 0; flex: 1; min-width: 150px; display: grid; gap: 6px; }
    li { display: grid; grid-template-columns: 10px 1fr auto 40px; gap: 8px; align-items: center; font-size: 13px; }
    li i { width: 10px; height: 10px; border-radius: 3px; }
    .lbl { text-transform: capitalize; }
    .val { font-variant-numeric: tabular-nums; font-weight: 600; }
    .pct { color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
    .empty { display: block; color: var(--muted); }
  `,
})
export class DonutChart {
  readonly slices = input<Slice[]>([]);
  readonly caption = input('calls');
  protected readonly r = R;

  protected readonly total = computed(() => this.slices().reduce((s, x) => s + x.value, 0));

  protected readonly arcs = computed<Arc[]>(() => {
    const total = this.total();
    if (!total) return [];
    let acc = 0;
    return this.slices()
      .filter((s) => s.value > 0)
      .map((s) => {
        const len = (s.value / total) * C;
        const arc: Arc = { ...s, dash: `${len} ${C - len}`, offset: -acc, pct: (s.value / total) * 100 };
        acc += len;
        return arc;
      });
  });

  protected readonly ariaLabel = computed(() =>
    this.arcs().map((a) => `${a.label} ${a.value}`).join(', ') || 'no data',
  );
}
