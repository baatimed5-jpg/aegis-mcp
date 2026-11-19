import { DatePipe, JsonPipe } from '@angular/common';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { Approval, Decision } from '../../core/models';
import { errorText, poll } from '../../core/util';

interface Toast {
  text: string;
  tone: 'ok' | 'bad';
}

@Component({
  selector: 'app-approvals',
  imports: [DatePipe, JsonPipe],
  template: `
    <header class="page-head">
      <div>
        <h1>Approvals</h1>
        <p>Sensitive calls (e.g. money transfers) wait here for a human decision. Refreshes every 3 s.</p>
      </div>
      <span class="pill" [class.attention]="items().length > 0">{{ items().length }} pending</span>
    </header>

    @if (error()) {
      <div class="alert bad">{{ error() }}</div>
    }
    @if (toast(); as t) {
      <div class="alert" [class]="'alert ' + t.tone">{{ t.text }}</div>
    }

    <section class="queue">
      @for (a of items(); track a.id) {
        <article class="card item">
          <div class="meta">
            <div>
              <span class="mono tool">{{ a.tool }}</span>
              <span class="muted small">requested by <strong>{{ a.agent_id }}</strong> · {{ a.created_at | date: 'MMM d, HH:mm:ss' }}</span>
            </div>
            <span class="badge info">{{ a.status }}</span>
          </div>
          <pre class="args">{{ a.arguments | json }}</pre>
          <div class="actions">
            <span class="muted small mono">id {{ a.id }}</span>
            <button class="btn danger" type="button" [disabled]="busy().has(a.id)" (click)="decide(a, 'deny')">Deny</button>
            <button class="btn success" type="button" [disabled]="busy().has(a.id)" (click)="decide(a, 'approve')">Approve</button>
          </div>
        </article>
      } @empty {
        <div class="card empty-card">
          {{ loaded() ? 'Nothing waiting for approval.' : 'Loading…' }}
        </div>
      }
    </section>
  `,
  styles: `
    .pill { padding: 6px 12px; border-radius: 999px; background: var(--hover); font-weight: 600; font-size: 13px; }
    .pill.attention { background: color-mix(in srgb, var(--warn) 20%, transparent); color: var(--warn); }
    .queue { display: grid; gap: 14px; }
    .item { padding: 16px 18px; display: grid; gap: 12px; border-left: 3px solid var(--warn); }
    .meta { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
    .meta > div { display: flex; flex-direction: column; gap: 4px; }
    .tool { font-size: 15px; font-weight: 600; }
    .args {
      margin: 0; padding: 12px; background: var(--hover); border-radius: 8px; font-size: 12.5px;
      max-height: 240px; overflow: auto; white-space: pre-wrap; word-break: break-word;
    }
    .actions { display: flex; gap: 10px; justify-content: flex-end; align-items: center; }
    .actions .small { margin-right: auto; }
    .empty-card { padding: 36px; text-align: center; color: var(--muted); }
  `,
})
export class ApprovalsPage {
  private readonly api = inject(ApiService);

  protected readonly items = signal<Approval[]>([]);
  protected readonly loaded = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly toast = signal<Toast | null>(null);
  protected readonly busy = signal<ReadonlySet<string>>(new Set());
  private toastTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    const destroyRef = inject(DestroyRef);
    poll(destroyRef, 3000, () => this.load());
    destroyRef.onDestroy(() => this.toastTimer && clearTimeout(this.toastTimer));
  }

  private load(): void {
    this.api.approvals().subscribe({
      next: (rows) => {
        this.items.set((rows ?? []).filter((r) => r.status === 'pending'));
        this.loaded.set(true);
        this.error.set(null);
      },
      error: (e) => this.error.set(errorText(e)),
    });
  }

  protected decide(a: Approval, decision: Decision): void {
    this.setBusy(a.id, true);
    this.api.decide(a.id, decision).subscribe({
      next: (res) => {
        this.setBusy(a.id, false);
        this.items.update((cur) => cur.filter((x) => x.id !== a.id));
        this.showToast({
          text: `${a.tool} ${res?.status ?? (decision === 'approve' ? 'approved' : 'denied')}.`,
          tone: decision === 'approve' ? 'ok' : 'bad',
        });
        this.load();
      },
      error: (e) => {
        this.setBusy(a.id, false);
        this.showToast({ text: `Could not ${decision} ${a.tool}: ${errorText(e)}`, tone: 'bad' });
        this.load();
      },
    });
  }

  private setBusy(id: string, on: boolean): void {
    this.busy.update((cur) => {
      const next = new Set(cur);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  private showToast(t: Toast): void {
    this.toast.set(t);
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => this.toast.set(null), 4000);
  }
}
