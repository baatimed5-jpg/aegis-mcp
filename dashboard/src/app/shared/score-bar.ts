import { Component, computed, input } from '@angular/core';

/** Horizontal 0..1 score bar, green → orange → red. */
@Component({
  selector: 'app-score-bar',
  template: `
    <div class="score" [title]="'score ' + label()">
      <div class="track"><div class="fill" [style.width.%]="pct()" [style.background]="color()"></div></div>
      <span>{{ label() }}</span>
    </div>
  `,
  styles: `
    .score { display: flex; align-items: center; gap: 8px; min-width: 120px; }
    .track { flex: 1; height: 6px; background: var(--hover); border-radius: 4px; overflow: hidden; }
    .fill { height: 100%; border-radius: 4px; }
    span { font-variant-numeric: tabular-nums; font-size: 12px; color: var(--text-2); width: 34px; text-align: right; }
  `,
})
export class ScoreBar {
  readonly value = input<number | null>(null);
  protected readonly clamped = computed(() => Math.max(0, Math.min(1, this.value() ?? 0)));
  protected readonly pct = computed(() => this.clamped() * 100);
  protected readonly label = computed(() => (this.value() === null ? '–' : this.clamped().toFixed(2)));
  protected readonly color = computed(() => {
    const v = this.clamped();
    return v >= 0.75 ? 'var(--bad)' : v >= 0.4 ? 'var(--warn)' : 'var(--ok)';
  });
}
