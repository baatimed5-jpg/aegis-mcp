import { KeyValuePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { forkJoin } from 'rxjs';
import { ApiService } from '../../core/api.service';
import { Pipeline, Policies } from '../../core/models';
import { errorText } from '../../core/util';

@Component({
  selector: 'app-policies',
  imports: [KeyValuePipe],
  template: `
    <header class="page-head">
      <div>
        <h1>Policies</h1>
        <p>Who may call what, and the order the gateway runs its middlewares.</p>
      </div>
      <button class="btn ghost" type="button" (click)="load()">Reload</button>
    </header>

    @if (error()) {
      <div class="alert bad">{{ error() }}</div>
    }

    @if (pipeline(); as p) {
      <section class="card pad">
        <h2>Pipeline</h2>
        <div class="flow-row">
          <span class="flow-label">Request</span>
          <div class="flow">
            <span class="node endpoint">agent</span>
            @for (m of p.request; track $index) {
              <span class="arrow" aria-hidden="true">→</span>
              <span class="node">{{ m }}</span>
            }
            <span class="arrow" aria-hidden="true">→</span>
            <span class="node endpoint">MCP server</span>
          </div>
        </div>
        <div class="flow-row">
          <span class="flow-label">Response</span>
          <div class="flow">
            <span class="node endpoint">MCP server</span>
            @for (m of p.response; track $index) {
              <span class="arrow" aria-hidden="true">→</span>
              <span class="node">{{ m }}</span>
            }
            <span class="arrow" aria-hidden="true">→</span>
            <span class="node endpoint">agent</span>
          </div>
        </div>
        <div class="facts">
          <div>
            <span class="muted small">Injection detector</span>
            <span class="chip accent">{{ p.detector }}</span>
          </div>
          <div>
            <span class="muted small">Downstream servers</span>
            <div class="chips">
              @for (s of p.servers; track s) {
                <span class="chip mono">{{ s }}</span>
              } @empty {
                <span class="muted">none</span>
              }
            </div>
          </div>
        </div>
      </section>
    }

    <h2 class="section-title">Agents</h2>
    <section class="agents">
      @for (entry of policies()?.agents ?? {} | keyvalue; track entry.key) {
        <article class="card pad agent">
          <h3>{{ entry.key }}</h3>
          <div class="group">
            <span class="muted small">Allowed tools ({{ entry.value.allow.length }})</span>
            <div class="chips">
              @for (t of entry.value.allow; track t) {
                <span class="chip ok mono">{{ t }}</span>
              } @empty {
                <span class="muted">none (deny all)</span>
              }
            </div>
          </div>
          <div class="group">
            <span class="muted small">Require human approval ({{ entry.value.require_approval.length }})</span>
            <div class="chips">
              @for (t of entry.value.require_approval; track t) {
                <span class="chip warn mono">{{ t }}</span>
              } @empty {
                <span class="muted">none</span>
              }
            </div>
          </div>
        </article>
      } @empty {
        <div class="card pad muted">{{ loaded() ? 'No agent policies defined.' : 'Loading…' }}</div>
      }
    </section>
  `,
  styles: `
    .pad { padding: 18px; }
    .flow-row { display: grid; grid-template-columns: 90px 1fr; gap: 12px; align-items: center; margin: 12px 0; }
    .flow-label { font-size: 12px; font-weight: 600; text-transform: uppercase; color: var(--muted); letter-spacing: .5px; }
    .flow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .node {
      padding: 6px 12px; border-radius: 8px; background: var(--accent-soft); color: var(--accent);
      font-weight: 600; font-size: 13px; border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    }
    .node.endpoint { background: var(--hover); color: var(--text-2); border-color: var(--border); font-weight: 500; }
    .arrow { color: var(--muted); }
    .facts { display: flex; gap: 32px; flex-wrap: wrap; margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border); }
    .facts > div { display: flex; flex-direction: column; gap: 6px; }
    .section-title { margin: 26px 0 12px; }
    .agents { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
    .agent h3 { margin: 0 0 12px; font-size: 16px; }
    .group { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    @media (max-width: 600px) { .flow-row { grid-template-columns: 1fr; } }
  `,
})
export class PoliciesPage {
  private readonly api = inject(ApiService);

  protected readonly policies = signal<Policies | null>(null);
  protected readonly pipeline = signal<Pipeline | null>(null);
  protected readonly loaded = signal(false);
  protected readonly error = signal<string | null>(null);

  constructor() {
    this.load();
  }

  protected load(): void {
    forkJoin({ policies: this.api.policies(), pipeline: this.api.pipeline() }).subscribe({
      next: ({ policies, pipeline }) => {
        this.policies.set(policies);
        this.pipeline.set(pipeline);
        this.loaded.set(true);
        this.error.set(null);
      },
      error: (e) => {
        this.loaded.set(true);
        this.error.set(errorText(e));
      },
    });
  }
}
