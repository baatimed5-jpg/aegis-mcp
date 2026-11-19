import { Component, input } from '@angular/core';

@Component({
  selector: 'app-kpi-card',
  template: `
    <div class="kpi card" [class]="'kpi card ' + tone()">
      <span class="label">{{ label() }}</span>
      <strong class="value">{{ value() }}</strong>
      @if (hint()) {
        <span class="hint">{{ hint() }}</span>
      }
    </div>
  `,
  styles: `
    .kpi { padding: 16px 18px; display: flex; flex-direction: column; gap: 6px; border-top: 3px solid var(--border); }
    .kpi.ok { border-top-color: var(--ok); }
    .kpi.bad { border-top-color: var(--bad); }
    .kpi.info { border-top-color: var(--info); }
    .kpi.warn { border-top-color: var(--warn); }
    .kpi.accent { border-top-color: var(--accent); }
    .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; font-weight: 600; }
    .value { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
    .hint { font-size: 12px; color: var(--text-2); }
  `,
})
export class KpiCard {
  readonly label = input.required<string>();
  readonly value = input.required<string>();
  readonly hint = input<string | null>(null);
  readonly tone = input<'ok' | 'bad' | 'info' | 'warn' | 'accent' | 'muted'>('muted');
}
