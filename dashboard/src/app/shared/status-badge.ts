import { Component, computed, input } from '@angular/core';
import { statusTone } from '../core/util';

@Component({
  selector: 'app-status-badge',
  template: `<span class="badge" [class]="'badge ' + tone()">{{ status() }}</span>`,
})
export class StatusBadge {
  readonly status = input.required<string>();
  protected readonly tone = computed(() => statusTone(this.status()));
}
